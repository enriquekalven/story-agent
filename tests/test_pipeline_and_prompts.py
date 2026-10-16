"""Comprehensive tests for prompt sanitization, EPUB 3 validity, visual QA, resume cache, Drive upsert, and pipeline CLI."""

import io
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import audio_generator
import drive_sync
import prompts
import publish_pipeline
from storybook import StorybookGenerator


def _make_blank_jpeg(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(240, 235, 225)).save(buf, format="JPEG")
    return buf.getvalue()


class TestPromptSanitizationAndInvariants(unittest.TestCase):
    def test_series_style_not_corrupted_by_sanitizer(self):
        """Regression test for Defect #3: trusted SERIES_STYLE must retain 'words'."""
        prompt = prompts.construct_image_prompt(
            page_scene="Lily and Robo walking through a glowing pastel meadow.",
            features_char=True,
        )
        self.assertIn("Strictly textless, with no signatures, logos, or words", prompt)
        self.assertIn("Avoid:", prompt)
        self.assertIn("NO antennas", prompt)

    def test_quoted_titles_and_banners_stripped_from_scene(self):
        """Regression test for Defects #1 & #2: strip quoted titles and banners from LLM scenes."""
        leaked_scene = (
            'Front cover illustration featuring Lily and Robo with a glowing banner saying '
            '"Curious Little Minds" and book title \'The Night-Light Helpers\' written by Enrique Chan.'
        )
        prompt = prompts.construct_image_prompt(page_scene=leaked_scene, features_char=True)
        self.assertNotIn("The Night-Light Helpers", prompt)
        self.assertNotIn("Enrique Chan", prompt)
        self.assertNotIn("banner saying", prompt.lower())

    def test_closing_page_scene_strips_the_end_quotes(self):
        leaked_p20 = 'Lily and Robo waving goodbye under the stars with the words "The End" in the sky.'
        cleaned = prompts.sanitize_scene_description(leaked_p20)
        self.assertNotIn("The End", cleaned)
        self.assertIn("Lily and Robo waving goodbye", cleaned)


class TestEPUB3AndPDFValidation(unittest.TestCase):
    def setUp(self):
        self.generator = StorybookGenerator(drive_service=None)

    def test_epub3_valid_with_ampersand_in_title(self):
        """Regression test for Defect #7: EPUB 3 has nav.xhtml, dcterms:modified, cover-image, and XML-escapes '&'."""
        jpg = _make_blank_jpeg()
        epub_bytes = self.generator.create_epub(
            title="Lily & Robo's <Magical> Adventure",
            pages_data=[("page_01.jpg", jpg), ("page_02.jpg", jpg)],
            language="English",
        )
        report = self.generator.validate_epub_bytes(epub_bytes)
        self.assertTrue(report["valid"], f"EPUB 3 validation failed: {report['errors']}")

    def test_pdf_compilation_300_dpi(self):
        """Regression test for Defect #8: compiled PDF embeds 300 DPI metadata and supports print upscaling."""
        img1 = Image.open(io.BytesIO(_make_blank_jpeg(128, 128)))
        img2 = Image.open(io.BytesIO(_make_blank_jpeg(128, 128)))
        pdf_bytes = StorybookGenerator.compile_pdf_bytes([img1, img2], dpi=300.0, upscale_for_print=False)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 500)


class TestValidatorAndAdaptiveOverlay(unittest.TestCase):
    def setUp(self):
        self.generator = StorybookGenerator(drive_service=None)

    def test_back_cover_blurb_preserved_and_page_19_trimmed(self):
        """Regression test for Defects #5 & #6."""
        long_p19 = " ".join(["Ask your child how helper robots remember steps!"] * 12)  # 96 words
        story = {
            "title": "Night Light Helpers",
            "pages": [
                {"page_number": 1, "text": "Night Light Helpers", "scene_description": "Lily in a meadow."},
                {"page_number": 19, "text": long_p19, "scene_description": "Lily and Robo with star jars."},
                {
                    "page_number": 20,
                    "text": "Collect all 8 Curious Little Minds adventures with Lily and Robo!",
                    "scene_description": "Starry night sky.",
                },
            ],
        }
        validated = self.generator.validate_and_correct_story(story, "long running agents", "English")
        self.assertEqual(validated["pages"][-1]["text"], "The End")
        self.assertEqual(
            validated["back_cover_blurb"],
            "Collect all 8 Curious Little Minds adventures with Lily and Robo!",
        )
        p19_words = validated["pages"][1]["text"].split()
        self.assertLessEqual(len(p19_words), 60)

    def test_overlay_dense_text_does_not_overflow(self):
        jpg = _make_blank_jpeg(512, 512)
        dense_text = " ".join(["Curious explorers love building gentle bedtime castles together."] * 10)
        out = self.generator.overlay_text_on_image(
            jpg, dense_text, page_num=19, total_pages=20, language="English"
        )
        self.assertIsInstance(out, bytes)
        self.assertGreater(len(out), 1000)


class TestVisualQAAndParallelResume(unittest.TestCase):
    def test_visual_qa_regenerates_failed_page(self):
        mock_client = MagicMock()
        gen = StorybookGenerator(drive_service=None, ai_client=mock_client)
        jpg = _make_blank_jpeg(128, 128)

        with (
            patch.object(gen, "generate_page_image", return_value=jpg) as mock_gen_img,
            patch.object(
                gen,
                "evaluate_page_image_qa",
                side_effect=[
                    {"passed": False, "has_unwanted_text": True, "issues": ["banner text visible"]},
                    {"passed": True, "has_unwanted_text": False, "issues": []},
                ],
            ) as mock_qa,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                page = {
                    "page_number": 1,
                    "text": "Test Title\nWritten by Enrique Chan",
                    "image_prompt": "Clean meadow",
                    "scene_description": "Clean meadow",
                    "features_main_character": True,
                }
                result = gen._render_single_page(
                    page=page,
                    total_pages=2,
                    language="English",
                    local_dir=tmpdir,
                    resume=True,
                    enable_visual_qa=True,
                    max_qa_retries=2,
                )
                self.assertEqual(mock_gen_img.call_count, 2)
                self.assertEqual(mock_qa.call_count, 2)
                self.assertTrue(result["qa"]["passed"])

                # Second call with resume=True should reuse cached raw image without calling generate_page_image
                gen._render_single_page(
                    page=page,
                    total_pages=2,
                    language="English",
                    local_dir=tmpdir,
                    resume=True,
                    enable_visual_qa=True,
                )
                self.assertEqual(mock_gen_img.call_count, 2)


class TestDriveSyncUpsertAndPipelineExitCode(unittest.TestCase):
    def test_upsert_updates_existing_file_instead_of_duplicating(self):
        mock_drive = MagicMock()
        mock_files = MagicMock()
        mock_drive.files.return_value = mock_files
        mock_files.list.return_value.execute.return_value = {"files": [{"id": "existing_file_999"}]}
        mock_files.update.return_value.execute.return_value = {"id": "existing_file_999"}

        file_id = drive_sync.upsert_bytes_to_drive(
            mock_drive,
            b"updated pdf content",
            "Book.pdf",
            "folder_123",
            mimetype="application/pdf",
        )
        self.assertEqual(file_id, "existing_file_999")
        self.assertTrue(mock_files.update.called)
        self.assertFalse(mock_files.create.called)

    def test_pipeline_exits_nonzero_on_failed_topic(self):
        with patch(
            "publish_pipeline.run_pipeline",
            return_value=[{"status": "partial_failure", "title": "Broken Book", "pages_failed": [2]}],
        ):
            with self.assertRaises(SystemExit) as ctx:
                publish_pipeline.main(["--topic", "agent sandbox", "--no-drive"])
            self.assertEqual(ctx.exception.code, 1)

    def test_clean_script_for_cloud_tts_removes_slnc_directives(self):
        raw = "欢迎收听\n[[slnc 900]]\n第一页内容\n[[slnc 600]]\n晚安"
        cleaned = audio_generator.clean_script_for_cloud_tts(raw)
        self.assertNotIn("slnc", cleaned)
        self.assertIn("欢迎收听", cleaned)


if __name__ == "__main__":
    unittest.main()
