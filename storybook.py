import os
import io
import json
import logging
from google.genai import Client
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
import google.auth
import prompts

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

class StorybookGenerator:
    """Generates kid-friendly stories and overlayed cover/page art, exporting to Google Drive."""
    
    def __init__(self, drive_service):
        self.drive_service = drive_service
        
        # Configure Vertex AI environment
        try:
            _, project_id = google.auth.default()
            os.environ["GOOGLE_CLOUD_PROJECT"] = project_id or "project-maui"
        except Exception:
            os.environ["GOOGLE_CLOUD_PROJECT"] = "project-maui"

        os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
        
        # Initialize Google GenAI client using application default credentials (which uses ADC)
        self.ai_client = Client()
        
    def generate_story(self, topic: str, age_group: str, language: str = "English") -> dict:
        """Generates a structured kid-friendly story outline and pages."""
        prompt = prompts.get_story_generation_prompt(topic, age_group, language)
        logger.info(f"Generating story for topic: '{topic}', age group: '{age_group}' using Gemini 2.5 Flash...")
        client = Client(vertexai=True, project="project-maui", location="us-central1")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            )
        )
        story_json = json.loads(response.text)
        
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
        
    def generate_page_image(self, prompt: str) -> bytes:
        """Generates a single page illustration using gemini-2.5-flash-image (Nano Banana 2)."""
        logger.info(f"Generating image with prompt: '{prompt}'...")
        client = Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[types.Part.from_text(text=prompt)],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="1:1", # Standard square book page format
                )
            )
        )
        
        # Extract the image bytes from inline data
        image_bytes = None
        if response.parts:
            for part in response.parts:
                if part.inline_data and part.inline_data.data:
                    image_bytes = part.inline_data.data
                    break
        
        if not image_bytes:
            raise Exception("No image was returned by the model.")
            
        return image_bytes

    def get_text_width(self, draw, text, font):
        """Helper to get text width safely across Pillow versions."""
        if hasattr(draw, "textlength"):
            return draw.textlength(text, font=font)
        elif hasattr(font, "getbbox"):
            return font.getbbox(text)[2]
        else:
            return draw.textsize(text, font=font)[0]

    def overlay_text_on_image(self, image_bytes: bytes, text: str, page_num: int, total_pages: int, language: str = "English") -> bytes:
        """Overlays text onto the generated image in a readable children's book format."""
        logger.info(f"Overlaying text on generated image for Page {page_num}...")
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
        
        # Try loading a font that supports multilingual characters (especially Chinese SC)
        font_path = None
        for path in [
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf"
        ]:
            if os.path.exists(path):
                font_path = path
                break
                
        # Determine layout type
        is_cover = (page_num == 1)
        is_last = (page_num == total_pages)
        
        # Load fonts at appropriate sizes
        font_size = int(height * 0.04)
        if is_last:
            font_size = int(height * 0.07) # Make "The End" larger
            
        try:
            font = ImageFont.truetype(font_path, size=font_size) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
            
        # Prepare text lines based on page type
        if is_cover:
            # On the cover, only display the author line (embedded under the image title)
            parts = text.split("\n")
            lines = [parts[-1]] if parts else [text]
        elif is_last:
            # On the last page, only display "The End" equivalent
            if language == "Spanish":
                lines = ["El fin"]
            elif language == "French":
                lines = ["Fin"]
            elif language == "Portuguese":
                lines = ["Fim"]
            elif language == "Italian":
                lines = ["Fine"]
            elif language == "German":
                lines = ["Ende"]
            elif language == "Chinese Simplified":
                lines = ["终"]
            elif language == "Japanese":
                lines = ["おしまい"]
            elif language == "Korean":
                lines = ["끝"]
            else:
                lines = ["The End"]
        else:
            # Regular pages: wrap text according to language
            has_asian = any(
                ('\u4e00' <= char <= '\u9fff') or  # Han (Chinese/Japanese)
                ('\u3040' <= char <= '\u309f') or  # Hiragana
                ('\u30a0' <= char <= '\u30ff') or  # Katakana
                ('\uac00' <= char <= '\ud7a3')      # Hangul (Korean)
                for char in text
            )
            lines = []
            if has_asian:
                current_line = ""
                for char in text:
                    current_line += char
                    if len(current_line) >= 22:
                        lines.append(current_line)
                        current_line = ""
                if current_line:
                    lines.append(current_line)
            else:
                words = text.split()
                current_line = []
                for word in words:
                    current_line.append(word)
                    if len(" ".join(current_line)) > 45:
                        current_line.pop()
                        lines.append(" ".join(current_line))
                        current_line = [word]
                if current_line:
                    lines.append(" ".join(current_line))
                    
        # Draw a semi-transparent background block at the bottom of the page ONLY for regular pages
        if not is_cover and not is_last:
            # Calculate dynamic height based on the number of wrapped lines
            line_height = int(font_size * 1.35)
            rect_height = len(lines) * line_height + int(height * 0.05)
            
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle(
                [(0, height - rect_height), (width, height)],
                fill=(255, 255, 255, 200) # White block with 78% opacity
            )
            # Composite overlay
            image = Image.alpha_composite(image.convert("RGBA"), overlay)
            
        draw = ImageDraw.Draw(image)
        
        # Position and draw lines
        if is_cover:
            # Center at the bottom, using a soft charcoal color with white outline for readability
            y_offset = int(height * 0.85)
            for line in lines:
                line_width = self.get_text_width(draw, line, font)
                x = (width - line_width) // 2
                draw.text((x, y_offset), line, fill=(44, 62, 80, 255), stroke_width=2, stroke_fill=(255, 255, 255, 255), font=font)
        elif is_last:
            # Center horizontally and vertically with a prominent size and outline
            line = lines[0]
            line_width = self.get_text_width(draw, line, font)
            x = (width - line_width) // 2
            y = (height - font_size) // 2
            draw.text((x, y), line, fill=(44, 62, 80, 255), stroke_width=3, stroke_fill=(255, 255, 255, 255), font=font)
        else:
            # Regular pages: Center horizontally within the white bottom block
            line_height = int(font_size * 1.35)
            y_offset = height - rect_height + int(height * 0.025)
            for line in lines:
                line_width = self.get_text_width(draw, line, font)
                x = (width - line_width) // 2
                draw.text((x, y_offset), line, fill=(44, 62, 80, 255), font=font)
                y_offset += line_height
                
        # Save composite image back to bytes
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG")
        return buffer.getvalue()

    def create_drive_folder(self, folder_name: str, parent_id: str = None) -> str:
        """Creates a folder in Google Drive or reuses an existing one with the same name."""
        # Escape single quotes in folder name
        escaped_name = folder_name.replace("'", "\\'")
        query = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
            
        try:
            results = self.drive_service.files().list(q=query, fields="files(id)").execute()
            files = results.get("files", [])
            if files:
                existing_id = files[0]["id"]
                logger.info(f"Reusing existing folder '{folder_name}' (ID: {existing_id})")
                return existing_id
        except Exception as e:
            logger.warning(f"Error checking folder existence: {e}")

        logger.info(f"Creating folder '{folder_name}' in Google Drive...")
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_id:
            file_metadata["parents"] = [parent_id]
            
        folder = self.drive_service.files().create(
            body=file_metadata, 
            fields="id"
        ).execute()
        return folder.get("id")

    def upload_image_to_drive(self, image_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads an image from bytes to a specific Google Drive folder."""
        logger.info(f"Uploading '{filename}' to Google Drive...")
        from googleapiclient.http import MediaIoBaseUpload
        
        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }
        media = MediaIoBaseUpload(
            io.BytesIO(image_bytes),
            mimetype="image/jpeg",
            resumable=True
        )
        file = self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink"
        ).execute()
        return file.get("id")

    def upload_pdf_to_drive(self, pdf_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads a PDF from bytes to a specific Google Drive folder."""
        logger.info(f"Uploading '{filename}' to Google Drive...")
        from googleapiclient.http import MediaIoBaseUpload
        
        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }
        media = MediaIoBaseUpload(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            resumable=True
        )
        file = self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()
        return file.get("id")

    def upload_epub_to_drive(self, epub_bytes: bytes, filename: str, folder_id: str) -> str:
        """Uploads an EPUB file to a specific Google Drive folder."""
        logger.info(f"Uploading '{filename}' to Google Drive...")
        from googleapiclient.http import MediaIoBaseUpload
        
        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }
        media = MediaIoBaseUpload(
            io.BytesIO(epub_bytes),
            mimetype="application/epub+zip",
            resumable=True
        )
        file = self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()
        return file.get("id")

    def create_epub(self, title: str, pages_data: list, language: str) -> bytes:
        """Creates a KDP-compatible fixed-layout EPUB from page images and returns the bytes."""
        logger.info(f"Creating KDP-compatible fixed-layout EPUB for '{title}'...")
        import zipfile
        import uuid
        
        epub_buf = io.BytesIO()
        book_id = str(uuid.uuid4())
        
        lang_codes = {
            "English": "en",
            "Spanish": "es",
            "Chinese Simplified": "zh",
            "French": "fr",
            "Portuguese": "pt"
        }
        lang_code = lang_codes.get(language, "en")
        
        with zipfile.ZipFile(epub_buf, "w", zipfile.ZIP_DEFLATED) as epub:
            # mimetype must be first and uncompressed
            epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            
            # META-INF/container.xml
            container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="http://www.oasis-open.org/names/tc/opendocument/xmlns/container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
            epub.writestr("META-INF/container.xml", container_xml)
            
            manifest_items = []
            spine_items = []
            
            for idx, (filename, img_bytes) in enumerate(pages_data):
                page_num = idx + 1
                img_href = f"images/{filename}"
                page_href = f"page_{page_num:02d}.xhtml"
                
                # Write page image
                epub.writestr(f"OEBPS/{img_href}", img_bytes)
                
                # Create XHTML page content (fixed-layout)
                page_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Page {page_num}</title>
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
                
                manifest_items.append(f'    <item id="page_{page_num:02d}" href="{page_href}" media-type="application/xhtml+xml"/>')
                manifest_items.append(f'    <item id="img_{page_num:02d}" href="{img_href}" media-type="image/jpeg"/>')
                spine_items.append(f'    <itemref idref="page_{page_num:02d}"/>')
                
            manifest_str = "\n".join(manifest_items)
            spine_str = "\n".join(spine_items)
            
            content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>Enrique Chan</dc:creator>
    <dc:language>{lang_code}</dc:language>
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

    def upload_txt_as_doc_to_drive(self, text_content: str, filename: str, folder_id: str) -> str:
        """Uploads plain text content to Google Drive and converts it into a Google Doc."""
        logger.info(f"Uploading '{filename}' as a Google Doc to Google Drive...")
        from googleapiclient.http import MediaIoBaseUpload
        
        file_metadata = {
            "name": filename,
            "mimeType": "application/vnd.google-apps.document", # Converts to Google Doc automatically
            "parents": [folder_id]
        }
        media = MediaIoBaseUpload(
            io.BytesIO(text_content.encode("utf-8")),
            mimetype="text/plain",
            resumable=True
        )
        file = self.drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()
        return file.get("id")

    def generate_kdp_description(self, title: str, topic: str, age_group: str, language: str) -> str:
        """Generates a professional Amazon KDP book description in the target language."""
        logger.info(f"Generating Amazon KDP description in {language}...")
        prompt = f"""
        You are a professional children's book publisher and marketer.
        Write a highly engaging, catchy, and professional Amazon KDP (Kindle Direct Publishing) book description for the following children's book:
        
        Title: "{title}"
        Topic: "{topic}" (written for ages {age_group})
        Author: Enrique Chan
        Language: {language}
        
        The description MUST be written entirely in {language}.
        Include:
        1. A catchy hook.
        2. A description of what the book teaches in a kid-friendly STEM context.
        3. Key selling points for parents and educators.
        4. Listing details (suggested age group, categories, and keywords/tags).
        
        Keep the tone warm, welcoming, and professional.
        """
        client = Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
            )
        )
        return response.text

    def validate_and_correct_story(self, story_json: dict, topic: str, language: str) -> dict:
        """Validates the storybook structure, details, prompts, and automatically corrects errors."""
        logger.info("Running storybook structure validation (Validation Agent Skill)...")
        
        pages = story_json.get("pages", [])
        total_pages = len(pages)
        
        # Failsafe character/robot name overrides to ensure consistency
        import re
        for page in pages:
            text_val = page.get("text", "")
            scene_val = page.get("scene_description", "")
            
            # Replace girl name variants (case-insensitively) with "Lily"
            for name in ["luna", "dottie", "lele", "xiao lele"]:
                text_val = re.sub(rf"\b{name}\b", "Lily", text_val, flags=re.IGNORECASE)
                scene_val = re.sub(rf"\b{name}\b", "Lily", scene_val, flags=re.IGNORECASE)
                
            # Replace robot name variants with "Robo" (or "Helper-Bot")
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
                logger.info(f"Corrected Cover Page text from '{cover_page.get('text')}' to expected author format.")
                cover_page["text"] = expected_author
                
        # 2. Enforce closing page
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
                
            if last_page.get("text") != expected_last:
                logger.info(f"Corrected Last Page text from '{last_page.get('text')}' to expected closing: '{expected_last}'")
                last_page["text"] = expected_last

        # 3. Compile and sanitize image prompts for character consistency
        main_char = story_json.get("main_character_description", "").strip() or prompts.SERIES_CHARACTER
        style = story_json.get("artistic_style", "").strip() or prompts.SERIES_STYLE
        
        for idx, page in enumerate(pages):
            scene = page.get("scene_description", "").strip()
            features_char = page.get("features_main_character", True)
            
            prompt = prompts.construct_image_prompt(
                page_scene=scene,
                main_char_desc=main_char,
                style_desc=style,
                features_char=features_char
            )
            page["image_prompt"] = prompt
            logger.info(f"Set consistent image_prompt for Page {page.get('page_number')}: '{prompt}'")
                
        # 4. Enforce Copyright & Infringement Protection
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
        
        # Scan title
        title = story_json.get("title", "")
        for key, rep in infringement_replacements.items():
            if key in title.lower():
                logger.warning(f"Trademark/copyright name '{key}' detected in title. Correcting...")
                import re
                title = re.sub(re.escape(key), rep, title, flags=re.IGNORECASE)
                story_json["title"] = title
                
        # Scan and correct page text and image prompts
        for page in pages:
            text_val = page.get("text", "")
            prompt_val = page.get("image_prompt", "")
            
            for key, rep in infringement_replacements.items():
                if key in text_val.lower():
                    logger.warning(f"Trademark/copyright name '{key}' detected in Page {page.get('page_number')} text. Correcting...")
                    import re
                    text_val = re.sub(re.escape(key), rep, text_val, flags=re.IGNORECASE)
                    page["text"] = text_val
                    
                if key in prompt_val.lower():
                    logger.warning(f"Trademark/copyright name '{key}' detected in Page {page.get('page_number')} prompt. Correcting...")
                    import re
                    prompt_val = re.sub(re.escape(key), rep, prompt_val, flags=re.IGNORECASE)
                    page["image_prompt"] = prompt_val

        logger.info("Storybook validation complete. All checks passed/corrected!")
        return story_json

    def run_workflow(self, topic: str, age_group: str, max_pages: int = None, language: str = "English", parent_id: str = None, override_char: str = None, override_style: str = None):
        """Runs the entire storybook generation workflow for a specific topic."""
        # 1. Generate story structure
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
            
        # 2. Create Google Drive folder for the book
        folder_id = self.create_drive_folder(f"{title} (Ages {age_group})", parent_id=parent_id)
        
        # 3. Generate illustrations, overlay text, and upload for each page
        pil_images = []
        pages_data = []
        for page in pages:
            page_num = page.get("page_number")
            text = page.get("text")
            img_prompt = page.get("image_prompt")
            
            logger.info(f"Processing Page {page_num}...")
            try:
                # Generate base illustration
                raw_img = self.generate_page_image(img_prompt)
                # Overlay story text
                final_img = self.overlay_text_on_image(raw_img, text, page_num, len(pages), language=language)
                
                # Convert to PIL Image for PDF compilation
                img = Image.open(io.BytesIO(final_img)).convert("RGB")
                pil_images.append(img)
                
                # Upload to Google Drive folder
                filename = f"page_{page_num:02d}.jpg"
                self.upload_image_to_drive(final_img, filename, folder_id)
                pages_data.append((filename, final_img))
            except Exception as e:
                logger.error(f"Failed to generate/upload Page {page_num}: {e}")
                
        # 4. Compile and upload combined PDF
        if pil_images:
            logger.info("Compiling all pages into a combined PDF...")
            pdf_buffer = io.BytesIO()
            pil_images[0].save(
                pdf_buffer,
                format="PDF",
                save_all=True,
                append_images=pil_images[1:]
            )
            pdf_bytes = pdf_buffer.getvalue()
            pdf_filename = f"{title}.pdf"
            self.upload_pdf_to_drive(pdf_bytes, pdf_filename, folder_id)
            
        # 5. Generate and upload Amazon KDP Publishing Description as Google Doc
        try:
            kdp_desc = self.generate_kdp_description(title, topic, age_group, language=language)
            self.upload_txt_as_doc_to_drive(kdp_desc, f"{title} - Amazon Publishing KDP Description", folder_id)
        except Exception as e:
            logger.error(f"Failed to generate/upload KDP description: {e}")
            
        # 6. Compile and upload KDP eBook EPUB file
        if pages_data:
            try:
                epub_bytes = self.create_epub(title, pages_data, language=language)
                epub_filename = f"{title} (eBook).epub"
                self.upload_epub_to_drive(epub_bytes, epub_filename, folder_id)
            except Exception as e:
                logger.error(f"Failed to generate/upload EPUB eBook: {e}")
            
        logger.info(f"Storybook '{title}' generation and upload complete!")
        logger.info(f"Output Location: https://drive.google.com/drive/folders/{folder_id}")

    def translate_story(self, story_json: dict, target_language: str) -> dict:
        """Translates the story text and title to the target language using Gemini."""
        if target_language.lower() == "english":
            return story_json
            
        logger.info(f"Translating storybook into {target_language}...")
        input_data = {
            "title": story_json.get("title", ""),
            "pages": [{"page_number": p.get("page_number"), "text": p.get("text", "")} for p in story_json.get("pages", [])]
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
                }},
                ...
            ]
        }}
        """
        client = Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            )
        )
        
        translated_data = json.loads(response.text)
        
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

    def run_multilingual_workflow(self, topic: str, age_group: str, languages: list, max_pages: int = None, parent_id: str = None, override_char: str = None, override_style: str = None):
        """Generates raw page illustrations once, then builds individual editions for each language."""
        logger.info(f"Starting multilingual storybook pipeline for topic '{topic}'...")
        
        # 1. Generate story structure in English (base language)
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
            
        # 2. Generate and download base raw illustrations (without overlay text)
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
                
        # 3. Process each language
        for language in languages:
            logger.info(f"=== Generating book edition for language: '{language}' ===")
            try:
                lang_story = self.translate_story(base_story, language)
                lang_title = lang_story.get("title", f"Story about {topic} ({language})")
                lang_pages = lang_story.get("pages", [])
                
                # Create a subfolder inside Google Drive for this specific language edition
                lang_folder_id = self.create_drive_folder(f"{lang_title} (Ages {age_group})", parent_id=parent_id)
                
                pil_images = []
                pages_data = []
                for p in lang_pages:
                    p_num = p.get("page_number")
                    text = p.get("text")
                    
                    if p_num not in raw_images:
                        logger.error(f"Raw image missing for Page {p_num}! Skipping page...")
                        continue
                        
                    raw_img = raw_images[p_num]
                    
                    # Overlay language specific text on top of the base raw image
                    final_img = self.overlay_text_on_image(raw_img, text, p_num, len(lang_pages), language=language)
                    
                    # Convert to PIL Image for PDF compiling
                    img = Image.open(io.BytesIO(final_img)).convert("RGB")
                    pil_images.append(img)
                    
                    # Upload page image to Drive folder
                    filename = f"page_{p_num:02d}.jpg"
                    self.upload_image_to_drive(final_img, filename, lang_folder_id)
                    pages_data.append((filename, final_img))
                    
                # Compile and upload PDF
                if pil_images:
                    pdf_buffer = io.BytesIO()
                    pil_images[0].save(
                        pdf_buffer,
                        format="PDF",
                        save_all=True,
                        append_images=pil_images[1:]
                    )
                    pdf_filename = f"{lang_title}.pdf"
                    self.upload_pdf_to_drive(pdf_buffer.getvalue(), pdf_filename, lang_folder_id)
                    
                # Compile and upload EPUB
                if pages_data:
                    epub_bytes = self.create_epub(lang_title, pages_data, language=language)
                    epub_filename = f"{lang_title} (eBook).epub"
                    self.upload_epub_to_drive(epub_bytes, epub_filename, lang_folder_id)
                    
                # Generate and upload KDP description Doc
                kdp_desc = self.generate_kdp_description(lang_title, topic, age_group, language=language)
                self.upload_txt_as_doc_to_drive(kdp_desc, f"{lang_title} - Amazon Publishing KDP Description", lang_folder_id)
                
                logger.info(f"Successfully generated and uploaded '{language}' edition!")
            except Exception as e:
                logger.error(f"Failed to generate book edition for language '{language}': {e}")
