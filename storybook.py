import concurrent.futures
import functools
import hashlib
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import google.auth
from google.genai import Client, types
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

import audio_generator
import config
import drive_sync
import epub_packager
import prompts

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Closing punctuation that must never begin a wrapped CJK line (kinsoku shori).
CJK_NO_LINE_START = "，。！？、；：」』）》〉】…,.!?;:)"


@functools.lru_cache(maxsize=8)
def _resolve_best_font_path(bundled_dir_str: str) -> Optional[str]:
    """Resolves and caches the first loadable cross-platform Unicode/CJK font path."""
    candidate_paths: list[str] = []
    bundled_dir = pathlib.Path(bundled_dir_str)
    if bundled_dir.exists():
        for fname in (
            "NotoSansCJK-Regular.ttc",
            "NotoSansSC-Regular.otf",
            "NotoSansSC-Regular.ttf",
            "DejaVuSans.ttf",
        ):
            p = bundled_dir / fname
            if p.exists():
                candidate_paths.append(str(p))

    candidate_paths.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )

    for path in candidate_paths:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, size=16)
                return path
            except Exception:
                continue
    return None


@functools.lru_cache(maxsize=64)
def _load_cached_truetype(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


@functools.lru_cache(maxsize=32)
def _read_cached_bytes_by_stat(path_str: str, mtime_ns: int, size: int) -> bytes:
    return pathlib.Path(path_str).read_bytes()


def read_file_bytes_cached(path: pathlib.Path) -> Optional[bytes]:
    """Reads file bytes with stat-invalidated in-memory caching."""
    try:
        st = path.stat()
        return _read_cached_bytes_by_stat(str(path), st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Failed to read cached file {path}: {e}")
        return None


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
    passed: bool = Field(description="True only if image is textless, has at most 1 Lily and 1 Robo, and matches Book 1 invariants")
    lily_count: int = Field(
        default=1,
        description="Number of times the girl Lily appears in the candidate illustration (must be 0 or 1; >1 means duplicate/cloned Lily). Chinese Heritage books: number of human children (Mei and/or Ming) visible",
    )
    robo_count: int = Field(
        default=1,
        description="Number of times the blue spherical robot Robo appears in the candidate illustration (must be 0 or 1; >1 means duplicate/cloned Robo). Chinese Heritage books: number of red pandas (Baobao) visible",
    )
    has_unwanted_text: bool = Field(
        description="True if any words, letters, titles, speech bubbles, banners, or watermarks appear inside the artwork"
    )
    robo_off_model: bool = Field(
        description="True if Robo has a dark black screen or recessed visor cutout instead of his face drawn directly on his round sky-blue spherical body, or has wheels, tracks, or a boxy square torso"
    )
    lily_off_model: bool = Field(
        description="True if Lily has braided hair or a single ponytail instead of two high-side fluffy curly pigtail puffs with red bows and wavy shoulder-length curls, is missing her yellow polka-dot dress with white Peter Pan collar, OR has extra hands (>2 hands) or disembodied hands on her blue backpack straps"
    )
    lily_extra_hands: bool = Field(
        default=False,
        description="True if Lily has more than 2 hands/arms total OR has disembodied hands gripping her blue backpack shoulder straps",
    )
    score: float = Field(default=1.0, description="Quality score from 0.0 to 1.0")
    issues: list[str] = Field(default_factory=list, description="Specific visual issues detected")


HERITAGE_CAST = ["mei", "ming", "baobao"]

# Solo reference crop per Chinese Heritage presence mode (used by image generation and Visual QA).
HERITAGE_SOLO_REFS = {"mei_only": "mei_ref.png", "ming_only": "ming_ref.png", "baobao_only": "baobao_ref.png"}

# (max human children [Mei/Ming], max red pandas [Baobao]) allowed per Chinese Heritage presence mode.
HERITAGE_PRESENCE_LIMITS = {
    "both": (2, 1),
    "mei_only": (1, 0),
    "ming_only": (1, 0),
    "baobao_only": (0, 1),
    "none": (0, 0),
}

STEM_KDP_KEYWORDS = [
    "STEM picture book for kids ages 4-6",
    "AI and robotics bedtime story",
    "Curious Little Minds Lily and Robo",
    "coding and technology for preschoolers",
    "kindergarten computer science book",
    "bilingual family STEM read aloud",
]

HERITAGE_KDP_KEYWORDS = [
    "Chinese proverb picture book for kids ages 4-6",
    "Chinese heritage bedtime story",
    "Curious Little Minds Mei Ming and Baobao",
    "chengyu stories for preschoolers",
    "Chinese culture gift book for kids",
    "bilingual family Chinese read aloud",
]


def _to_png_bytes(image_bytes: bytes) -> bytes:
    """Re-encodes generated image bytes as a real PNG so `.png` reference files match their mime type."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes


def _compose_side_by_side(images: list[bytes], height: int = 1024) -> bytes:
    """Deterministically lays images side by side on one PNG canvas (safe master-sheet fallback)."""
    tiles = []
    for data in images:
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            width = max(1, round(rgb.width * height / rgb.height))
            tiles.append(rgb.resize((width, height), Image.Resampling.LANCZOS))
    canvas = Image.new("RGB", (sum(t.width for t in tiles), height), (250, 244, 230))
    x = 0
    for tile in tiles:
        canvas.paste(tile, (x, 0))
        x += tile.width
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def build_kdp_metadata(
    title: str,
    topic: str,
    language: str,
    age_group: str,
    back_cover_blurb: str = "",
    genre: Optional[str] = None,
) -> dict[str, Any]:
    """Builds the KDP upload-kit metadata using the series name and keywords of the book's genre."""
    heritage = prompts.is_heritage_genre(topic=topic, genre=genre)
    series_name = prompts.HERITAGE_SERIES_NAME if heritage else prompts.SERIES_NAME
    keywords = HERITAGE_KDP_KEYWORDS if heritage else STEM_KDP_KEYWORDS
    return {
        "title": title,
        "series": f"{prompts.SERIES_BRAND}: {series_name}",
        "author": prompts.SERIES_AUTHOR,
        "language": language,
        "age_group": age_group,
        "trim_size_inches": f"{config.PDF_TRIM_INCHES}x{config.PDF_TRIM_INCHES}",
        "pdf_dpi": config.PDF_DPI,
        "back_cover_blurb": back_cover_blurb,
        "keywords": [*keywords, topic.split("(")[0].strip()],
    }


class StorybookGenerator:
    """Generates kid-friendly stories and overlayed cover/page art, exporting to Google Drive."""

    def __init__(self, drive_service=None, ai_client: Optional[Client] = None):
        self.drive_service = drive_service
        self.ai_client = ai_client
        self._drive_folder_indices: dict[str, dict[str, dict[str, Any]]] = {}

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
        if isinstance(Client, type):
            try:
                from google.auth.transport.requests import Request as AuthRequest

                creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                if not creds.valid:
                    creds.refresh(AuthRequest())
            except Exception:
                try:
                    from google.auth import credentials as auth_credentials

                    class _GcloudCliCredentials(auth_credentials.Credentials):
                        def __init__(self) -> None:
                            super().__init__()
                            self.refresh(None)

                        def refresh(self, request) -> None:
                            self.token = subprocess.check_output(
                                ["gcloud", "auth", "print-access-token"], text=True, timeout=10
                            ).strip()

                    if getattr(self, "_gcloud_creds", None) is None:
                        self._gcloud_creds = _GcloudCliCredentials()
                    return Client(credentials=self._gcloud_creds)
                except Exception:
                    pass
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
        font_path = _resolve_best_font_path(str(config.BUNDLED_FONT_DIR))
        if font_path:
            try:
                return _load_cached_truetype(font_path, size)
            except Exception:
                pass

        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def generate_story(
        self,
        topic: str,
        age_group: str,
        language: str = "English",
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
    ) -> dict:
        """Generates a structured kid-friendly story outline and pages with schema validation & retry."""
        prompt = prompts.get_story_generation_prompt(
            topic, age_group, language, character_ids=character_ids, genre=genre
        )
        logger.info(
            f"Generating story for topic: '{topic}', age group: '{age_group}' using {config.TEXT_MODEL}..."
        )
        self._ensure_project_configured()
        client = self._get_client()

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

        if prompts.is_heritage_genre(topic=topic, character_ids=character_ids, genre=genre):
            story_json["genre"] = "chinese_heritage"
            story_json["series"] = f"{prompts.SERIES_BRAND}: {prompts.HERITAGE_SERIES_NAME}"
            story_json["main_character_description"] = prompts.HERITAGE_CHARACTER
            story_json["artistic_style"] = prompts.HERITAGE_STYLE

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
        """Loads canonical Book 1 Lily & Robo character reference sheet from disk."""
        return read_file_bytes_cached(config.REFERENCE_SHEET_PATH)

    def _load_character_reference_images(
        self,
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
    ) -> list[bytes]:
        """Loads the canonical Book 1 or Chinese Heritage master reference sheet plus any active guest sheets."""
        ref_bytes_list: list[bytes] = []
        ref_paths = (
            config.get_character_reference_paths(character_ids, genre=genre)
            if genre is not None
            else config.get_character_reference_paths(character_ids)
        )
        for path in ref_paths:
            data = read_file_bytes_cached(path)
            if data is not None:
                ref_bytes_list.append(data)

        is_heritage = config.is_heritage_character_list(character_ids, genre=genre)
        if not ref_bytes_list and not is_heritage:
            default_ref = self._load_reference_sheet_bytes()
            if default_ref:
                ref_bytes_list.append(default_ref)

        if not ref_bytes_list and config.REQUIRE_CHARACTER_REFERENCE and not is_heritage:
            raise FileNotFoundError(
                f"Mandatory Book 1 character reference sheet missing at {config.REFERENCE_SHEET_PATH}. "
                "Book 1 visual reference conditioning is required for all character illustrations."
            )
        return ref_bytes_list

    def ensure_character_reference_image(self, char_id: str) -> Optional[bytes]:
        """Ensures a character in characters.yaml has a reference image PNG on disk, generating one if missing."""
        registry = {str(c.get("id", "")).lower(): c for c in config.load_characters()}
        c = registry.get(char_id.lower())
        if not c:
            return None
        rel_path = c.get("reference_image")
        if not rel_path:
            return None
        target_path = config.PROJECT_ROOT / rel_path
        if target_path.exists():
            return target_path.read_bytes()

        logger.info(f"Generating canonical character reference sheet for '{char_id}' -> {target_path}...")
        os.makedirs(target_path.parent, exist_ok=True)
        is_heritage_char = str(c.get("genre", "")).lower() in ("chinese_heritage", "heritage", "proverbs")
        book1_ref = self._load_reference_sheet_bytes()
        style_clause = (
            "in a warm whimsical watercolor & colored-pencil storybook style harmoniously infused with classical Chinese shuimo (ink-and-wash) brushwork"
            if is_heritage_char
            else "in the exact whimsical watercolor & colored-pencil children's book style of the attached Book 1 reference sheet"
        )
        if is_heritage_char:
            prompt = (
                f"Canonical character reference illustration {style_clause}. Show ONLY ONE single character, "
                f"{c.get('name', char_id)}, as ONE full-body front-facing standing figure (no second pose, no turnaround): "
                f"{c.get('visual_description', '')}. "
                f"Invariants: {c.get('strict_invariants') or c.get('invariants', '')}. "
                "Plain warm cream rice-paper background with a soft ground shadow, NO scenery, NO text, NO labels, "
                "NO letters, NO seals, NO other characters or animals."
            )
        else:
            prompt = (
                f"Character turnaround reference sheet {style_clause}. Show ONLY ONE single character "
                f"{c.get('name', char_id)}: {c.get('visual_description', '')}. "
                f"Invariants: {c.get('strict_invariants') or c.get('invariants', '')}. "
                f"Clean warm cream rice-paper studio background, full-body front view and 3/4 view, NO text, NO labels, NO letters, NO other characters."
            )
        world_ref_path = config.PROJECT_ROOT / "assets" / "world_style_reference.png"
        style_refs = (
            [world_ref_path.read_bytes()]
            if (is_heritage_char and world_ref_path.exists())
            else ([book1_ref] if book1_ref else None)
        )
        img_bytes = _to_png_bytes(
            self.generate_page_image(
                prompt=prompt,
                reference_images=style_refs,
            )
        )
        target_path.write_bytes(img_bytes)
        return img_bytes

    def _lineup_has_full_heritage_cast(self, image_bytes: bytes) -> bool:
        """Vision-QA gate for the master sheet: exactly 2 children (Mei, Ming) + 1 red panda (Baobao), on-model."""
        qa = self.evaluate_page_image_qa(
            image_bytes,
            page_num=0,
            scene_description="Mei, Ming, and Baobao standing side by side in a character lineup.",
            features_char=True,
            character_ids=HERITAGE_CAST,
        )
        try:
            children, pandas = int(qa["lily_count"]), int(qa["robo_count"])
        except (KeyError, TypeError, ValueError):
            return False  # QA unavailable: never accept an unverified master sheet
        return bool(qa.get("passed")) and children == 2 and pandas == 1

    def ensure_heritage_reference_sheet(self, max_attempts: int = 3) -> Optional[bytes]:
        """Ensures the Mei, Ming & Baobao master lineup sheet exists, composing it on first use.

        The individual character references are generated first (if missing) and then used as
        conditioning, so the master sheet reproduces exactly the same canonical designs. Each
        candidate must pass a Vision-QA cast count (2 children + 1 red panda); if every attempt
        fails, the approved references are laid side by side deterministically instead.
        """
        sheet_path = config.HERITAGE_REFERENCE_SHEET_PATH
        if sheet_path.exists():
            return sheet_path.read_bytes()
        char_refs = [ref for ref in (self.ensure_character_reference_image(cid) for cid in HERITAGE_CAST) if ref]
        if len(char_refs) < len(HERITAGE_CAST):
            logger.warning("Heritage master reference sheet skipped: not all character references are available.")
            return None
        logger.info(f"Composing Chinese Heritage master reference sheet -> {sheet_path}...")
        prompt = (
            "The three attached reference images show, in order: (1) Mei, a 6-year-old girl; (2) Ming, her "
            "5-year-old little brother; (3) Baobao, their small red-panda spirit guide. Draw ALL THREE of them "
            "together in ONE single illustration as a canonical character lineup reference sheet: Mei standing on "
            "the LEFT, Ming standing in the MIDDLE, and Baobao standing on the RIGHT, all full-body and front-facing. "
            "All three MUST be visible: exactly two children and one red panda. "
            f"Mei: {prompts.MEI_CHARACTER_DESC} "
            f"Ming: {prompts.MING_CHARACTER_DESC} "
            f"Baobao: {prompts.BAOBAO_CHARACTER_DESC} "
            "Match each attached reference EXACTLY (same faces, hairstyles, outfits, colors, and proportions). "
            "Mei is the tallest, Ming is slightly shorter than Mei, and Baobao is small, about knee-high to the "
            "children. Warm whimsical watercolor & colored-pencil storybook style harmoniously infused with "
            "classical Chinese shuimo (ink-and-wash) brushwork, gentle brown-ink outlines, visible rice-paper grain, "
            "plain warm cream rice-paper background, ONE of each character only, NO scenery, NO text, NO labels, "
            "NO letters, NO seals."
        )
        img_bytes: Optional[bytes] = None
        candidate = b""
        for attempt in range(1, max_attempts + 1):
            candidate = _to_png_bytes(self.generate_page_image(prompt=prompt, reference_images=char_refs))
            if self._lineup_has_full_heritage_cast(candidate):
                img_bytes = candidate
                break
            logger.warning(
                f"Heritage master sheet attempt {attempt}/{max_attempts} failed the cast check "
                "(need exactly Mei, Ming, and Baobao); regenerating..."
            )
        if img_bytes is None:
            logger.warning("Using a deterministic side-by-side lineup of the approved character references.")
            try:
                img_bytes = _compose_side_by_side(char_refs)
            except Exception as e:
                logger.warning(f"Deterministic lineup failed ({e}); keeping the last generated candidate.")
                img_bytes = candidate
        os.makedirs(sheet_path.parent, exist_ok=True)
        sheet_path.write_bytes(img_bytes)
        return img_bytes

    def generate_page_image(
        self,
        prompt: str,
        reference_images: Optional[list[bytes]] = None,
        character_ids: Optional[list[str]] = None,
    ) -> bytes:
        """Generates a single page illustration using gemini-2.5-flash-image conditioned on reference art.

        Automatically adapts visual conditioning to both the STEM (Lily & Robo) and
        Chinese Heritage & Proverbs (Mei, Ming & Baobao) character presence modes.
        """
        logger.info(f"Generating image with prompt: '{prompt}'...")
        client = self._get_client()

        contents: list[Any] = []
        is_world_only = "PURE WORLD & OBJECT SCENE (NO CHARACTERS)" in prompt
        is_lily_only = "SOLO LILY SCENE (NO ROBO IN FRAME)" in prompt
        is_robo_only = "SOLO ROBO SCENE (NO LILY IN FRAME)" in prompt
        is_mei_only = prompts.SOLO_MEI_MARKER in prompt
        is_ming_only = prompts.SOLO_MING_MARKER in prompt
        is_baobao_only = prompts.SOLO_BAOBAO_MARKER in prompt
        is_heritage = (
            is_mei_only
            or is_ming_only
            or is_baobao_only
            or config.is_heritage_character_list(character_ids)
            or "STRICT CHINESE HERITAGE CHARACTER INVARIANTS" in prompt
        )

        if reference_images is not None:
            ref_list = list(reference_images)
        elif is_world_only:
            world_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "world_style_reference.png")
            ref_list = [world_ref] if world_ref else []
        elif is_mei_only:
            mei_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "mei_ref.png")
            ref_list = [mei_ref] if mei_ref else self._load_character_reference_images(character_ids or ["mei"], genre="chinese_heritage")
        elif is_ming_only:
            ming_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "ming_ref.png")
            ref_list = [ming_ref] if ming_ref else self._load_character_reference_images(character_ids or ["ming"], genre="chinese_heritage")
        elif is_baobao_only:
            baobao_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "baobao_ref.png")
            ref_list = [baobao_ref] if baobao_ref else self._load_character_reference_images(character_ids or ["baobao"], genre="chinese_heritage")
        elif is_lily_only:
            lily_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "lily_book1_ref.png")
            ref_list = [lily_ref] if lily_ref else self._load_character_reference_images(character_ids)
        elif is_robo_only:
            robo_ref = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "robo_book1_ref.png")
            ref_list = [robo_ref] if robo_ref else self._load_character_reference_images(character_ids)
        else:
            ref_list = self._load_character_reference_images(
                character_ids or (["mei", "ming", "baobao"] if is_heritage else None),
                genre="chinese_heritage" if is_heritage else None,
            )

        for ref_bytes in ref_list:
            contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))

        if ref_list and is_world_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "WATERCOLOR STYLE REFERENCE ONLY — NO CHARACTERS IN THIS SCENE: "
                        "Match only the whimsical hand-painted watercolor and colored-pencil artistic style, "
                        "warm brown ink outlines, pastel color palette, and visible watercolor paper grain "
                        "of the attached environment reference. Do NOT draw Lily, Robo, Mei, Ming, Baobao, or any human, "
                        "animal, or robot characters in this illustration. "
                        + prompt
                    )
                )
            )
        elif ref_list and is_mei_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT MEI CHARACTER MATCH REQUIREMENT (SOLO MEI SCENE — NO MING OR BAOBAO): "
                        f"Match the exact character design and whimsical Chinese shuimo watercolor style of Mei from the attached reference: {prompts.MEI_CHARACTER_DESC}. "
                        "Draw a single unified scene with ONLY ONE Mei and DO NOT include Ming or Baobao in this frame. "
                        + prompt
                    )
                )
            )
        elif ref_list and is_ming_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT MING CHARACTER MATCH REQUIREMENT (SOLO MING SCENE — NO MEI OR BAOBAO): "
                        f"Match the exact character design and whimsical Chinese shuimo watercolor style of Ming from the attached reference: {prompts.MING_CHARACTER_DESC}. "
                        "Draw a single unified scene with ONLY ONE Ming and DO NOT include Mei or Baobao in this frame. "
                        + prompt
                    )
                )
            )
        elif ref_list and is_baobao_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT BAOBAO CHARACTER MATCH REQUIREMENT (SOLO BAOBAO SCENE — NO HUMAN CHILDREN): "
                        f"Match the exact character design and whimsical Chinese shuimo watercolor style of Baobao from the attached reference: {prompts.BAOBAO_CHARACTER_DESC}. "
                        "Draw a single unified scene with ONLY ONE Baobao and DO NOT include Mei or Ming in this frame. "
                        + prompt
                    )
                )
            )
        elif ref_list and is_heritage and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT CHINESE HERITAGE CHARACTER MATCH REQUIREMENT: Match the exact character designs, "
                        "facial proportions, and whimsical watercolor + classical Chinese shuimo ink-and-wash style "
                        f"from the attached reference images: {prompts.HERITAGE_CHARACTER}. "
                        "Draw a single unified scene (NOT a 2x2 grid or collage) with at most ONE Mei, ONE Ming, and "
                        "ONE Baobao in the scene (NEVER duplicate or clone any character twice in the same image, and NEVER include Lily or any robot). "
                        + prompt
                    )
                )
            )
        elif ref_list and is_lily_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT BOOK 1 LILY CHARACTER MATCH REQUIREMENT (SOLO LILY SCENE — NO ROBOT): "
                        "Match the exact character design, facial proportions, and whimsical watercolor and "
                        "colored-pencil style of Lily from the attached Book 1 reference image: Lily (6yo girl, "
                        "warm chestnut-brown wavy hair with soft bangs styled in two high-side fluffy curly "
                        "pigtail buns/puffs tied with bright red ribbon bows PLUS soft wavy shoulder-length "
                        "curls framing her face and neck—NEVER braided, NEVER a single ponytail—wearing a short "
                        "puff-sleeved buttercup-yellow polka-dot A-line dress with a crisp white rounded Peter "
                        "Pan collar and three small front bodice buttons, sky-blue backpack, white ankle socks, "
                        "and bright red Mary Jane strap shoes). Draw a single unified scene with ONLY ONE Lily "
                        "and DO NOT include Robo or any robot in this frame. "
                        + prompt
                    )
                )
            )
        elif ref_list and is_robo_only and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT BOOK 1 ROBO CHARACTER MATCH REQUIREMENT (SOLO ROBO SCENE — NO HUMAN GIRL): "
                        "Match the exact character design and whimsical watercolor and colored-pencil style of "
                        "Robo from the attached Book 1 reference image: Robo (single round spherical sky-blue "
                        "metallic robot with a horizontal equator seam line, his face drawn DIRECTLY on his "
                        "smooth blue spherical metal body—NEVER a dark black screen or recessed visor cutout—"
                        "featuring two large expressive round cartoon eyes with white outer sclera rings and "
                        "deep cobalt-blue irises, two small curved eyebrows, soft rosy cheek blush ovals, a "
                        "simple happy curved U-shaped line smile, a single slender top antenna stalk on top of "
                        "his head tipped with a glowing orb ball, circular side ear-hubs, short segmented "
                        "tubular blue arms with rounded pincer hands, and short segmented cylindrical blue legs "
                        "with dome feet). Draw a single unified scene with ONLY ONE Robo and DO NOT include "
                        "Lily or any human girl in this frame. "
                        + prompt
                    )
                )
            )
        elif ref_list and reference_images is None:
            contents.append(
                types.Part.from_text(
                    text=(
                        "STRICT BOOK 1 CHARACTER MATCH REQUIREMENT: Match the exact character designs, "
                        "facial proportions, and whimsical watercolor and colored-pencil style from the attached "
                        "Book 1 reference images: Lily (6yo girl, warm chestnut-brown wavy hair with soft bangs "
                        "styled in two high-side fluffy curly pigtail buns/puffs tied with bright red ribbon bows "
                        "PLUS soft wavy shoulder-length curls framing her face and neck—NEVER braided, NEVER a "
                        "single ponytail—wearing a short puff-sleeved buttercup-yellow polka-dot A-line dress with "
                        "a crisp white rounded Peter Pan collar and three small front bodice buttons, sky-blue "
                        "backpack, white ankle socks, and bright red Mary Jane strap shoes) and Robo (single round "
                        "spherical sky-blue metallic robot with a horizontal equator seam line, his face drawn "
                        "DIRECTLY on his smooth blue spherical metal body—NEVER a dark black screen or recessed "
                        "visor cutout—featuring two large expressive round cartoon eyes with white outer sclera "
                        "rings and deep cobalt-blue irises, two small curved eyebrows, soft rosy cheek blush ovals, "
                        "a simple happy curved U-shaped line smile, a single slender top antenna stalk on top of "
                        "his head tipped with a glowing orb ball, circular side ear-hubs, short segmented tubular "
                        "blue arms with rounded pincer hands, and short segmented cylindrical blue legs with dome "
                        "feet). Draw a single unified scene (NOT a 2x2 grid or collage) with at most ONE Lily and "
                        "ONE Robo in the scene (NEVER duplicate or clone Lily or Robo twice in the same image). "
                        + prompt
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
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
    ) -> dict[str, Any]:
        """Uses Gemini Vision to audit a raw generated page image against canonical reference sheets.

        Checks for:
        1. Accidental baked-in text, titles, banners, or watermarks.
        2. Character consistency and character presence mode for either STEM (Lily & Robo) or Chinese Heritage (Mei, Ming & Baobao).
        """
        client = self._get_client()
        is_heritage = prompts.is_heritage_genre(character_ids=character_ids, genre=genre)
        char_mode = prompts.detect_page_character_mode(
            scene_description,
            features_char=features_char,
            character_ids=character_ids,
            genre="chinese_heritage" if is_heritage else genre,
        )
        if is_heritage:
            qa_prompt = f"""
            You are a strict visual quality assurance inspector for the children's picture book series
            "{prompts.SERIES_BRAND}: {prompts.HERITAGE_SERIES_NAME}".
            Inspect the candidate raw illustration for Page {page_num} (before programmatic text overlay)
            and compare it against the canonical Mei, Ming & Baobao character invariants.

            Intended visual scene: {scene_description}
            Expected character presence mode: {char_mode} ('both' = Mei, Ming, and Baobao together; 'mei_only' = only Mei; 'ming_only' = only Ming; 'baobao_only' = only Baobao with no children; 'none' = pure world/object scene with no characters)

            Count characters precisely: set `lily_count` to the number of HUMAN CHILDREN (Mei and/or Ming) visible in the candidate image, and `robo_count` to the number of red pandas (Baobao) visible.

            Evaluate strictly against these rules:
            1. `has_unwanted_text`: Is there ANY visible text, words, letters, book titles, speech bubbles with text, banners with text, or watermarks rendered inside the candidate illustration? (Must be False for the image to pass).
            2. `lily_off_model`: Use this boolean to report if Mei or Ming is off-model (e.g. Mei missing her two top-side double-buns with red silk ribbons and vermilion-red tangzhuang tunic over cream skirt, Ming missing his jade-green sleeveless tangzhuang vest over cream shirt and navy shorts, extra hands >2, or duplicated/cloned characters). (Must be False).
            3. `robo_off_model`: Use this boolean to report if Baobao the tiny chubby red-panda spirit guide is off-model (e.g. drawn as a black-and-white giant panda or a metallic robot instead of a tiny cinnamon-rust red panda with a striped tail and celadon-jade cloud pendant, or duplicated/cloned). (Must be False).
            4. `lily_extra_hands`: Do Mei or Ming have more than 2 hands/arms total? (Must be False).
            5. Set `passed` to True ONLY if `has_unwanted_text` is False AND `robo_off_model` is False AND `lily_off_model` is False AND `lily_extra_hands` is False.
            """
        else:
            qa_prompt = f"""
            You are a strict visual quality assurance inspector for the children's picture book series
            "{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}".
            Inspect the candidate raw illustration for Page {page_num} (before programmatic text overlay)
            and compare it directly against the canonical Book 1 ("LILY & ROBO: DAY & NIGHT ADVENTURES") character reference.

            Intended visual scene: {scene_description}
            Expected character presence mode: {char_mode} ('both' = Lily and Robo together; 'lily_only' = only Lily, 0 Robo; 'robo_only' = only Robo, 0 Lily; 'none' = pure world/object scene with 0 Lily and 0 Robo)

            Evaluate strictly against these rules:
            1. `has_unwanted_text`: Is there ANY visible text, words, letters, book titles, speech bubbles with text, banners with text, or watermarks rendered inside the candidate illustration? (Must be False for the image to pass).
            2. `lily_count`: Count how many times the girl Lily appears in the candidate image (0, 1, or >1).
            3. `robo_count`: Count how many times the blue spherical robot Robo appears in the candidate image (0, 1, or >1).
            4. `robo_off_model`: If Robo appears in the scene, does he deviate from Book 1 by having a dark black/navy monitor screen or recessed faceplate cutout instead of his face drawn directly on his round sky-blue spherical body with large white-and-blue cartoon eyes and a curved line smile, OR having wheels, tank tracks, a boxy square torso, OR appearing when `char_mode` is 'none' or 'lily_only', OR appearing duplicated/cloned more than once? (Must be False).
            5. `lily_off_model`: If Lily appears in the scene, does she deviate from Book 1 by having braided pigtails, only a single ponytail, missing her yellow polka-dot A-line dress with white Peter Pan collar, having extra hands (>2 hands) or disembodied hands on her blue backpack straps, OR appearing when `char_mode` is 'none' or 'robo_only', OR appearing duplicated/cloned more than once? (Must be False).
            6. `lily_extra_hands`: Does Lily have more than 2 hands/arms total OR disembodied hands gripping her sky-blue backpack shoulder straps? (Must be False — her blue backpack straps must be smooth plain blue fabric).
            7. Set `passed` to True ONLY if `has_unwanted_text` is False AND `robo_off_model` is False AND `lily_off_model` is False AND `lily_extra_hands` is False.
            """
        try:
            qa_contents: list[Any] = []
            if is_heritage:
                solo_ref = HERITAGE_SOLO_REFS.get(char_mode)
                solo_bytes = (
                    read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / solo_ref)
                    if solo_ref
                    else None
                )
                if solo_bytes is not None:
                    ref_bytes = solo_bytes
                elif char_mode == "none":
                    ref_bytes = None
                else:
                    ref_imgs = self._load_character_reference_images(character_ids, genre="chinese_heritage")
                    ref_bytes = ref_imgs[0] if ref_imgs else None
            elif char_mode == "lily_only":
                lily_bytes = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "lily_book1_ref.png")
                ref_bytes = lily_bytes if lily_bytes is not None else self._load_reference_sheet_bytes()
            elif char_mode == "robo_only":
                robo_bytes = read_file_bytes_cached(config.PROJECT_ROOT / "assets" / "characters" / "robo_book1_ref.png")
                ref_bytes = robo_bytes if robo_bytes is not None else self._load_reference_sheet_bytes()
            elif char_mode == "both":
                ref_bytes = self._load_reference_sheet_bytes()
            else:
                ref_bytes = None
            if ref_bytes:
                qa_contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))
            qa_contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
            qa_contents.append(types.Part.from_text(text=qa_prompt))

            response = client.models.generate_content(
                model=config.VISION_QA_MODEL,
                contents=qa_contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VisualQAResult,
                    temperature=0.1,
                ),
            )
            result = json.loads(response.text)
            if not isinstance(result, dict):
                return {"passed": True, "score": 1.0, "issues": []}
            # Enforce boolean + count invariant logic
            has_text = bool(result.get("has_unwanted_text", False))
            robo_bad = bool(result.get("robo_off_model", False))
            lily_extra = bool(result.get("lily_extra_hands", False))
            lily_bad = bool(result.get("lily_off_model", False)) or lily_extra
            issues = list(result.get("issues", []) or [])
            if is_heritage:
                max_children, max_pandas = HERITAGE_PRESENCE_LIMITS.get(char_mode, (2, 1))
                children = result.get("lily_count")
                pandas = result.get("robo_count")
                children = max_children if children is None else int(children)
                pandas = max_pandas if pandas is None else int(pandas)
                if lily_extra:
                    issues.append("Character has extra hands (>2 hands)")
                if children > max_children:
                    lily_bad = True
                    issues.append(
                        f"Presence mode '{char_mode}' allows at most {max_children} child(ren) (Mei/Ming); got {children}"
                    )
                if pandas > max_pandas:
                    robo_bad = True
                    issues.append(f"Presence mode '{char_mode}' allows at most {max_pandas} Baobao; got {pandas}")
                passed = bool(result.get("passed", True)) and not (has_text or robo_bad or lily_bad)
                result["passed"] = passed
                result["issues"] = issues
                return result
            default_lily = 0 if char_mode in ("none", "robo_only") else 1
            default_robo = 0 if char_mode in ("none", "lily_only") else 1
            lily_count = int(result.get("lily_count", default_lily) if result.get("lily_count") is not None else default_lily)
            robo_count = int(result.get("robo_count", default_robo) if result.get("robo_count") is not None else default_robo)
            if lily_extra:
                issues.append("Lily has extra hands (>2 hands) or disembodied hands on her blue backpack straps")
            if lily_count > 1:
                lily_bad = True
                issues.append(f"Duplicate Lily detected (lily_count={lily_count}); scene must contain at most 1 Lily")
            if robo_count > 1:
                robo_bad = True
                issues.append(f"Duplicate Robo detected (robo_count={robo_count}); scene must contain at most 1 Robo")
            if char_mode == "none" and (lily_count > 0 or robo_count > 0):
                lily_bad = lily_bad or (lily_count > 0)
                robo_bad = robo_bad or (robo_count > 0)
                issues.append(f"World-only page ('none') must have 0 Lily and 0 Robo (got lily={lily_count}, robo={robo_count})")
            elif char_mode == "lily_only" and robo_count > 0:
                robo_bad = True
                issues.append(f"Solo Lily page ('lily_only') must have 0 Robo (got robo={robo_count})")
            elif char_mode == "robo_only" and lily_count > 0:
                lily_bad = True
                issues.append(f"Solo Robo page ('robo_only') must have 0 Lily (got lily={lily_count})")
            passed = bool(result.get("passed", True)) and not (has_text or robo_bad or lily_bad)
            result["passed"] = passed
            result["issues"] = issues
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
                para_lines: list[str] = []
                current_line = ""
                for char in paragraph:
                    # Kinsoku: closing punctuation never starts a line; keep it at the end of the previous one.
                    if not current_line and para_lines and char in CJK_NO_LINE_START:
                        para_lines[-1] += char
                        continue
                    current_line += char
                    if len(current_line) >= max_chars_cjk:
                        para_lines.append(current_line)
                        current_line = ""
                if current_line:
                    para_lines.append(current_line)
                lines.extend(para_lines)
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
        text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
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
                "Cantonese": "完",
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
            y = int(height * 0.86)
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
            folder_index=self._drive_folder_indices.get(folder_id),
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
            folder_index=self._drive_folder_indices.get(folder_id),
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
            folder_index=self._drive_folder_indices.get(folder_id),
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
            folder_index=self._drive_folder_indices.get(folder_id),
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
            folder_index=self._drive_folder_indices.get(folder_id),
        )

    @staticmethod
    def compile_pdf_bytes(
        pil_images: list[Image.Image],
        dpi: float = config.PDF_DPI,
        upscale_for_print: bool = False,
    ) -> bytes:
        """Compiles PIL images into a print-ready PDF with proper DPI metadata (default 300 DPI)."""
        return epub_packager.compile_pdf_bytes(pil_images, dpi=dpi, upscale_for_print=upscale_for_print)

    def create_epub(self, title: str, pages_data: list, language: str) -> bytes:
        """Creates a valid EPUB 3.0 fixed-layout eBook with nav document, dcterms:modified, and XML escaping."""
        return epub_packager.create_epub(title, pages_data, language=language, author=prompts.SERIES_AUTHOR)

    @staticmethod
    def validate_epub_bytes(epub_bytes: bytes, epub_path: Optional[str] = None) -> dict[str, Any]:
        """Validates EPUB 3 package invariants and optionally runs `epubcheck` CLI if available."""
        return epub_packager.validate_epub_bytes(epub_bytes, epub_path=epub_path)

    def generate_kdp_description(
        self, title: str, topic: str, age_group: str, language: str, genre: Optional[str] = None
    ) -> str:
        """Generates a professional Amazon KDP book description in the target language and series genre."""
        logger.info(f"Generating Amazon KDP description in {language}...")
        if prompts.is_heritage_genre(topic=topic, genre=genre):
            prompt = f"""
        You are a professional children's book publisher and marketer for the "{prompts.SERIES_BRAND}" imprint.
        Write a highly engaging, catchy, and professional Amazon KDP (Kindle Direct Publishing) book description for the following picture book:

        Series: "{prompts.SERIES_BRAND}: {prompts.HERITAGE_SERIES_NAME}"
        Title: "{title}"
        Topic: "{topic}" (written for ages {age_group})
        Author: {prompts.SERIES_AUTHOR}
        Language: {language}

        The description MUST be written entirely in {language}.
        Include:
        1. A catchy headline hook introducing the {prompts.SERIES_BRAND} Chinese Proverb Tales.
        2. A heartwarming story synopsis starring curious Mei, her playful little brother Ming, and their red-panda spirit guide Baobao.
        3. The classic Chinese proverb (chengyu) at the heart of the story (characters, Pinyin, and meaning) and the timeless value it teaches, in playful, intuitive terms.
        4. "Why Parents & Educators Love Curious Little Minds" (3 key bullet points on cultural heritage, character values like patience and perseverance, and family read-aloud bonding).
        5. Series callout inviting readers to collect all books in the {prompts.SERIES_BRAND} series.
        6. Listing metadata (suggested age group 4-6, preschool to kindergarten grade levels, and 7 high-converting search keywords/tags).

        Keep the tone warm, welcoming, inspirational, and professional.
        """
        else:
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

    def validate_and_correct_story(
        self,
        story_json: dict,
        topic: str,
        language: str,
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
    ) -> dict:
        """Validates the storybook structure, details, prompts, and automatically corrects errors."""
        logger.info("Running storybook structure validation (Validation Agent Skill)...")

        is_heritage = prompts.is_heritage_genre(
            topic=topic,
            character_ids=character_ids,
            genre=genre or story_json.get("genre"),
        )
        if is_heritage:
            story_json["genre"] = "chinese_heritage"
            story_json["series"] = f"{prompts.SERIES_BRAND}: {prompts.HERITAGE_SERIES_NAME}"
        else:
            story_json["series"] = f"{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}"
        story_json["author"] = prompts.SERIES_AUTHOR

        pages = story_json.get("pages", [])
        total_pages = len(pages)

        registered_names = {
            str(c.get("name", "")).strip().lower()
            for c in config.get_active_characters(character_ids)
            if c.get("name")
        }

        if not is_heritage:
            # Failsafe character/robot name overrides for STEM books while preserving active guest characters
            for page in pages:
                text_val = page.get("text", "")
                scene_val = page.get("scene_description", "")

                for name in ["luna", "dottie", "lele", "xiao lele"]:
                    if name in registered_names:
                        continue
                    text_val = re.sub(rf"\b{name}\b", "Lily", text_val, flags=re.IGNORECASE)
                    scene_val = re.sub(rf"\b{name}\b", "Lily", scene_val, flags=re.IGNORECASE)

                for rname in ["chip", "clic", "pip", "clik", "dotti"]:
                    if rname in registered_names:
                        continue
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

        # 2. Enforce word budget on Page 19 (STEM / Proverb Wisdom Corner) so the text card stays compact
        for page in pages:
            if page.get("page_number") == 19:
                words = page.get("text", "").split()
                if len(words) > 65:
                    logger.info(
                        f"Trimming Page 19 Corner from {len(words)} words to 60 words to prevent card overflow."
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
        default_char = prompts.HERITAGE_CHARACTER if is_heritage else prompts.SERIES_CHARACTER
        default_style = prompts.HERITAGE_STYLE if is_heritage else prompts.SERIES_STYLE
        main_char = story_json.get("main_character_description", "").strip() or default_char
        style = story_json.get("artistic_style", "").strip() or default_style

        for page in pages:
            scene = page.get("scene_description", "").strip()
            features_char = page.get("features_main_character", True)

            prompt = prompts.construct_image_prompt(
                page_scene=scene,
                main_char_desc=main_char,
                style_desc=style,
                features_char=features_char,
                character_ids=character_ids,
                genre="chinese_heritage" if is_heritage else None,
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

        is_heritage = (
            story_json.get("genre") == "chinese_heritage"
            or "Mei (" in story_json.get("main_character_description", "")
        )
        char_translation_rule = (
            "4. The main characters' names 'Mei', 'Ming', and 'Baobao' must be preserved as 'Mei', 'Ming', and 'Baobao' (or translated naturally in Chinese as '美美', '明明', and '宝宝'; in Japanese as 'メイ', 'ミン', and 'バオバオ'; in Korean as '메이', '밍', and '바오바오'). On Page 19 (Proverb Wisdom & Heritage Corner), preserve the Chinese proverb characters and Pinyin alongside the translated explanation."
            if is_heritage
            else "4. The main character girl's name 'Lily' must be preserved as 'Lily' (or translated phonetically, e.g., '莉莉' in Chinese, '릴리' in Korean, 'リリー' in Japanese). The companion robot's name 'Robo' must remain 'Robo' (or translated to 'Robo' / 'Helper-Bot' equivalent in the target language). Do NOT change them to other names (like 'Luna' or 'Leo')."
        )
        cantonese_rule = ""
        if target_language.lower().startswith("cantonese"):
            cantonese_rule = (
                "5. Write natural, colloquial Hong Kong Cantonese (粵語白話文) in Traditional Chinese characters, exactly as a "
                "Hong Kong parent would read it aloud (e.g. 佢、佢哋、嘅、咗、喺、啲、冇、唔、嚟、睇、畀). Do NOT write Mandarin or "
                "formal written Chinese (書面語), and never use Simplified characters. Use only common characters (avoid rare "
                "or extension characters such as 㗎). Keep sound effects playful. The cover byline is '作者：Enrique Chan' and "
                "the closing word on the final page is '完'."
            )
            zh_ref = {
                "title": story_json.get("title_zh", ""),
                "pages": [
                    {"page_number": p.get("page_number"), "text": p.get("text_zh", "")}
                    for p in story_json.get("pages", [])
                    if p.get("text_zh")
                ],
            }
            if zh_ref["pages"]:
                cantonese_rule += (
                    "\n        6. Stay consistent with this book's Mandarin edition (Simplified Chinese, below): reuse its character "
                    "names and invented terms converted to Traditional characters (e.g. if it calls Robo 乐乐, write 樂樂). "
                    "Translate from the English, not from the Mandarin.\n        Mandarin edition JSON: "
                    + json.dumps(zh_ref, ensure_ascii=False)
                )
        prompt = f"""
        You are a professional children's book translator.
        Translate the following children's book title and page texts from English into {target_language}.
        
        Ensure that:
        1. The translation matches the tone and simplicity appropriate for young children (ages 4-6).
        2. Line breaks (\\n) in the text are preserved where appropriate (especially on the cover page to separate the title from 'Written by Enrique Chan').
        3. On the final page, ensure the closing text translates to the correct closing word for {target_language} (e.g. 'Fin' for Spanish/French, 'Fim' for Portuguese, 'Fine' for Italian, 'Ende' for German, 'おしまい' for Japanese, '끝' for Korean).
        {char_translation_rule}
        {cantonese_rule}
        
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

    def ensure_multilingual_translations(
        self,
        story_json: dict,
        local_dir: Optional[str] = None,
        target_langs: Optional[list[str]] = None,
    ) -> dict:
        """Ensures requested non-English translations (`text_<lang>`) are populated in `story_json`."""
        langs = target_langs or ["es", "zh", "yue"]
        pages = story_json.get("pages", [])
        updated = False

        lang_specs = {
            "es": ("Spanish", "Escrito por Enrique Chan", "El fin"),
            "zh": ("Chinese Simplified", "作者：Enrique Chan", "终"),
            "yue": ("Cantonese", "作者：Enrique Chan", "完"),
            "pt": ("Portuguese", "Escrito por Enrique Chan", "Fim"),
            "ja": ("Japanese", "作：Enrique Chan", "おしまい"),
            "ko": ("Korean", "글: Enrique Chan", "끝"),
            "fr": ("French", "Écrit par Enrique Chan", "Fin"),
            "it": ("Italian", "Scritto da Enrique Chan", "Fine"),
            "de": ("German", "Geschrieben von Enrique Chan", "Ende"),
        }

        def _needs_translation(lang_code: str) -> bool:
            if lang_code not in lang_specs:
                return False
            title_key = f"title_{lang_code}"
            text_key = f"text_{lang_code}"
            return not story_json.get(title_key) or not all(p.get(text_key) for p in pages)

        def _apply_translated_story(lang_code: str, tr_story: dict) -> None:
            nonlocal updated
            lang_name, byline, closing_word = lang_specs[lang_code]
            title_key = f"title_{lang_code}"
            text_key = f"text_{lang_code}"
            loc_title = tr_story.get("title") or story_json.get("title", "")
            story_json[title_key] = loc_title
            tr_map = {
                int(p.get("page_number", 1)): p.get("text", "")
                for p in tr_story.get("pages", [])
            }
            for p in pages:
                p_num = int(p.get("page_number", 1))
                if p_num == 1:
                    p[text_key] = f"{loc_title}\n{byline}"
                elif (
                    p_num == len(pages)
                    and len(pages) > 1
                    and p.get("text", "").strip().lower() == "the end"
                ):
                    p[text_key] = closing_word
                elif p_num in tr_map and tr_map[p_num]:
                    p[text_key] = tr_map[p_num]
            updated = True
            logger.info(f"Populated {lang_name} translation ('{loc_title}') in story.json")

        # Run independent languages concurrently first so Mandarin (`zh`) is ready before Cantonese (`yue`).
        primary_langs = [lc for lc in langs if lc != "yue" and _needs_translation(lc)]
        if len(primary_langs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(primary_langs))) as pool:
                futs = {
                    lc: pool.submit(self.translate_story, story_json, lang_specs[lc][0])
                    for lc in primary_langs
                }
                for lc in primary_langs:
                    lang_name = lang_specs[lc][0]
                    try:
                        _apply_translated_story(lc, futs[lc].result())
                    except Exception as e:
                        logger.warning(f"{lang_name} translation skipped/failed: {e}")
        else:
            for lc in primary_langs:
                lang_name = lang_specs[lc][0]
                try:
                    _apply_translated_story(lc, self.translate_story(story_json, lang_name))
                except Exception as e:
                    logger.warning(f"{lang_name} translation skipped/failed: {e}")

        if "yue" in langs and _needs_translation("yue"):
            lang_name = lang_specs["yue"][0]
            try:
                _apply_translated_story("yue", self.translate_story(story_json, lang_name))
            except Exception as e:
                logger.warning(f"{lang_name} translation skipped/failed: {e}")

        if updated and local_dir:
            try:
                story_path = os.path.join(local_dir, "story.json")
                with open(story_path, "w", encoding="utf-8") as f:
                    json.dump(story_json, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"Failed to persist multilingual story.json: {e}")

        return story_json

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
            t = p.get("text_zh") or p.get("text", "")
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
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
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
                with open(hash_path, encoding="utf-8") as hf:
                    cached_hash = hf.read().strip()
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
                    char_mode = prompts.detect_page_character_mode(
                        scene_desc, features_char=features_char, character_ids=character_ids, genre=genre
                    )
                    if char_mode == "none":
                        prompt_for_attempt = (
                            f"{img_prompt} CRITICAL CORRECTION: Do NOT render any words, letters, titles, "
                            "speech bubbles, or banners anywhere in the image. This is a PURE WORLD & OBJECT SCENE: "
                            "do NOT draw any human, animal, or robot characters in this illustration—show ONLY the "
                            "environment and objects described."
                        )
                    elif prompts.is_heritage_genre(character_ids=character_ids, genre=genre):
                        prompt_for_attempt = (
                            f"{img_prompt} CRITICAL CHINESE HERITAGE CORRECTION: Do NOT render any words, letters, titles, "
                            "speech bubbles, or banners anywhere in the image. Match the attached canonical reference sheets "
                            f"for Mei, Ming, and Baobao ({prompts.HERITAGE_CHARACTER}) with at most 1 of each character."
                        )
                    elif char_mode == "lily_only":
                        prompt_for_attempt = (
                            f"{img_prompt} CRITICAL BOOK 1 CORRECTION: Do NOT render any words, letters, titles, "
                            "speech bubbles, or banners anywhere in the image. Show ONLY ONE Lily (NO Robo or any "
                            "robot in this scene), matching the attached Book 1 Lily reference."
                        )
                    elif char_mode == "robo_only":
                        prompt_for_attempt = (
                            f"{img_prompt} CRITICAL BOOK 1 CORRECTION: Do NOT render any words, letters, titles, "
                            "speech bubbles, or banners anywhere in the image. Show ONLY ONE Robo (NO Lily or any "
                            "human girl in this scene), matching the attached Book 1 Robo reference."
                        )
                    else:
                        prompt_for_attempt = (
                            f"{img_prompt} CRITICAL BOOK 1 CORRECTION: Do NOT render any words, letters, titles, "
                            "speech bubbles, or banners anywhere in the image. Match the attached Book 1 reference "
                            "sheet: Lily MUST have warm chestnut-brown wavy hair with soft bangs styled in two "
                            "high-side fluffy curly pigtail puffs tied with bright red ribbon bows PLUS soft wavy "
                            "shoulder-length curls framing her face, a buttercup-yellow polka-dot A-line dress with "
                            "a crisp white Peter Pan collar and three front buttons, sky-blue backpack, white ankle "
                            "socks, and bright red Mary Jane strap shoes; Robo MUST be a single round spherical "
                            "sky-blue metallic robot with a horizontal equator seam line, his face drawn DIRECTLY on "
                            "his smooth blue spherical metal body (NEVER a black screen or recessed visor cutout), "
                            "two large round white-and-cobalt-blue cartoon eyes with small curved eyebrows, a simple "
                            "happy curved line smile, rosy blush cheeks, and a single slender top antenna stalk on "
                            "top of his head tipped with a glowing orb ball."
                        )
                if character_ids:
                    raw_img = self.generate_page_image(prompt_for_attempt, character_ids=character_ids)
                else:
                    raw_img = self.generate_page_image(prompt_for_attempt)
                if enable_visual_qa:
                    qa_kwargs: dict[str, Any] = {
                        "page_num": page_num,
                        "scene_description": scene_desc,
                        "features_char": features_char,
                    }
                    if character_ids:
                        qa_kwargs["character_ids"] = character_ids
                    if genre:
                        qa_kwargs["genre"] = genre
                    qa_report = self.evaluate_page_image_qa(raw_img, **qa_kwargs)
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
        export_vids: bool = True,
        character_ids: Optional[list[str]] = None,
        genre: Optional[str] = None,
    ) -> dict:
        """Runs the fast, streamlined publishing pipeline with multilingual Google Vids & Chinese MP3 audio.

        Supports:
        - Resume from cached `story.json` and raw page illustrations (`resume=True`)
        - Parallel page illustration generation (`max_workers > 1`)
        - Per-page Gemini Vision QA gate with automatic regeneration (`enable_visual_qa=True`)
        - Valid EPUB 3.0 + 300 DPI PDF + KDP metadata kit + `manifest.json`
        - Automatic Spanish (`es`) & Mandarin Chinese (`zh`) translation and 16:9 Google Vids deck export
        - Canonical Book 1 character reference conditioning & extensible guest character registry
        """
        workflow_start = time.monotonic()
        logger.info(f"=== Starting Fast Publishing Workflow for topic: '{topic}' ===")
        is_heritage = prompts.is_heritage_genre(topic=topic, character_ids=character_ids, genre=genre)
        if is_heritage and not character_ids:
            character_ids = ["mei", "ming", "baobao"]

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
            if character_ids or genre:
                story = self.generate_story(
                    topic, age_group, language=language, character_ids=character_ids, genre=genre
                )
            else:
                story = self.generate_story(topic, age_group, language=language)

        if override_char:
            story["main_character_description"] = override_char
        if override_style:
            story["artistic_style"] = override_style

        if character_ids or genre:
            story = self.validate_and_correct_story(
                story, topic, language=language, character_ids=character_ids, genre=genre
            )
            for cid in character_ids or []:
                if cid.lower() not in ("lily", "robo"):
                    try:
                        self.ensure_character_reference_image(cid)
                    except Exception as e:
                        logger.warning(f"Could not auto-generate reference image for '{cid}': {e}")
            if is_heritage:
                try:
                    self.ensure_heritage_reference_sheet()
                except Exception as e:
                    logger.warning(f"Could not compose the Chinese Heritage master reference sheet: {e}")
        else:
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
        folder_index: Optional[dict[str, dict[str, Any]]] = None
        if self.drive_service:
            try:
                drive_folder_name = (
                    os.path.basename(os.path.normpath(output_dir))
                    if (output_dir and parent_id)
                    else f"{title} (Ages {age_group})"
                )
                folder_id = self.create_drive_folder(drive_folder_name, parent_id=parent_id)
                if folder_id:
                    folder_index = drive_sync.list_folder_index(self.drive_service, folder_id)
                    self._drive_folder_indices[folder_id] = folder_index
                logger.info(f"Created Google Drive folder (ID: {folder_id})")
            except Exception as e:
                logger.warning(f"Could not create Google Drive folder: {e}. Output will be saved locally.")

        # 3. Generate illustrations and overlay text (sequential when max_workers==1, parallel when >1)
        page_results: dict[int, dict[str, Any]] = {}
        pages_failed: list[dict[str, Any]] = []
        total_pages = len(pages)
        effective_genre = "chinese_heritage" if is_heritage else genre

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
                        config.MAX_QA_RETRIES,
                        character_ids,
                        effective_genre,
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
                        character_ids=character_ids,
                        genre=effective_genre,
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

            with Image.open(io.BytesIO(final_img)) as opened_img:
                pil_images.append(opened_img.convert("RGB"))
            pages_data.append((img_filename, final_img))

        # 4. Compile and save combined 300 DPI PDF
        pdf_path = None
        if pil_images:
            logger.info("Compiling all pages into a combined PDF manuscript...")
            try:
                pdf_bytes = self.compile_pdf_bytes(pil_images, dpi=config.PDF_DPI, upscale_for_print=upscale_pdf)
            finally:
                for p_im in pil_images:
                    p_im.close()
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
        kdp_path = os.path.join(local_dir, "kdp_description.txt")
        kdp_desc = ""
        try:
            if resume and os.path.exists(kdp_path) and os.path.getsize(kdp_path) > 0:
                with open(kdp_path, encoding="utf-8") as f:
                    kdp_desc = f.read()
                logger.info(f"Resumed existing kdp_description.txt from '{local_dir}'")
            else:
                kdp_desc = self.generate_kdp_description(
                    title, topic, age_group, language=language, genre="chinese_heritage" if is_heritage else None
                )
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
        kdp_metadata = build_kdp_metadata(
            title=title,
            topic=topic,
            language=language,
            age_group=age_group,
            back_cover_blurb=story.get("back_cover_blurb", ""),
            genre="chinese_heritage" if is_heritage else None,
        )
        try:
            with open(kdp_metadata_path, "w", encoding="utf-8") as f:
                json.dump(kdp_metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write kdp_metadata.json: {e}")

        # 6.5. Ensure Spanish (es) and Mandarin Chinese (zh) translations are populated in story.json
        if generate_audio or export_vids:
            story = self.ensure_multilingual_translations(story, local_dir=local_dir)

        # 7. Generate Chinese Read-Aloud MP3 Audio
        audio_path = None
        if generate_audio:
            try:
                audio_filename = f"{safe_title}_Chinese_Read_Aloud.mp3"
                audio_path = os.path.join(local_dir, audio_filename)
                if resume and os.path.exists(audio_path) and os.path.getsize(audio_path) > 10000:
                    logger.info(f"Resumed existing Chinese audio MP3: '{audio_path}'")
                else:
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

        # 8. Automatically export 16:9 Google Vids decks (.pptx + voiceover scripts) & localized editions (EN, ES, ZH)
        vids_export_result = None
        if export_vids and len(pages_failed) == 0 and len(pages_data) == len(pages):
            try:
                import vids_exporter

                vids_export_result = vids_exporter.export_book_for_google_vids(
                    book_dir=local_dir,
                    languages=["en", "es", "zh", "yue"],
                    drive_service=self.drive_service,
                    drive_root_id=parent_id,
                    drive_book_folder_id=folder_id,
                    compile_localized_books=True,
                )
            except Exception as e:
                logger.error(f"Failed to export Google Vids decks for '{title}': {e}")

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
            "google_vids": vids_export_result,
        }
        manifest_path = os.path.join(local_dir, "manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write manifest.json: {e}")

        # 9. Sync localized PDFs, EPUBs, and metadata files to Google Drive if connected
        if folder_id and self.drive_service:
            try:
                for meta_file in (story_json_path, manifest_path, kdp_metadata_path, kdp_path):
                    if meta_file and os.path.exists(meta_file):
                        drive_sync.upsert_local_file(
                            self.drive_service, meta_file, folder_id, folder_index=folder_index
                        )
                if vids_export_result:
                    for _, l_pdf in (vids_export_result.get("localized_pdfs") or {}).items():
                        if os.path.exists(l_pdf):
                            drive_sync.upsert_local_file(
                                self.drive_service, l_pdf, folder_id, folder_index=folder_index
                            )
                    for _, l_epub in (vids_export_result.get("localized_epubs") or {}).items():
                        if os.path.exists(l_epub):
                            drive_sync.upsert_local_file(
                                self.drive_service, l_epub, folder_id, folder_index=folder_index
                            )
            except Exception as e:
                logger.warning(f"Localized asset Drive upload skipped: {e}")
            finally:
                self._drive_folder_indices.pop(folder_id, None)

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
            "google_vids": vids_export_result,
        }
