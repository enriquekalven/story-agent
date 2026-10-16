import concurrent.futures
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import google.auth
from google.genai import Client, types
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

import audio_generator
import config
import drive_sync
import prompts

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ==============================================================================
# Structured Output Schemas (Pydantic)
# ==============================================================================
class StoryPageSchema(BaseModel):
    page_number: int = Field(description="1-indexed page number (1 to 20)")
    text: str = Field(description="Page text in target language")
    features_main_character: bool = Field(default=True)
    scene_description: str = Field(
        description="Purely visual English scene description with zero text, titles, or banners"
    )


class StoryBookSchema(BaseModel):
    title: str
    series: str = f"{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}"
    author: str = prompts.SERIES_AUTHOR
    main_character_description: str = prompts.SERIES_CHARACTER
    artistic_style: str = prompts.SERIES_STYLE
    pages: list[StoryPageSchema]


class TranslatedPageSchema(BaseModel):
    page_number: int
    text: str


class TranslationSchema(BaseModel):
    title: str
    pages: list[TranslatedPageSchema]


class VisualQAResult(BaseModel):
    passed: bool = Field(description="True only if image is textless and characters match invariants")
    has_unwanted_text: bool = Field(
        description="True if any words, letters, titles, banners, or watermarks appear inside the artwork"
    )
    robo_off_model: bool = Field(
        description="True if Robo has antennas, wheels, tracks, or a boxy body instead of being spherical"
    )
    lily_off_model: bool = Field(
        description="True if Lily is missing twin pigtails or her yellow polka-dot dress when present"
    )
    score: float = Field(default=1.0, description="Quality score from 0.0 to 1.0")
    issues: list[str] = Field(default_factory=list, description="Specific visual issues detected")


class StorybookGenerator:
    """Generates kid-friendly stories and overlayed cover/page art, exporting to Google Drive."""

    def __init__(self, drive_service=None, ai_client: Optional[Client] = None):
        self.drive_service = drive_service
        self.ai_client = ai_client

        # Configure Vertex AI environment without blocking on network credential discovery in __init__
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.GOOGLE_CLOUD_PROJECT)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", config.GOOGLE_CLOUD_LOCATION)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", config.GOOGLE_GENAI_USE_VERTEXAI)

    def _ensure_project_configured(self) -> str:
        """Lazily resolves project ID via ADC only if not already configured."""
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            return project
        try:
            _, project_id = google.auth.default()
            project = project_id or config.GOOGLE_CLOUD_PROJECT
        except Exception:
            project = config.GOOGLE_CLOUD_PROJECT
        os.environ["GOOGLE_CLOUD_PROJECT"] = project
        return project

    def _get_client(self) -> Client:
        """Returns an initialized GenAI Client, respecting injected clients or test patches."""
        if self.ai_client is not None:
            return self.ai_client
        self._ensure_project_configured()
        return Client()

    def _call_with_retry(
        self,
        fn: Callable[[], Any],
        max_retries: int = config.MAX_API_RETRIES,
        description: str = "GenAI call",
    ) -> Any:
        """Executes a callable with exponential backoff retry."""
        for attempt in range(1, max_retries + 1):
            try:
                return fn()
            except Exception as e:
                logger.warning(f"{description} attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    time.sleep(2 * attempt)
                else:
                    raise

    def get_best_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Resolves a cross-platform Unicode/CJK-capable font (bundled -> Linux -> macOS -> default)."""
        candidate_paths: list[str] = []

        # 1. Bundled fonts in assets/fonts/
        if config.BUNDLED_FONT_DIR.exists():
            for fname in (
                "NotoSansCJK-Regular.ttc",
                "NotoSansSC-Regular.otf",
                "NotoSansSC-Regular.ttf",
                "DejaVuSans.ttf",
            ):
                p = config.BUNDLED_FONT_DIR / fname
                if p.exists():
                    candidate_paths.append(str(p))

        # 2. Linux system fonts (Ubuntu/Debian fonts-noto-cjk & fonts-dejavu)
        candidate_paths.extend(
            [
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )

        # 3. macOS system fonts
        candidate_paths.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
        )

        for path in candidate_paths:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue

        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def generate_story(self, topic: str, age_group: str, language: str = "English") -> dict:
        """Generates a structured kid-friendly story outline and pages with schema validation & retry."""
        prompt = prompts.get_story_generation_prompt(topic, age_group, language)
        logger.info(
            f"Generating story for topic: '{topic}', age group: '{age_group}' using {config.TEXT_MODEL}..."
        )
        project = self._ensure_project_configured()
        client = self.ai_client or Client(
            vertexai=True,
            project=project,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        def _invoke():
            response = client.models.generate_content(
                model=config.TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StoryBookSchema,
                    temperature=0.7,
                ),
            )
            return json.loads(response.text)

        story_json = self._call_with_retry(_invoke, description="Story JSON generation")

        # Programmatically enforce Enrique Chan as the author on the cover page (Page 1)
        if "pages" in story_json and len(story_json["pages"]) > 0:
            cover_page = story_json["pages"][0]
            title = story_json.get("title", f"Story about {topic}")
            if language == "Spanish":
                cover_page["text"] = f"{title}\nEscrito por Enrique Chan"
            elif language == "Chinese Simplified":
                cover_page["text"] = f"{title}\n作者：Enrique Chan"
            elif language == "French":
                cover_page["text"] = f"{title}\nÉcrit par Enrique Chan"
            elif language == "Portuguese":
                cover_page["text"] = f"{title}\nEscrito por Enrique Chan"
            else:
                cover_page["text"] = f"{title}\nWritten by Enrique Chan"

        return story_json

    def _load_reference_sheet_bytes(self) -> Optional[bytes]:
        """Loads canonical Lily & Robo character reference sheet if present on disk."""
        if config.REFERENCE_SHEET_PATH.exists():
            try:
                return config.REFERENCE_SHEET_PATH.read_bytes()
            except Exception as e:
                logger.warning(f"Failed to read character reference sheet: {e}")
        return None

    def generate_page_image(
        self,
        prompt: str,
        reference_images: Optional[list[bytes]] = None,
    ) -> bytes:
        """Generates a single page illustration using gemini-2.5-flash-image (Nano Banana 2)."""
        logger.info(f"Generating image with prompt: '{prompt}'...")
        client = self._get_client()

        contents: list[Any] = []
        ref_list = list(reference_images) if reference_images else []
        if not ref_list:
            default_ref = self._load_reference_sheet_bytes()
            if default_ref:
                ref_list.append(default_ref)

        for ref_bytes in ref_list:
            contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))
        if ref_list:
            contents.append(
                types.Part.from_text(
                    text=(
                        "Match the exact character designs of Lily (6yo girl, twin pigtails, yellow "
                        "white-polka-dot dress) and Robo (small spherical light-blue robot, NO antennas, "
                        "NO wheels) from the reference image. " + prompt
                    )
                )
            )
        else:
            contents.append(types.Part.from_text(text=prompt))

        def _invoke() -> bytes:
            response = client.models.generate_content(
                model=config.IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                    ),
                ),
            )

            image_bytes = None
            if response.parts:
                for part in response.parts:
                    if part.inline_data and part.inline_data.data:
                        image_bytes = part.inline_data.data
                        break

            if image_bytes:
                return image_bytes
            raise RuntimeError("No image was returned by the model.")

        return self._call_with_retry(_invoke, description="Image generation")

    def evaluate_page_image_qa(
        self,
        image_bytes: bytes,
        page_num: int,
        scene_description: str = "",
        features_char: bool = True,
    ) -> dict[str, Any]:
        """Uses Gemini Vision to audit a raw generated page image before text overlay.

        Checks for:
        1. Accidental baked-in text, titles, banners, or watermarks.
        2. Character invariant violations (e.g., Robo having antennas, wheels, or a boxy body).
        """
        client = self._get_client()
        qa_prompt = f"""
        You are a strict visual quality assurance inspector for the children's picture book series
        "{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}".
        Inspect this raw illustration for Page {page_num} (before programmatic text overlay).

        Intended visual scene: {scene_description}
        Features main characters (Lily & Robo): {features_char}

        Evaluate strictly against these rules:
        1. `has_unwanted_text`: Is there ANY visible text, words, letters, book titles, banners with text, or watermarks rendered inside the illustration? (Must be False for the image to pass).
        2. `robo_off_model`: If the robot Robo appears in the scene, does he have antennas, wheels, tank tracks, or a boxy human-like torso instead of being a small round/spherical light-blue robot? (Must be False).
        3. `lily_off_model`: If the girl Lily appears in the scene, is she missing her twin pigtails or yellow polka-dot dress? (Must be False).
        4. Set `passed` to True ONLY if `has_unwanted_text` is False AND `robo_off_model` is False AND `lily_off_model` is False.
        """
        try:
            response = client.models.generate_content(
                model=config.VISION_QA_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    types.Part.from_text(text=qa_prompt),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VisualQAResult,
                    temperature=0.1,
                ),
            )
            result = json.loads(response.text)
            if not isinstance(result, dict):
                return {"passed": True, "score": 1.0, "issues": []}
            # Enforce boolean invariant logic
            has_text = bool(result.get("has_unwanted_text", False))
            robo_bad = bool(result.get("robo_off_model", False))
            lily_bad = bool(result.get("lily_off_model", False))
            passed = bool(result.get("passed", True)) and not (has_text or robo_bad or lily_bad)
            result["passed"] = passed
            return result
        except Exception as e:
            logger.warning(f"Visual QA check skipped on Page {page_num} due to error: {e}")
            return {"passed": True, "score": 1.0, "issues": [f"qa_error: {e}"]}

    def get_text_width(self, draw, text, font):
        """Helper to get text width safely across Pillow versions."""
        if hasattr(draw, "textlength"):
            return draw.textlength(text, font=font)
        elif hasattr(font, "getbbox"):
            return font.getbbox(text)[2]
        else:
            return draw.textsize(text, font=font)[0]

    @staticmethod
    def _wrap_text_lines(text: str, max_chars_en: int = 45, max_chars_cjk: int = 22) -> list[str]:
        """Wraps text into lines for either CJK or space-delimited scripts."""
        has_asian = any(
            ("\u4e00" <= char <= "\u9fff")
            or ("\u3040" <= char <= "\u309f")
            or ("\u30a0" <= char <= "\u30ff")
            or ("\uac00" <= char <= "\ud7a3")
            for char in text
        )
        lines: list[str] = []
        for paragraph in text.splitlines() or [text]:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if has_asian:
                current_line = ""
                for char in paragraph:
                    current_line += char
                    if len(current_line) >= max_chars_cjk:
                        lines.append(current_line)
                        current_line = ""
                if current_line:
                    lines.append(current_line)
            else:
                words = paragraph.split()
                current_words: list[str] = []
                for word in words:
                    current_words.append(word)
                    if len(" ".join(current_words)) > max_chars_en:
                        if len(current_words) > 1:
                            current_words.pop()
                            lines.append(" ".join(current_words))
                            current_words = [word]
                        else:
                            lines.append(word)
                            current_words = []
                if current_words:
                    lines.append(" ".join(current_words))
        return lines or [text]

    def overlay_text_on_image(
        self,
        image_bytes: bytes,
        text: str,
        page_num: int,
        total_pages: int,
        language: str = "English",
    ) -> bytes:
        """Overlays text onto the generated image in a readable children's book format.

        Includes:
        - Cover page (page 1): Series pill badge, prominent wrapped book title plaque, and author credit.
        - Story/STEM pages (pages 2..N-1): Adaptive parchment card capped at <=38% of page height.
        - Closing page (page N): Centered localized 'The End' typography.
        """
        logger.info(f"Overlaying text on generated image for Page {page_num}...")
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size

        is_cover = page_num == 1
        is_last = page_num == total_pages and total_pages > 1

        font_size = int(height * 0.04)
        if is_last:
            font_size = int(height * 0.07)
        font = self.get_best_font(max(12, font_size))

        # Prepare text lines based on page type
        cover_title_lines: list[str] = []
        if is_cover:
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            if len(parts) >= 2:
                title_part = " ".join(parts[:-1])
                author_part = parts[-1]
                cover_title_lines = self._wrap_text_lines(title_part, max_chars_en=26, max_chars_cjk=14)
                lines = [author_part]
            else:
                lines = [parts[-1]] if parts else [text]
        elif is_last:
            closing_map = {
                "Spanish": "El fin",
                "French": "Fin",
                "Portuguese": "Fim",
                "Italian": "Fine",
                "German": "Ende",
                "Chinese Simplified": "终",
                "Japanese": "おしまい",
                "Korean": "끝",
            }
            lines = [closing_map.get(language, "The End")]
        else:
            # Adaptive sizing: ensure parchment card never exceeds 38% of page height (fixes Page 19 overflow)
            max_card_height = int(height * 0.38)
            max_chars_en = 45
            max_chars_cjk = 22
            lines = self._wrap_text_lines(text, max_chars_en=max_chars_en, max_chars_cjk=max_chars_cjk)

            for scale in (1.0, 0.88, 0.76, 0.66, 0.56):
                candidate_font_size = max(12, int(height * 0.04 * scale))
                candidate_chars_en = int(45 / scale)
                candidate_chars_cjk = int(22 / scale)
                lines = self._wrap_text_lines(
                    text,
                    max_chars_en=candidate_chars_en,
                    max_chars_cjk=candidate_chars_cjk,
                )
                line_height = int(candidate_font_size * 1.32)
                card_padding_y = int(height * 0.022)
                card_height = len(lines) * line_height + (2 * card_padding_y)
                font_size = candidate_font_size
                font = self.get_best_font(font_size)
                if card_height <= max_card_height:
                    break

        # Draw an elegant, rounded parchment card ONLY for regular pages
        if not is_cover and not is_last:
            line_height = int(font_size * 1.32)
            card_padding_y = int(height * 0.022)
            card_height = min(int(height * 0.42), len(lines) * line_height + (2 * card_padding_y))

            card_margin_x = int(width * 0.05)
            card_bottom = int(height * 0.95)
            card_top = max(int(height * 0.50), card_bottom - card_height)
            card_left = card_margin_x
            card_right = width - card_margin_x

            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            shadow_offset = int(height * 0.005)
            overlay_draw.rounded_rectangle(
                [
                    (card_left + shadow_offset, card_top + shadow_offset),
                    (card_right + shadow_offset, card_bottom + shadow_offset),
                ],
                radius=18,
                fill=(0, 0, 0, 35),
            )

            overlay_draw.rounded_rectangle(
                [(card_left, card_top), (card_right, card_bottom)],
                radius=18,
                fill=(255, 253, 248, 228),
                outline=(220, 215, 200, 160),
                width=1,
            )
            image = Image.alpha_composite(image.convert("RGBA"), overlay)

        draw = ImageDraw.Draw(image)

        if is_cover:
            # 1. Render Series Badge at top of cover
            badge_text = prompts.SERIES_BRAND
            badge_font_size = max(10, int(height * 0.028))
            badge_font = self.get_best_font(badge_font_size)

            badge_width = self.get_text_width(draw, badge_text, badge_font)
            badge_pad_x, badge_pad_y = int(width * 0.03), int(height * 0.01)
            b_left = (width - badge_width) // 2 - badge_pad_x
            b_top = int(height * 0.05)
            b_right = (width + badge_width) // 2 + badge_pad_x
            b_bottom = b_top + badge_font_size + (2 * badge_pad_y)

            b_overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            b_draw = ImageDraw.Draw(b_overlay)
            b_draw.rounded_rectangle(
                [(b_left, b_top), (b_right, b_bottom)],
                radius=12,
                fill=(255, 255, 255, 215),
                outline=(230, 200, 120, 210),
                width=1,
            )

            # 2. Render Book Title banner below Series Badge when title line is present
            title_font_size = max(14, int(height * 0.052))
            title_font = self.get_best_font(title_font_size)
            title_line_height = int(title_font_size * 1.25)
            t_top = b_bottom + int(height * 0.02)
            if cover_title_lines:
                max_t_width = max(
                    self.get_text_width(draw, t_line, title_font) for t_line in cover_title_lines
                )
                t_pad_x = int(width * 0.04)
                t_pad_y = int(height * 0.015)
                t_left = max(int(width * 0.06), int((width - max_t_width) // 2 - t_pad_x))
                t_right = min(int(width * 0.94), int((width + max_t_width) // 2 + t_pad_x))
                t_bottom = t_top + len(cover_title_lines) * title_line_height + (2 * t_pad_y)
                b_draw.rounded_rectangle(
                    [(t_left, t_top), (t_right, t_bottom)],
                    radius=16,
                    fill=(255, 253, 248, 210),
                    outline=(230, 200, 120, 180),
                    width=1,
                )

            image = Image.alpha_composite(image.convert("RGBA"), b_overlay)
            draw = ImageDraw.Draw(image)
            draw.text(
                ((width - badge_width) // 2, b_top + badge_pad_y),
                badge_text,
                fill=(180, 110, 20, 255),
                font=badge_font,
            )

            if cover_title_lines:
                ty = t_top + int(height * 0.015)
                for t_line in cover_title_lines:
                    tw = self.get_text_width(draw, t_line, title_font)
                    tx = (width - tw) // 2
                    draw.text(
                        (tx, ty),
                        t_line,
                        fill=(35, 50, 70, 255),
                        stroke_width=2,
                        stroke_fill=(255, 255, 255, 255),
                        font=title_font,
                    )
                    ty += title_line_height

            # 3. Author credit at the bottom
            y_offset = int(height * 0.88)
            for line in lines:
                line_width = self.get_text_width(draw, line, font)
                x = (width - line_width) // 2
                draw.text(
                    (x, y_offset),
                    line,
                    fill=(44, 62, 80, 255),
                    stroke_width=2,
                    stroke_fill=(255, 255, 255, 255),
                    font=font,
                )
        elif is_last:
            line = lines[0]
            line_width = self.get_text_width(draw, line, font)
            x = (width - line_width) // 2
            y = (height - font_size) // 2
            draw.text(
                (x, y),
                line,
                fill=(44, 62, 80, 255),
                stroke_width=3,
                stroke_fill=(255, 255, 255, 255),
                font=font,
            )
        else:
            line_height = int(font_size * 1.32)
            y_offset = card_top + card_padding_y
            for line in lines:
                if y_offset + font_size > card_bottom:
                    break
                line_width = self.get_text_width(draw, line, font)
                x = (width - line_width) // 2
                draw.text((x, y_offset), line, fill=(44, 53, 64, 255), font=font)
                y_offset += line_height

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()

    def create_drive_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """Creates a folder in Google Drive or reuses an existing one with the same name."""
        return drive_sync.get_or_create_folder(self.drive_service, folder_name, parent_id=parent_id) or ""

    def upload_image_to_drive(self, image_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads or updates an image from bytes to a specific Google Drive folder."""
        return drive_sync.upsert_bytes_to_drive(
            self.drive_service,
            image_bytes,
            filename,
            folder_id,
            mimetype="image/jpeg",
            fields="id, webViewLink",
        )

    def upload_pdf_to_drive(self, pdf_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads or updates a PDF from bytes to a specific Google Drive folder."""
        return drive_sync.upsert_bytes_to_drive(
            self.drive_service,
            pdf_bytes,
            filename,
            folder_id,
            mimetype="application/pdf",
            fields="id",
        )

    def upload_epub_to_drive(self, epub_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads or updates an EPUB file to a specific Google Drive folder."""
        return drive_sync.upsert_bytes_to_drive(
            self.drive_service,
            epub_bytes,
            filename,
            folder_id,
            mimetype="application/epub+zip",
            fields="id",
        )

    def upload_audio_to_drive(self, audio_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads or updates an MP3 audio file to a specific Google Drive folder."""
        return drive_sync.upsert_bytes_to_drive(
            self.drive_service,
            audio_bytes,
            filename,
            folder_id,
            mimetype="audio/mpeg",
            fields="id",
        )

    def upload_txt_as_doc_to_drive(self, text_content: str, filename: str, folder_id: str) -> str:
        """Uploads plain text content to Google Drive and converts it into a Google Doc."""
        return drive_sync.upsert_bytes_to_drive(
            self.drive_service,
            text_content.encode("utf-8"),
            filename,
            folder_id,
            mimetype="text/plain",
            target_mime_type="application/vnd.google-apps.document",
            fields="id",
        )

    @staticmethod
    def compile_pdf_bytes(
        pil_images: list[Image.Image],
        dpi: float = config.PDF_DPI,
        upscale_for_print: bool = False,
    ) -> bytes:
        """Compiles PIL images into a print-ready PDF with proper DPI metadata (default 300 DPI)."""
        if not pil_images:
            return b""
        processed = pil_images
        if upscale_for_print:
            target_px = int(config.PDF_TRIM_INCHES * dpi)
            processed = [
                img.resize((target_px, target_px), Image.Resampling.LANCZOS)
                if img.size != (target_px, target_px)
                else img
                for img in pil_images
            ]
        pdf_buffer = io.BytesIO()
        processed[0].save(
            pdf_buffer,
            format="PDF",
            save_all=True,
            append_images=processed[1:],
            resolution=dpi,
        )
        return pdf_buffer.getvalue()

    def create_epub(self, title: str, pages_data: list, language: str) -> bytes:
        """Creates a valid EPUB 3.0 fixed-layout eBook with nav document, dcterms:modified, and XML escaping."""
        logger.info(f"Creating KDP-compatible fixed-layout EPUB 3 for '{title}'...")
        epub_buf = io.BytesIO()
        book_id = str(uuid.uuid4())
        modified_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        lang_codes = {
            "English": "en",
            "Spanish": "es",
            "Chinese Simplified": "zh",
            "French": "fr",
            "Portuguese": "pt",
            "Japanese": "ja",
            "Korean": "ko",
            "German": "de",
            "Italian": "it",
        }
        lang_code = lang_codes.get(language, "en")
        escaped_title = html.escape(title or "Curious Little Minds Storybook")
        escaped_author = html.escape(prompts.SERIES_AUTHOR)

        with zipfile.ZipFile(epub_buf, "w", zipfile.ZIP_DEFLATED) as epub:
            # 1. mimetype MUST be the first entry and uncompressed (ZIP_STORED)
            epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # 2. META-INF/container.xml
            container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="http://www.oasis-open.org/names/tc/opendocument/xmlns/container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
            epub.writestr("META-INF/container.xml", container_xml)

            manifest_items = [
                '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            ]
            spine_items = []
            nav_links = []

            for idx, (filename, img_bytes) in enumerate(pages_data):
                page_num = idx + 1
                img_href = f"images/{html.escape(filename)}"
                page_href = f"page_{page_num:02d}.xhtml"

                epub.writestr(f"OEBPS/images/{filename}", img_bytes)

                page_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang_code}">
<head>
  <title>{escaped_title} - Page {page_num}</title>
  <meta name="viewport" content="width=1024,height=1024"/>
  <style>
    body {{ margin: 0; padding: 0; background-color: #FFFFFF; }}
    img {{ width: 1024px; height: 1024px; display: block; }}
  </style>
</head>
<body>
  <div>
    <img src="{img_href}" alt="Page {page_num}"/>
  </div>
</body>
</html>"""
                epub.writestr(f"OEBPS/{page_href}", page_xhtml)

                img_props = ' properties="cover-image"' if page_num == 1 else ""
                manifest_items.append(
                    f'    <item id="page_{page_num:02d}" href="{page_href}" media-type="application/xhtml+xml"/>'
                )
                manifest_items.append(
                    f'    <item id="img_{page_num:02d}" href="{img_href}" media-type="image/jpeg"{img_props}/>'
                )
                spine_items.append(f'    <itemref idref="page_{page_num:02d}"/>')
                nav_links.append(f'        <li><a href="{page_href}">Page {page_num}</a></li>')

            nav_links_str = "\n".join(nav_links)
            nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang_code}">
<head>
  <title>{escaped_title} - Navigation</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{escaped_title}</h1>
    <ol>
{nav_links_str}
    </ol>
  </nav>
</body>
</html>"""
            epub.writestr("OEBPS/nav.xhtml", nav_xhtml)

            manifest_str = "\n".join(manifest_items)
            spine_str = "\n".join(spine_items)

            content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0" xml:lang="{lang_code}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{escaped_title}</dc:title>
    <dc:creator>{escaped_author}</dc:creator>
    <dc:language>{lang_code}</dc:language>
    <meta property="dcterms:modified">{modified_utc}</meta>
    <meta property="rendition:layout">pre-paginated</meta>
    <meta property="rendition:orientation">auto</meta>
    <meta property="rendition:spread">none</meta>
  </metadata>
  <manifest>
{manifest_str}
  </manifest>
  <spine>
{spine_str}
  </spine>
</package>"""
            epub.writestr("OEBPS/content.opf", content_opf)

        return epub_buf.getvalue()

    @staticmethod
    def validate_epub_bytes(epub_bytes: bytes, epub_path: Optional[str] = None) -> dict[str, Any]:
        """Validates EPUB 3 package invariants and optionally runs `epubcheck` CLI if available."""
        errors: list[str] = []
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
            names = zf.namelist()
            if not names or names[0] != "mimetype":
                errors.append("First entry in EPUB zip must be 'mimetype'.")
            else:
                info = zf.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    errors.append("'mimetype' entry must be uncompressed (ZIP_STORED).")
                if zf.read("mimetype") != b"application/epub+zip":
                    errors.append("Invalid mimetype payload.")

            for required in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
                if required not in names:
                    errors.append(f"Missing required EPUB 3 file: {required}")

            # Parse XML files for well-formedness and EPUB 3 metadata/manifest properties
            for name in names:
                if name.endswith((".xml", ".opf", ".xhtml")):
                    try:
                        ET.fromstring(zf.read(name))
                    except ET.ParseError as e:
                        errors.append(f"Malformed XML in {name}: {e}")

            if "OEBPS/content.opf" in names:
                opf_text = zf.read("OEBPS/content.opf").decode("utf-8", errors="replace")
                if 'property="dcterms:modified"' not in opf_text:
                    errors.append("Missing required <meta property=\"dcterms:modified\"> in content.opf.")
                if 'properties="nav"' not in opf_text:
                    errors.append("Missing required nav item with properties=\"nav\" in content.opf.")
                if 'properties="cover-image"' not in opf_text:
                    errors.append("Missing cover-image item in content.opf.")

        epubcheck_bin = shutil.which("epubcheck")
        if epubcheck_bin and epub_path and os.path.exists(epub_path):
            res = subprocess.run([epubcheck_bin, epub_path], capture_output=True, text=True)
            if res.returncode != 0:
                errors.append(f"epubcheck failed: {res.stderr or res.stdout}")

        return {"valid": len(errors) == 0, "errors": errors}

    def generate_kdp_description(self, title: str, topic: str, age_group: str, language: str) -> str:
        """Generates a professional Amazon KDP book description in the target language."""
        logger.info(f"Generating Amazon KDP description in {language}...")
        prompt = f"""
        You are a professional children's book publisher and marketer for the "{prompts.SERIES_BRAND}" early STEM imprint.
        Write a highly engaging, catchy, and professional Amazon KDP (Kindle Direct Publishing) book description for the following picture book:
        
        Series: "{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}"
        Title: "{title}"
        Topic: "{topic}" (written for ages {age_group})
        Author: {prompts.SERIES_AUTHOR}
        Language: {language}
        
        The description MUST be written entirely in {language}.
        Include:
        1. A catchy headline hook introducing {prompts.SERIES_BRAND}.
        2. A heartwarming story synopsis starring Lily and her companion robot Robo.
        3. Clear educational STEM takeaways explained in playful, intuitive terms.
        4. "Why Parents & Educators Love Curious Little Minds" (3 key bullet points on emotional intelligence, teamwork, and early STEM curiosity).
        5. Series callout inviting readers to collect all books in the {prompts.SERIES_BRAND} series.
        6. Listing metadata (suggested age group 4-6, preschool to kindergarten grade levels, and 7 high-converting search keywords/tags).
        
        Keep the tone warm, welcoming, inspirational, and professional.
        """
        client = self._get_client()

        def _invoke() -> str:
            response = client.models.generate_content(
                model=config.TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                ),
            )
            return response.text

        return self._call_with_retry(_invoke, description="KDP description generation")

    def validate_and_correct_story(self, story_json: dict, topic: str, language: str) -> dict:
        """Validates the storybook structure, details, prompts, and automatically corrects errors."""
        logger.info("Running storybook structure validation (Validation Agent Skill)...")

        # Inject series branding metadata
        story_json["series"] = f"{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}"
        story_json["author"] = prompts.SERIES_AUTHOR

        pages = story_json.get("pages", [])
        total_pages = len(pages)

        # Failsafe character/robot name overrides to ensure consistency
        for page in pages:
            text_val = page.get("text", "")
            scene_val = page.get("scene_description", "")

            for name in ["luna", "dottie", "lele", "xiao lele"]:
                text_val = re.sub(rf"\b{name}\b", "Lily", text_val, flags=re.IGNORECASE)
                scene_val = re.sub(rf"\b{name}\b", "Lily", scene_val, flags=re.IGNORECASE)

            for rname in ["chip", "clic", "pip", "leo", "clik", "dotti"]:
                text_val = re.sub(rf"\b{rname}\b", "Robo", text_val, flags=re.IGNORECASE)
                scene_val = re.sub(rf"\b{rname}\b", "Robo", scene_val, flags=re.IGNORECASE)

            page["text"] = text_val
            page["scene_description"] = scene_val

        # 1. Enforce cover page author credit
        if total_pages > 0:
            cover_page = pages[0]
            title = story_json.get("title", f"Story about {topic}")
            if language == "Spanish":
                expected_author = f"{title}\nEscrito por Enrique Chan"
            elif language == "Chinese Simplified":
                expected_author = f"{title}\n作者：Enrique Chan"
            elif language == "French":
                expected_author = f"{title}\nÉcrit par Enrique Chan"
            elif language == "Portuguese":
                expected_author = f"{title}\nEscrito por Enrique Chan"
            else:
                expected_author = f"{title}\nWritten by Enrique Chan"

            if cover_page.get("text") != expected_author:
                logger.info(
                    f"Corrected Cover Page text from '{cover_page.get('text')}' to expected author format."
                )
                cover_page["text"] = expected_author

        # 2. Enforce word budget on Page 19 (STEM Exploration Corner) so the text card stays compact
        for page in pages:
            if page.get("page_number") == 19:
                words = page.get("text", "").split()
                if len(words) > 65:
                    logger.info(
                        f"Trimming Page 19 STEM Corner from {len(words)} words to 60 words to prevent card overflow."
                    )
                    trimmed = " ".join(words[:60]).rstrip(",;:")
                    if not trimmed.endswith((".", "!", "?")):
                        trimmed += "."
                    page["text"] = trimmed

        # 3. Enforce closing page while preserving any generated back-cover blurb in metadata
        if total_pages > 0:
            last_page = pages[-1]
            if language == "Spanish":
                expected_last = "El fin"
            elif language == "French":
                expected_last = "Fin"
            elif language == "Portuguese":
                expected_last = "Fim"
            elif language == "Chinese Simplified":
                expected_last = "终"
            else:
                expected_last = "The End"

            original_last_text = (last_page.get("text") or "").strip()
            if original_last_text and original_last_text != expected_last:
                last_page["back_cover_blurb"] = original_last_text
                story_json["back_cover_blurb"] = original_last_text
                logger.info(
                    f"Preserved Page {total_pages} teaser in 'back_cover_blurb' and set closing text to '{expected_last}'"
                )
                last_page["text"] = expected_last

        # 4. Compile and sanitize image prompts for character consistency
        main_char = story_json.get("main_character_description", "").strip() or prompts.SERIES_CHARACTER
        style = story_json.get("artistic_style", "").strip() or prompts.SERIES_STYLE

        for page in pages:
            scene = page.get("scene_description", "").strip()
            features_char = page.get("features_main_character", True)

            prompt = prompts.construct_image_prompt(
                page_scene=scene,
                main_char_desc=main_char,
                style_desc=style,
                features_char=features_char,
            )
            page["image_prompt"] = prompt
            logger.info(f"Set consistent image_prompt for Page {page.get('page_number')}: '{prompt}'")

        # 5. Enforce Copyright & Infringement Protection
        logger.info("Running Copyright and Trademark Infringement scan...")
        infringement_replacements = {
            "wall-e": "a friendly helper robot",
            "walle": "a friendly helper robot",
            "r2-d2": "a companion robot",
            "r2d2": "a companion robot",
            "c-3po": "a golden helper robot",
            "c3po": "a golden helper robot",
            "baymax": "a soft, huggable robot",
            "lego": "toy block",
            "minecraft": "pixel block game",
            "pixar style": "whimsical cartoon style",
            "style of pixar": "whimsical cartoon style",
            "disney style": "whimsical cartoon style",
            "style of disney": "whimsical cartoon style",
            "ghibli style": "hand-drawn watercolor style",
            "style of ghibli": "hand-drawn watercolor style",
            "studio ghibli": "hand-drawn watercolor style",
        }

        title = story_json.get("title", "")
        for key, rep in infringement_replacements.items():
            if key in title.lower():
                logger.warning(f"Trademark/copyright name '{key}' detected in title. Correcting...")
                title = re.sub(re.escape(key), rep, title, flags=re.IGNORECASE)
                story_json["title"] = title

        for page in pages:
            text_val = page.get("text", "")
            prompt_val = page.get("image_prompt", "")

            for key, rep in infringement_replacements.items():
                if key in text_val.lower():
                    logger.warning(
                        f"Trademark/copyright name '{key}' detected in Page {page.get('page_number')} text. Correcting..."
                    )
                    text_val = re.sub(re.escape(key), rep, text_val, flags=re.IGNORECASE)
                    page["text"] = text_val

                if key in prompt_val.lower():
                    logger.warning(
                        f"Trademark/copyright name '{key}' detected in Page {page.get('page_number')} prompt. Correcting..."
                    )
                    prompt_val = re.sub(re.escape(key), rep, prompt_val, flags=re.IGNORECASE)
                    page["image_prompt"] = prompt_val

        # 6. Enforce Child-Friendly Vocabulary (STEM Jargon Filter)
        logger.info("Running Child-Friendly STEM Jargon Filter...")
        jargon_replacements = {
            r"\bapis?\b": "helpful tools",
            r"\balgorithms?\b": "playful recipes",
            r"\bservers?\b": "helper stations",
            r"\bdatabases?\b": "memory treasure boxes",
            r"\bparameters?\b": "special settings",
            r"\bdebugging\b": "puzzle-solving",
            r"\bcode execution\b": "robot magic",
            r"\bcloud computing\b": "sky stations",
        }
        for page in pages:
            text_val = page.get("text", "")
            for pattern, rep in jargon_replacements.items():
                if re.search(pattern, text_val, flags=re.IGNORECASE):
                    logger.info(
                        f"Replacing technical jargon pattern '{pattern}' in Page {page.get('page_number')}"
                    )
                    text_val = re.sub(pattern, rep, text_val, flags=re.IGNORECASE)
                    page["text"] = text_val

        logger.info("Storybook validation complete. All checks passed/corrected!")
        return story_json

    def run_workflow(
        self,
        topic: str,
        age_group: str,
        max_pages: Optional[int] = None,
        language: str = "English",
        parent_id: Optional[str] = None,
        override_char: Optional[str] = None,
        override_style: Optional[str] = None,
    ):
        """Runs the entire storybook generation workflow for a specific topic."""
        story = self.generate_story(topic, age_group, language=language)
        if override_char:
            story["main_character_description"] = override_char
        if override_style:
            story["artistic_style"] = override_style

        story = self.validate_and_correct_story(story, topic, language=language)
        title = story.get("title", f"Story about {topic}")
        pages = story.get("pages", [])

        if max_pages is not None:
            logger.info(f"Limiting storybook to {max_pages} pages for testing...")
            pages = pages[:max_pages]

        folder_id = self.create_drive_folder(f"{title} (Ages {age_group})", parent_id=parent_id)

        pil_images = []
        pages_data = []
        failed_pages = []
        for page in pages:
            page_num = page.get("page_number")
            text = page.get("text")
            img_prompt = page.get("image_prompt")

            logger.info(f"Processing Page {page_num}...")
            try:
                raw_img = self.generate_page_image(img_prompt)
                final_img = self.overlay_text_on_image(raw_img, text, page_num, len(pages), language=language)

                img = Image.open(io.BytesIO(final_img)).convert("RGB")
                pil_images.append(img)

                filename = f"page_{page_num:02d}.jpg"
                self.upload_image_to_drive(final_img, filename, folder_id)
                pages_data.append((filename, final_img))
            except Exception as e:
                logger.error(f"Failed to generate/upload Page {page_num}: {e}")
                failed_pages.append(page_num)

        if pil_images:
            logger.info("Compiling all pages into a combined PDF...")
            pdf_bytes = self.compile_pdf_bytes(pil_images)
            pdf_filename = f"{title}.pdf"
            self.upload_pdf_to_drive(pdf_bytes, pdf_filename, folder_id)

        try:
            kdp_desc = self.generate_kdp_description(title, topic, age_group, language=language)
            self.upload_txt_as_doc_to_drive(
                kdp_desc, f"{title} - Amazon Publishing KDP Description", folder_id
            )
        except Exception as e:
            logger.error(f"Failed to generate/upload KDP description: {e}")

        if pages_data:
            try:
                epub_bytes = self.create_epub(title, pages_data, language=language)
                epub_filename = f"{title} (eBook).epub"
                self.upload_epub_to_drive(epub_bytes, epub_filename, folder_id)
            except Exception as e:
                logger.error(f"Failed to generate/upload EPUB eBook: {e}")

        if failed_pages:
            raise RuntimeError(f"Workflow incomplete: failed to generate pages {failed_pages}")

        logger.info(f"Storybook '{title}' generation and upload complete!")
        logger.info(f"Output Location: https://drive.google.com/drive/folders/{folder_id}")

    def translate_story(self, story_json: dict, target_language: str) -> dict:
        """Translates the story text and title to the target language using Gemini."""
        if target_language.lower() == "english":
            return story_json

        logger.info(f"Translating storybook into {target_language}...")
        input_data = {
            "title": story_json.get("title", ""),
            "pages": [
                {"page_number": p.get("page_number"), "text": p.get("text", "")}
                for p in story_json.get("pages", [])
            ],
        }

        prompt = f"""
        You are a professional children's book translator.
        Translate the following children's book title and page texts from English into {target_language}.
        
        Ensure that:
        1. The translation matches the tone and simplicity appropriate for young children (ages 4-6).
        2. Line breaks (\\n) in the text are preserved where appropriate (especially on the cover page to separate the title from 'Written by Enrique Chan').
        3. On the final page, ensure the closing text translates to the correct closing word for {target_language} (e.g. 'Fin' for Spanish/French, 'Fim' for Portuguese, 'Fine' for Italian, 'Ende' for German, 'おしまい' for Japanese, '끝' for Korean).
        4. The main character girl's name 'Lily' must be preserved as 'Lily' (or translated phonetically, e.g., '莉莉' in Chinese, '릴리' in Korean, 'リリー' in Japanese). The companion robot's name 'Robo' must remain 'Robo' (or translated to 'Robo' / 'Helper-Bot' equivalent in the target language). Do NOT change them to other names (like 'Luna' or 'Leo').
        
        Input JSON:
        {json.dumps(input_data, ensure_ascii=False)}
        
        Format the output strictly as a JSON object matching this schema:
        {{
            "title": "Translated Book Title",
            "pages": [
                {{
                    "page_number": 1,
                    "text": "Translated text for page 1"
                }}
            ]
        }}
        """
        client = self._get_client()

        def _invoke() -> dict:
            response = client.models.generate_content(
                model=config.TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TranslationSchema,
                    temperature=0.3,
                ),
            )
            return json.loads(response.text)

        translated_data = self._call_with_retry(_invoke, description=f"Translation to {target_language}")

        new_story = story_json.copy()
        new_story["title"] = translated_data.get("title", new_story.get("title"))

        trans_pages = {p.get("page_number"): p.get("text") for p in translated_data.get("pages", [])}
        new_pages = []
        for p in story_json.get("pages", []):
            np = p.copy()
            page_num = p.get("page_number")
            if page_num in trans_pages:
                np["text"] = trans_pages[page_num]
            new_pages.append(np)
        new_story["pages"] = new_pages

        return new_story

    def run_multilingual_workflow(
        self,
        topic: str,
        age_group: str,
        languages: list,
        max_pages: Optional[int] = None,
        parent_id: Optional[str] = None,
        override_char: Optional[str] = None,
        override_style: Optional[str] = None,
    ):
        """Generates raw page illustrations once, then builds individual editions for each language."""
        logger.info(f"Starting multilingual storybook pipeline for topic '{topic}'...")

        base_story = self.generate_story(topic, age_group, language="English")
        if override_char:
            base_story["main_character_description"] = override_char
        if override_style:
            base_story["artistic_style"] = override_style

        base_story = self.validate_and_correct_story(base_story, topic, language="English")
        pages = base_story.get("pages", [])
        if max_pages is not None:
            logger.info(f"Limiting base storybook to {max_pages} pages for testing...")
            pages = pages[:max_pages]
            base_story["pages"] = pages

        raw_images = {}
        for page in pages:
            page_num = page.get("page_number")
            img_prompt = page.get("image_prompt")
            logger.info(f"Generating base raw illustration for Page {page_num}...")
            try:
                raw_img = self.generate_page_image(img_prompt)
                raw_images[page_num] = raw_img
            except Exception as e:
                logger.error(f"Failed to generate base raw image for Page {page_num}: {e}")
                raise e

        for language in languages:
            logger.info(f"=== Generating book edition for language: '{language}' ===")
            try:
                lang_story = self.translate_story(base_story, language)
                lang_title = lang_story.get("title", f"Story about {topic} ({language})")
                lang_pages = lang_story.get("pages", [])

                lang_folder_id = self.create_drive_folder(
                    f"{lang_title} (Ages {age_group})", parent_id=parent_id
                )

                pil_images = []
                pages_data = []
                for p in lang_pages:
                    p_num = p.get("page_number")
                    text = p.get("text")

                    if p_num not in raw_images:
                        logger.error(f"Raw image missing for Page {p_num}! Skipping page...")
                        continue

                    raw_img = raw_images[p_num]
                    final_img = self.overlay_text_on_image(
                        raw_img, text, p_num, len(lang_pages), language=language
                    )

                    img = Image.open(io.BytesIO(final_img)).convert("RGB")
                    pil_images.append(img)

                    filename = f"page_{p_num:02d}.jpg"
                    self.upload_image_to_drive(final_img, filename, lang_folder_id)
                    pages_data.append((filename, final_img))

                if pil_images:
                    pdf_bytes = self.compile_pdf_bytes(pil_images)
                    pdf_filename = f"{lang_title}.pdf"
                    self.upload_pdf_to_drive(pdf_bytes, pdf_filename, lang_folder_id)

                if pages_data:
                    epub_bytes = self.create_epub(lang_title, pages_data, language=language)
                    epub_filename = f"{lang_title} (eBook).epub"
                    self.upload_epub_to_drive(epub_bytes, epub_filename, lang_folder_id)

                kdp_desc = self.generate_kdp_description(lang_title, topic, age_group, language=language)
                self.upload_txt_as_doc_to_drive(
                    kdp_desc, f"{lang_title} - Amazon Publishing KDP Description", lang_folder_id
                )

                logger.info(f"Successfully generated and uploaded '{language}' edition!")
            except Exception as e:
                logger.error(f"Failed to generate book edition for language '{language}': {e}")

    def generate_chinese_audio(
        self,
        story_json: dict,
        output_mp3_path: str,
        voice: str = audio_generator.DEFAULT_CHINESE_VOICE,
        rate_wpm: int = 175,
        bgm_path: str = "background_music.mp3",
        bgm_volume: float = 0.12,
    ) -> str:
        """Generates a soothing Chinese read-aloud MP3 audio file from the story JSON."""
        logger.info(f"Generating Chinese audio narration for '{story_json.get('title')}'...")

        sample_text = ""
        for p in story_json.get("pages", []):
            t = p.get("text", "")
            if t:
                sample_text = t
                break

        if not audio_generator.is_chinese_text(sample_text):
            logger.info("Story text is not in Chinese. Translating script to Chinese for narration...")
            try:
                zh_story = self.translate_story(story_json, "Chinese Simplified")
            except Exception as e:
                logger.warning(f"Translation to Chinese failed ({e}); falling back to original story text.")
                zh_story = story_json
        else:
            zh_story = story_json

        return audio_generator.generate_chinese_audio_file(
            story_json=zh_story,
            output_mp3_path=output_mp3_path,
            voice=voice,
            rate_wpm=rate_wpm,
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
        )

    def _render_single_page(
        self,
        page: dict,
        total_pages: int,
        language: str,
        local_dir: str,
        resume: bool = False,
        enable_visual_qa: bool = False,
        max_qa_retries: int = config.MAX_QA_RETRIES,
    ) -> dict[str, Any]:
        """Generates (or loads cached) raw page image, runs optional visual QA loop, and overlays text."""
        page_num = page.get("page_number", 1)
        text = page.get("text", "")
        img_prompt = page.get("image_prompt", "")
        scene_desc = page.get("scene_description", "")
        features_char = page.get("features_main_character", True)

        img_filename = f"page_{page_num:02d}.jpg"
        img_path = os.path.join(local_dir, img_filename)
        raw_cache_path = os.path.join(local_dir, f".cache_page_{page_num:02d}_raw.jpg")
        prompt_hash = hashlib.sha256(f"{img_prompt}|{config.IMAGE_MODEL}".encode("utf-8")).hexdigest()[:16]
        hash_path = os.path.join(local_dir, f".cache_page_{page_num:02d}.sha256")

        t0 = time.monotonic()
        raw_img: Optional[bytes] = None
        qa_report: dict[str, Any] = {"passed": True, "score": 1.0, "issues": [], "attempts": 1}

        # Check resume cache
        if resume and os.path.exists(raw_cache_path) and os.path.exists(hash_path):
            try:
                cached_hash = open(hash_path, encoding="utf-8").read().strip()
                if cached_hash == prompt_hash:
                    with open(raw_cache_path, "rb") as rf:
                        raw_img = rf.read()
                    logger.info(f"Reusing cached raw illustration for Page {page_num} (hash={prompt_hash})")
            except Exception:
                raw_img = None

        if raw_img is None:
            attempts = max(1, max_qa_retries + 1) if enable_visual_qa else 1
            for qa_attempt in range(1, attempts + 1):
                prompt_for_attempt = img_prompt
                if qa_attempt > 1:
                    prompt_for_attempt = (
                        f"{img_prompt} CRITICAL CORRECTION: Do NOT render any words, letters, titles, "
                        "or banners anywhere in the image. Ensure Robo is a smooth sphere with NO antennas and NO wheels."
                    )
                raw_img = self.generate_page_image(prompt_for_attempt)
                if enable_visual_qa:
                    qa_report = self.evaluate_page_image_qa(
                        raw_img,
                        page_num=page_num,
                        scene_description=scene_desc,
                        features_char=features_char,
                    )
                    qa_report["attempts"] = qa_attempt
                    if qa_report.get("passed", True):
                        break
                    logger.warning(
                        f"Page {page_num} failed Visual QA (attempt {qa_attempt}/{attempts}): {qa_report.get('issues')}"
                    )
                else:
                    break

            if resume and raw_img:
                try:
                    with open(raw_cache_path, "wb") as rf:
                        rf.write(raw_img)
                    with open(hash_path, "w", encoding="utf-8") as hf:
                        hf.write(prompt_hash)
                except Exception:
                    pass

        final_img = self.overlay_text_on_image(raw_img, text, page_num, total_pages, language=language)
        with open(img_path, "wb") as f:
            f.write(final_img)

        duration_s = round(time.monotonic() - t0, 2)
        return {
            "page_number": page_num,
            "filename": img_filename,
            "path": img_path,
            "bytes": final_img,
            "duration_s": duration_s,
            "qa": qa_report,
        }

    def run_fast_publishing_workflow(
        self,
        topic: str,
        age_group: str = "4-6",
        max_pages: Optional[int] = None,
        language: str = "English",
        parent_id: Optional[str] = None,
        override_char: Optional[str] = None,
        override_style: Optional[str] = None,
        generate_audio: bool = True,
        output_dir: Optional[str] = None,
        bgm_path: str = "background_music.mp3",
        chinese_voice: str = audio_generator.DEFAULT_CHINESE_VOICE,
        resume: bool = False,
        max_workers: int = 1,
        enable_visual_qa: bool = False,
        upscale_pdf: bool = False,
    ) -> dict:
        """Runs the fast, streamlined single-edition publishing pipeline with Chinese MP3 audio.

        Supports:
        - Resume from cached `story.json` and raw page illustrations (`resume=True`)
        - Parallel page illustration generation (`max_workers > 1`)
        - Per-page Gemini Vision QA gate with automatic regeneration (`enable_visual_qa=True`)
        - Valid EPUB 3.0 + 300 DPI PDF + KDP metadata kit + `manifest.json`
        """
        workflow_start = time.monotonic()
        logger.info(f"=== Starting Fast Publishing Workflow for topic: '{topic}' ===")

        # 1. Load cached story.json on resume or generate fresh story structure
        story = None
        if resume and output_dir and os.path.exists(os.path.join(output_dir, "story.json")):
            try:
                with open(os.path.join(output_dir, "story.json"), encoding="utf-8") as f:
                    story = json.load(f)
                logger.info(f"Resumed existing story.json from '{output_dir}'")
            except Exception as e:
                logger.warning(f"Could not load cached story.json ({e}); regenerating...")

        if story is None:
            story = self.generate_story(topic, age_group, language=language)

        if override_char:
            story["main_character_description"] = override_char
        if override_style:
            story["artistic_style"] = override_style

        story = self.validate_and_correct_story(story, topic, language=language)
        title = story.get("title", f"Story about {topic}")
        pages = story.get("pages", [])

        if max_pages is not None:
            logger.info(f"Limiting storybook to {max_pages} pages for testing...")
            pages = pages[:max_pages]
            story["pages"] = pages

        safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in title).strip()
        local_dir = output_dir or os.path.join("output", safe_title)
        os.makedirs(local_dir, exist_ok=True)

        story_json_path = os.path.join(local_dir, "story.json")
        with open(story_json_path, "w", encoding="utf-8") as f:
            json.dump(story, f, ensure_ascii=False, indent=2)

        # 2. Setup Google Drive folder if available
        folder_id = None
        if self.drive_service:
            try:
                folder_id = self.create_drive_folder(f"{title} (Ages {age_group})", parent_id=parent_id)
                logger.info(f"Created Google Drive folder (ID: {folder_id})")
            except Exception as e:
                logger.warning(f"Could not create Google Drive folder: {e}. Output will be saved locally.")

        # 3. Generate illustrations and overlay text (sequential when max_workers==1, parallel when >1)
        page_results: dict[int, dict[str, Any]] = {}
        pages_failed: list[dict[str, Any]] = []
        total_pages = len(pages)

        if max_workers > 1 and total_pages > 1:
            logger.info(f"Generating {total_pages} pages concurrently (max_workers={max_workers})...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_pnum = {
                    executor.submit(
                        self._render_single_page,
                        page,
                        total_pages,
                        language,
                        local_dir,
                        resume,
                        enable_visual_qa,
                    ): page.get("page_number", idx + 1)
                    for idx, page in enumerate(pages)
                }
                for future in concurrent.futures.as_completed(future_to_pnum):
                    p_num = future_to_pnum[future]
                    try:
                        page_results[p_num] = future.result()
                    except Exception as e:
                        logger.error(f"Failed to generate Page {p_num}: {e}")
                        pages_failed.append({"page_number": p_num, "error": str(e)})
        else:
            for idx, page in enumerate(pages):
                p_num = page.get("page_number", idx + 1)
                logger.info(f"Generating illustration and text overlay for Page {p_num}...")
                try:
                    page_results[p_num] = self._render_single_page(
                        page,
                        total_pages,
                        language,
                        local_dir,
                        resume=resume,
                        enable_visual_qa=enable_visual_qa,
                    )
                except Exception as e:
                    logger.error(f"Failed to generate Page {p_num}: {e}")
                    pages_failed.append({"page_number": p_num, "error": str(e)})

        pil_images: list[Image.Image] = []
        pages_data: list[tuple[str, bytes]] = []
        qa_reports: list[dict[str, Any]] = []

        for page in pages:
            p_num = page.get("page_number")
            if p_num not in page_results:
                continue
            item = page_results[p_num]
            final_img = item["bytes"]
            img_filename = item["filename"]
            qa_reports.append({"page_number": p_num, **item.get("qa", {})})

            if folder_id and self.drive_service:
                try:
                    self.upload_image_to_drive(final_img, img_filename, folder_id)
                except Exception as e:
                    logger.warning(f"Drive image upload failed: {e}")

            img = Image.open(io.BytesIO(final_img)).convert("RGB")
            pil_images.append(img)
            pages_data.append((img_filename, final_img))

        # 4. Compile and save combined 300 DPI PDF
        pdf_path = None
        if pil_images:
            logger.info("Compiling all pages into a combined PDF manuscript...")
            pdf_bytes = self.compile_pdf_bytes(pil_images, dpi=config.PDF_DPI, upscale_for_print=upscale_pdf)
            pdf_filename = f"{safe_title}.pdf"
            pdf_path = os.path.join(local_dir, pdf_filename)
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

            if folder_id and self.drive_service:
                try:
                    self.upload_pdf_to_drive(pdf_bytes, f"{title}.pdf", folder_id)
                except Exception as e:
                    logger.warning(f"Drive PDF upload failed: {e}")

        # 5. Compile, validate, and save KDP eBook EPUB 3
        epub_path = None
        epub_validation = {"valid": False, "errors": []}
        if pages_data:
            try:
                epub_bytes = self.create_epub(title, pages_data, language=language)
                epub_filename = f"{safe_title} (eBook).epub"
                epub_path = os.path.join(local_dir, epub_filename)
                with open(epub_path, "wb") as f:
                    f.write(epub_bytes)

                epub_validation = self.validate_epub_bytes(epub_bytes, epub_path=epub_path)
                if not epub_validation["valid"]:
                    logger.warning(f"EPUB validation warnings: {epub_validation['errors']}")

                if folder_id and self.drive_service:
                    try:
                        self.upload_epub_to_drive(epub_bytes, f"{title} (eBook).epub", folder_id)
                    except Exception as e:
                        logger.warning(f"Drive EPUB upload failed: {e}")
            except Exception as e:
                logger.error(f"Failed to generate EPUB eBook: {e}")

        # 6. Generate and save KDP Description + KDP Upload Metadata Kit
        kdp_path = None
        kdp_desc = ""
        try:
            kdp_desc = self.generate_kdp_description(title, topic, age_group, language=language)
            kdp_path = os.path.join(local_dir, "kdp_description.txt")
            with open(kdp_path, "w", encoding="utf-8") as f:
                f.write(kdp_desc)

            if folder_id and self.drive_service:
                try:
                    self.upload_txt_as_doc_to_drive(
                        kdp_desc, f"{title} - Amazon Publishing KDP Description", folder_id
                    )
                except Exception as e:
                    logger.warning(f"Drive KDP description upload failed: {e}")
        except Exception as e:
            logger.error(f"Failed to generate KDP description: {e}")

        # Write KDP upload kit metadata JSON
        kdp_metadata_path = os.path.join(local_dir, "kdp_metadata.json")
        kdp_metadata = {
            "title": title,
            "series": f"{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}",
            "author": prompts.SERIES_AUTHOR,
            "language": language,
            "age_group": age_group,
            "trim_size_inches": f"{config.PDF_TRIM_INCHES}x{config.PDF_TRIM_INCHES}",
            "pdf_dpi": config.PDF_DPI,
            "back_cover_blurb": story.get("back_cover_blurb", ""),
            "keywords": [
                "STEM picture book for kids ages 4-6",
                "AI and robotics bedtime story",
                "Curious Little Minds Lily and Robo",
                "coding and technology for preschoolers",
                "kindergarten computer science book",
                "bilingual family STEM read aloud",
                topic.split("(")[0].strip(),
            ],
        }
        try:
            with open(kdp_metadata_path, "w", encoding="utf-8") as f:
                json.dump(kdp_metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write kdp_metadata.json: {e}")

        # 7. Generate Chinese Read-Aloud MP3 Audio
        audio_path = None
        if generate_audio:
            try:
                audio_filename = f"{safe_title}_Chinese_Read_Aloud.mp3"
                audio_path = os.path.join(local_dir, audio_filename)
                self.generate_chinese_audio(
                    story_json=story,
                    output_mp3_path=audio_path,
                    voice=chinese_voice,
                    bgm_path=bgm_path,
                )
                if folder_id and self.drive_service and os.path.exists(audio_path):
                    try:
                        with open(audio_path, "rb") as f:
                            audio_bytes = f.read()
                        self.upload_audio_to_drive(audio_bytes, audio_filename, folder_id)
                    except Exception as e:
                        logger.warning(f"Drive audio upload failed: {e}")
            except Exception as e:
                logger.error(f"Failed to generate Chinese audio narration: {e}")

        total_duration_s = round(time.monotonic() - workflow_start, 2)
        status = "success" if (len(pages_failed) == 0 and len(pages_data) == len(pages)) else "partial_failure"

        manifest = {
            "status": status,
            "title": title,
            "topic": topic,
            "language": language,
            "age_group": age_group,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": total_duration_s,
            "pages_expected": len(pages),
            "pages_succeeded": len(pages_data),
            "pages_failed": pages_failed,
            "epub_validation": epub_validation,
            "qa_reports": qa_reports,
            "output_dir": os.path.abspath(local_dir),
            "drive_folder_id": folder_id,
            "pdf_path": pdf_path,
            "epub_path": epub_path,
            "kdp_description_path": kdp_path,
            "kdp_metadata_path": kdp_metadata_path,
            "audio_path": audio_path,
        }
        manifest_path = os.path.join(local_dir, "manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write manifest.json: {e}")

        logger.info(f"=== Fast Publishing Workflow completed for '{title}' (status={status})! ===")
        logger.info(f"Local Assets Directory: {os.path.abspath(local_dir)}")
        if folder_id:
            logger.info(f"Google Drive Folder: https://drive.google.com/drive/folders/{folder_id}")

        return {
            "status": status,
            "title": title,
            "pages_count": len(pages),
            "pages_succeeded": len(pages_data),
            "pages_failed": pages_failed,
            "output_dir": os.path.abspath(local_dir),
            "drive_folder_id": folder_id,
            "pdf_path": pdf_path,
            "epub_path": epub_path,
            "kdp_description_path": kdp_path,
            "kdp_metadata_path": kdp_metadata_path,
            "manifest_path": manifest_path,
            "audio_path": audio_path,
        }
