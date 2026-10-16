import json
import os
import unittest
from unittest.mock import MagicMock, patch

from storybook import StorybookGenerator


class TestStorybookGenerator(unittest.TestCase):
    
    def setUp(self):
        self.mock_drive = MagicMock()
        self.generator = StorybookGenerator(self.mock_drive)
        
    def test_text_wrapping_standard(self):
        # Test standard space-separated wrapping (English)
        text = "This is a very long sentence that needs to wrap properly onto multiple lines because it exceeds forty-five characters."
        image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xdd\x00\x04\x00\x01\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x37\xff\xd9" # 1x1 blank JPEG bytes
        
        # Call overlay_text_on_image
        try:
            res = self.generator.overlay_text_on_image(image_bytes, text, page_num=2, total_pages=20, language="English")
            self.assertIsInstance(res, bytes)
        except Exception as e:
            self.fail(f"overlay_text_on_image raised exception: {e}")
            
    def test_text_wrapping_asian(self):
        # Test character-based wrapping (Japanese)
        text = "これは日本語のテスト文章です。非常に長い文章なので、一行が二十二文字を超えると自動的に改行されるはずです。"
        image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xdd\x00\x04\x00\x01\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x37\xff\xd9"
        
        try:
            res = self.generator.overlay_text_on_image(image_bytes, text, page_num=2, total_pages=20, language="Japanese")
            self.assertIsInstance(res, bytes)
        except Exception as e:
            self.fail(f"overlay_text_on_image (Japanese) raised exception: {e}")

    @patch("storybook.Client")
    def test_translate_story(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Mock Gemini response
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "title": "Translated Title",
            "pages": [
                {"page_number": 1, "text": "Translated Page 1"},
                {"page_number": 2, "text": "Translated Page 2"}
            ]
        })
        mock_client.models.generate_content.return_value = mock_response
        
        story_json = {
            "title": "Original Title",
            "main_character_description": "Lily description",
            "artistic_style": "Watercolor",
            "pages": [
                {"page_number": 1, "text": "Original Page 1", "image_prompt": "Prompt 1"},
                {"page_number": 2, "text": "Original Page 2", "image_prompt": "Prompt 2"}
            ]
        }
        
        res = self.generator.translate_story(story_json, "Spanish")
        
        self.assertEqual(res["title"], "Translated Title")
        self.assertEqual(res["pages"][0]["text"], "Translated Page 1")
        # Ensure image prompts are preserved!
        self.assertEqual(res["pages"][0]["image_prompt"], "Prompt 1")
        self.assertEqual(res["main_character_description"], "Lily description")
        
    @patch("storybook.StorybookGenerator.generate_story")
    @patch("storybook.StorybookGenerator.validate_and_correct_story")
    @patch("storybook.StorybookGenerator.generate_page_image")
    @patch("storybook.StorybookGenerator.overlay_text_on_image")
    @patch("storybook.StorybookGenerator.create_drive_folder")
    @patch("storybook.StorybookGenerator.upload_image_to_drive")
    @patch("storybook.StorybookGenerator.upload_pdf_to_drive")
    @patch("storybook.StorybookGenerator.upload_epub_to_drive")
    @patch("storybook.StorybookGenerator.upload_txt_as_doc_to_drive")
    @patch("storybook.StorybookGenerator.translate_story")
    @patch("storybook.StorybookGenerator.generate_kdp_description")
    def test_run_multilingual_workflow(
        self, mock_kdp, mock_translate, mock_upload_txt, mock_upload_epub, mock_upload_pdf,
        mock_upload_img, mock_create_folder, mock_overlay, mock_gen_img,
        mock_validate, mock_gen_story
    ):
        # Mock generator return values
        blank_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xdd\x00\x04\x00\x01\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x37\xff\xd9"
        mock_gen_story.return_value = {"title": "Base Title", "pages": [{"page_number": 1, "text": "Cover", "image_prompt": "Cover prompt"}]}
        mock_validate.return_value = {"title": "Base Title", "pages": [{"page_number": 1, "text": "Cover", "image_prompt": "Cover prompt"}]}
        mock_gen_img.return_value = blank_jpeg
        mock_overlay.return_value = blank_jpeg
        mock_create_folder.return_value = "folder_123"
        mock_translate.return_value = {"title": "Translated Title", "pages": [{"page_number": 1, "text": "Translated Cover", "image_prompt": "Cover prompt"}]}
        mock_kdp.return_value = "Mocked KDP description"
        
        try:
            self.generator.run_multilingual_workflow(
                topic="test topic",
                age_group="4-6",
                languages=["English", "Spanish"],
                parent_id="parent_id_123"
            )
            # Ensure images are generated only once (1 page in the mocked story = 1 generate_page_image call)
            self.assertEqual(mock_gen_img.call_count, 1)
            # Ensure overlays are called twice (once for English, once for Spanish)
            self.assertEqual(mock_overlay.call_count, 2)
            # Ensure folder is created for each language edition
            self.assertEqual(mock_create_folder.call_count, 2)
        except Exception as e:
            self.fail(f"run_multilingual_workflow raised exception: {e}")

    def test_upload_audio_to_drive(self):
        mock_files = MagicMock()
        self.mock_drive.files.return_value = mock_files
        mock_create = MagicMock()
        mock_files.create.return_value = mock_create
        mock_create.execute.return_value = {"id": "audio_file_id_456"}

        file_id = self.generator.upload_audio_to_drive(
            audio_bytes=b"fake audio mp3 bytes",
            filename="Story_Chinese_Read_Aloud.mp3",
            folder_id="drive_folder_123"
        )
        self.assertEqual(file_id, "audio_file_id_456")
        self.assertTrue(mock_files.create.called)
        call_kwargs = mock_files.create.call_args[1]
        self.assertEqual(call_kwargs["body"]["name"], "Story_Chinese_Read_Aloud.mp3")
        self.assertEqual(call_kwargs["body"]["parents"], ["drive_folder_123"])

    @patch("audio_generator.generate_chinese_audio_file")
    @patch("storybook.StorybookGenerator.translate_story")
    def test_generate_chinese_audio(self, mock_translate, mock_gen_audio):
        mock_gen_audio.return_value = "/tmp/story_Chinese_Read_Aloud.mp3"
        mock_translate.return_value = {
            "title": "沙盒机器人",
            "pages": [{"page_number": 1, "text": "沙盒机器人\n作者：Enrique Chan"}]
        }

        story_en = {
            "title": "Agent Sandbox",
            "pages": [{"page_number": 1, "text": "Agent Sandbox\nWritten by Enrique Chan"}]
        }

        out = self.generator.generate_chinese_audio(
            story_json=story_en,
            output_mp3_path="/tmp/story_Chinese_Read_Aloud.mp3"
        )
        self.assertEqual(out, "/tmp/story_Chinese_Read_Aloud.mp3")
        self.assertTrue(mock_translate.called)
        self.assertTrue(mock_gen_audio.called)

    @patch("storybook.StorybookGenerator.generate_story")
    @patch("storybook.StorybookGenerator.validate_and_correct_story")
    @patch("storybook.StorybookGenerator.generate_page_image")
    @patch("storybook.StorybookGenerator.overlay_text_on_image")
    @patch("storybook.StorybookGenerator.create_drive_folder")
    @patch("storybook.StorybookGenerator.upload_image_to_drive")
    @patch("storybook.StorybookGenerator.upload_pdf_to_drive")
    @patch("storybook.StorybookGenerator.upload_epub_to_drive")
    @patch("storybook.StorybookGenerator.upload_txt_as_doc_to_drive")
    @patch("storybook.StorybookGenerator.generate_kdp_description")
    @patch("storybook.StorybookGenerator.generate_chinese_audio")
    @patch("storybook.StorybookGenerator.upload_audio_to_drive")
    def test_run_fast_publishing_workflow(
        self, mock_upload_audio, mock_gen_audio, mock_kdp, mock_upload_txt,
        mock_upload_epub, mock_upload_pdf, mock_upload_img, mock_create_folder,
        mock_overlay, mock_gen_img, mock_validate, mock_gen_story
    ):
        blank_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xdd\x00\x04\x00\x01\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x37\xff\xd9"
        mock_gen_story.return_value = {
            "title": "Fast Test Story",
            "pages": [
                {"page_number": 1, "text": "Cover", "image_prompt": "Cover prompt"},
                {"page_number": 2, "text": "Page 2", "image_prompt": "Page 2 prompt"}
            ]
        }
        mock_validate.return_value = {
            "title": "Fast Test Story",
            "pages": [
                {"page_number": 1, "text": "Cover", "image_prompt": "Cover prompt"},
                {"page_number": 2, "text": "Page 2", "image_prompt": "Page 2 prompt"}
            ]
        }
        mock_gen_img.return_value = blank_jpeg
        mock_overlay.return_value = blank_jpeg
        mock_create_folder.return_value = "folder_fast_123"
        mock_kdp.return_value = "KDP Test Description"

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            res = self.generator.run_fast_publishing_workflow(
                topic="agent sandbox",
                age_group="4-6",
                max_pages=2,
                generate_audio=True,
                output_dir=tmpdir
            )

            # Assert single-pass generation (2 pages)
            self.assertEqual(mock_gen_img.call_count, 2)
            self.assertEqual(mock_overlay.call_count, 2)

            # Assert Drive uploads
            self.assertEqual(mock_upload_img.call_count, 2)
            self.assertEqual(mock_upload_pdf.call_count, 1)
            self.assertEqual(mock_upload_epub.call_count, 1)
            self.assertEqual(mock_upload_txt.call_count, 1)

            # Assert Chinese Audio was generated
            self.assertTrue(mock_gen_audio.called)

            # Assert returned manifest
            self.assertEqual(res["title"], "Fast Test Story")
            self.assertEqual(res["pages_count"], 2)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "story.json")))
            self.assertTrue(os.path.exists(res["pdf_path"]))
            self.assertTrue(os.path.exists(res["epub_path"]))
            self.assertTrue(os.path.exists(res["kdp_description_path"]))

    def test_validate_and_correct_story_branding_and_jargon(self):
        story = {
            "title": "Agent Sandbox Adventure",
            "pages": [
                {"page_number": 1, "text": "Wrong Title\nBy Anon"},
                {"page_number": 2, "text": "Robo connected to the API and checked the server database for parameters."},
                {"page_number": 3, "text": "The End"}
            ]
        }
        corrected = self.generator.validate_and_correct_story(story, "agent sandbox", "English")

        # Verify Series & Author branding injected
        self.assertEqual(corrected["series"], "Curious Little Minds™: Lily & Robo Adventures")
        self.assertEqual(corrected["author"], "Enrique Chan")

        # Verify Cover author credit enforced
        self.assertEqual(corrected["pages"][0]["text"], "Agent Sandbox Adventure\nWritten by Enrique Chan")

        # Verify Technical Jargon replaced with child-friendly story equivalents
        p2_text = corrected["pages"][1]["text"].lower()
        self.assertNotIn("api", p2_text)
        self.assertNotIn("server", p2_text)
        self.assertNotIn("database", p2_text)
        self.assertIn("helpful tools", p2_text)
        self.assertIn("helper stations", p2_text)
        self.assertIn("memory treasure boxes", p2_text)

    def test_overlay_cover_badge(self):
        blank_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xdd\x00\x04\x00\x01\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x37\xff\xd9"
        # Test cover page (page 1) generates without exception and returns bytes
        cover_img = self.generator.overlay_text_on_image(
            image_bytes=blank_jpeg,
            text="Agent Sandbox\nWritten by Enrique Chan",
            page_num=1,
            total_pages=20,
            language="English"
        )
        self.assertIsInstance(cover_img, bytes)
        self.assertGreater(len(cover_img), 0)

if __name__ == "__main__":
    unittest.main()
