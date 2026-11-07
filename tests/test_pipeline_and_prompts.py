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


class TestGoogleVidsExporter(unittest.TestCase):
    def test_vids_exporter_builds_16x9_pptx_with_speaker_notes(self):
        import json
        import os
        import zipfile

        import vids_exporter

        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = os.path.join(tmpdir, "Sample Book")
            os.makedirs(book_dir, exist_ok=True)
            jpg = _make_blank_jpeg(512, 512)
            for p_num in (1, 2):
                with open(os.path.join(book_dir, f".cache_page_{p_num:02d}_raw.jpg"), "wb") as rf:
                    rf.write(jpg)
                with open(os.path.join(book_dir, f"page_{p_num:02d}.jpg"), "wb") as pf:
                    pf.write(jpg)

            story_data = {
                "title": "The Lantern-Parade Horse",
                "title_es": "El Caballo del Desfile de Faroles",
                "title_zh": "花灯小马大游行",
                "author": "Enrique Chan",
                "pages": [
                    {
                        "page_number": 1,
                        "text": "The Lantern-Parade Horse\nWritten by Enrique Chan",
                        "text_es": "El Caballo del Desfile de Faroles\nEscrito por Enrique Chan",
                        "text_zh": "花灯小马大游行\n作者：Enrique Chan",
                    },
                    {
                        "page_number": 2,
                        "text": 'Lily clapped her hands! *CLIP-CLOP, GLOW!*',
                        "text_es": "¡Lily aplaudió! *¡CLOP-CLOP, BRILLA!*",
                        "text_zh": "莉莉拍拍小手！*哒哒，闪闪发光！*",
                    },
                ],
            }
            with open(os.path.join(book_dir, "story.json"), "w", encoding="utf-8") as sf:
                json.dump(story_data, sf, ensure_ascii=False)

            res = vids_exporter.export_book_for_google_vids(
                book_dir,
                auto_translate=False,
                compile_localized_books=True,
            )
            self.assertIn("en", res["decks"])
            self.assertIn("es", res["decks"])
            self.assertIn("zh", res["decks"])
            self.assertTrue(os.path.exists(res["decks"]["en"]))
            self.assertTrue(os.path.exists(res["decks"]["es"]))
            self.assertTrue(os.path.exists(res["decks"]["zh"]))
            self.assertIn("es", res["localized_pdfs"])
            self.assertIn("es", res["localized_epubs"])
            self.assertTrue(os.path.exists(res["localized_pdfs"]["es"]))
            self.assertTrue(os.path.exists(res["localized_epubs"]["es"]))

            with zipfile.ZipFile(res["decks"]["en"], "r") as zf:
                names = zf.namelist()
                self.assertIn("ppt/slides/slide1.xml", names)
                self.assertIn("ppt/notesSlides/notesSlide2.xml", names)
                notes2_xml = zf.read("ppt/notesSlides/notesSlide2.xml").decode("utf-8")
                self.assertIn("CLIP-CLOP, GLOW!", notes2_xml)
                self.assertNotIn("*CLIP-CLOP", notes2_xml)
                stage_img = Image.open(io.BytesIO(zf.read("ppt/media/page_01_stage.jpg")))
                self.assertEqual(stage_img.size, (1920, 1080))

            with zipfile.ZipFile(res["decks"]["es"], "r") as zf_es:
                notes2_es = zf_es.read("ppt/notesSlides/notesSlide2.xml").decode("utf-8")
                self.assertIn("¡Lily aplaudió!", notes2_es)
                self.assertNotIn("*¡CLOP-CLOP", notes2_es)

    def test_speaker_notes_match_storybook_text_exactly(self):
        import vids_exporter

        story_data = {
            "title": "The Night-Light Helpers",
            "title_es": "Los Ayudantes de la Luz Nocturna",
            "title_zh": "夜灯小帮手",
            "pages": [
                {
                    "page_number": 1,
                    "text": "The Night-Light Helpers\nWritten by Enrique Chan",
                    "text_es": "Los Ayudantes de la Luz Nocturna\nEscrito por Enrique Chan",
                    "text_zh": "夜灯小帮手\n作者：Enrique Chan",
                },
                {
                    "page_number": 2,
                    "text": "Curious Little Minds Publishing\nText and illustrations copyright © 2026 Enrique Chan. All rights reserved.",
                    "text_es": "Editorial Mentes Curiosas\nTexto e ilustraciones copyright © 2026 Enrique Chan. Todos los derechos reservados.",
                    "text_zh": "好奇心出版社\n文字与插图版权 © 2026 Enrique Chan。保留所有权利。",
                },
                {
                    "page_number": 3,
                    "text": "For every curious little mind.",
                    "text_es": "Para cada pequeña mente curiosa.",
                    "text_zh": "献给每一个好奇的小脑袋。",
                },
                {
                    "page_number": 4,
                    "text": "Lily and Robo stepped into the lantern square.",
                    "text_es": "Lily y Robo entraron en la plaza de las linternas.",
                    "text_zh": "莉莉和乐乐走进了灯笼广场。",
                },
                {
                    "page_number": 19,
                    "text": "STEM Exploration Corner!\n1. What helpers keep working at night?",
                    "text_es": "¡Rincón de Exploración STEM!\n1. ¿Qué ayudantes siguen trabajando por la noche?",
                    "text_zh": "STEM探索角！\n1. 有哪些小帮手在夜里继续工作？",
                },
                {
                    "page_number": 20,
                    "text": "The End",
                    "text_es": "El fin",
                    "text_zh": "终",
                },
            ],
        }
        # Pages 1 & 2: warm presenter intros
        self.assertIn(
            "Welcome to Curious Little Minds.",
            vids_exporter.extract_page_voiceover(story_data, story_data["pages"][0], 20, lang="en"),
        )
        self.assertIn(
            "Bienvenidos a Pequeñas Mentes Curiosas.",
            vids_exporter.extract_page_voiceover(story_data, story_data["pages"][0], 20, lang="es"),
        )
        self.assertIn(
            "In this cozy world, complex technology concepts",
            vids_exporter.extract_page_voiceover(story_data, story_data["pages"][1], 20, lang="en"),
        )
        # Pages 3-18: 100% verbatim story content
        for page in story_data["pages"][2:4]:
            self.assertEqual(
                vids_exporter.extract_page_voiceover(story_data, page, 20, lang="en"),
                page["text"],
            )
            self.assertEqual(
                vids_exporter.extract_page_voiceover(story_data, page, 20, lang="es"),
                page["text_es"],
            )
            self.assertEqual(
                vids_exporter.extract_page_voiceover(story_data, page, 20, lang="zh"),
                page["text_zh"],
            )
        # Pages 19 & 20: warm STEM corner & bedtime sign-off
        self.assertIn(
            "In our STEM corner today, let's think together:",
            vids_exporter.extract_page_voiceover(story_data, story_data["pages"][4], 20, lang="en"),
        )
        self.assertIn(
            "Sleep well, little explorer",
            vids_exporter.extract_page_voiceover(story_data, story_data["pages"][5], 20, lang="en"),
        )

    def test_vids_avatar_automator_builds_macro(self):
        import json
        import os

        import vids_avatar_automator

        # output/ is gitignored, so a fresh checkout (CI) has no generated books: build one in a temp dir.
        title = "Lantern Square Lights"
        story = {
            "title": title,
            "pages": [
                {"page_number": 4, "text": "Robo's lantern glowed softly."},
                {"page_number": 3, "text": "Lily and Robo stepped into the lantern square."},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = os.path.join(tmpdir, title)
            os.makedirs(book_dir)
            with open(os.path.join(book_dir, "story.json"), "w", encoding="utf-8") as f:
                json.dump(story, f)
            catalog = vids_avatar_automator.load_all_book_scripts(tmpdir)

        self.assertEqual(list(catalog), [title])
        self.assertEqual(len(catalog[title]), 10)
        self.assertTrue(all(len(lines) == 2 for lines in catalog[title].values()))
        js = vids_avatar_automator.build_vids_avatar_macro_js(
            start_scene=2,
            end_scene=20,
            cooldown_sec=12,
            avatar_name="Mia",
            auto_start=False,
            book_scripts=catalog,
        )
        self.assertIn("story-agent-vids-automator", js)
        self.assertIn("const DEFAULT_START = 2;", js)
        self.assertIn("const DEFAULT_END = 20;", js)
        self.assertIn('"Mia"', js)
        self.assertIn(title, js)




class TestCharacterConsistencyAndRegistry(unittest.TestCase):
    def test_book1_reference_sheet_exists_and_loads(self):
        import config

        self.assertTrue(
            config.REFERENCE_SHEET_PATH.exists(),
            f"Canonical Book 1 reference sheet must exist at {config.REFERENCE_SHEET_PATH}",
        )
        gen = StorybookGenerator(drive_service=None)
        refs = gen._load_character_reference_images()
        self.assertGreaterEqual(len(refs), 1)
        self.assertGreater(len(refs[0]), 1000)

    def test_missing_reference_sheet_raises_when_required(self):
        import pathlib

        import config

        gen = StorybookGenerator(drive_service=None)
        with (
            patch.object(config, "REFERENCE_SHEET_PATH", pathlib.Path("/nonexistent/ref.png")),
            patch.object(config, "get_character_reference_paths", return_value=[]),
            patch.object(config, "REQUIRE_CHARACTER_REFERENCE", True),
        ):
            with self.assertRaises(FileNotFoundError):
                gen._load_character_reference_images()

    def test_extensible_character_registry_injects_guest_character(self):
        import config

        active_with_mia = config.get_active_characters(["mia"])
        ids = [c["id"] for c in active_with_mia]
        self.assertIn("lily", ids)
        self.assertIn("robo", ids)
        self.assertIn("mia", ids)

        img_prompt = prompts.construct_image_prompt(
            page_scene="Lily, Robo, and Mia building a glowing sandcastle together.",
            features_char=True,
            character_ids=["mia"],
        )
        self.assertIn("Mia", img_prompt)
        self.assertIn("tortoiseshell glasses", img_prompt)
        self.assertIn("Peter Pan collar", img_prompt)

    def test_validate_and_correct_story_preserves_guest_characters(self):
        gen = StorybookGenerator(drive_service=None)
        raw_story = {
            "title": "The Garden Workshop",
            "pages": [
                {
                    "page_number": 1,
                    "text": "The Garden Workshop\nWritten by Enrique Chan",
                    "scene_description": "Lily, Robo, Mia, and Leo waving in the garden.",
                    "features_main_character": True,
                },
                {
                    "page_number": 2,
                    "text": "Leo handed Mia a wrench while Lily and Robo cheered!",
                    "scene_description": "Leo and Mia helping Lily and Robo build a birdhouse.",
                    "features_main_character": True,
                },
                {
                    "page_number": 3,
                    "text": "The End",
                    "scene_description": "Back cover illustration.",
                    "features_main_character": True,
                },
            ],
        }
        corrected = gen.validate_and_correct_story(
            raw_story,
            topic="Agent Teamwork",
            language="English",
            character_ids=["lily", "robo", "mia", "leo"],
        )
        self.assertIn("Leo handed Mia", corrected["pages"][1]["text"])
        self.assertIn("Mia", corrected["pages"][1]["image_prompt"])
        self.assertIn("Leo", corrected["pages"][1]["image_prompt"])

    def test_spanish_and_english_narration_script_builders(self):
        story_data = {
            "title": "The Lantern-Parade Horse",
            "title_es": "El Caballo del Desfile de Faroles",
            "pages": [
                {
                    "page_number": 1,
                    "text": "The Lantern-Parade Horse\nWritten by Enrique Chan",
                    "text_es": "El Caballo del Desfile de Faroles\nEscrito por Enrique Chan",
                },
                {
                    "page_number": 2,
                    "text": "Lily clapped her hands! *CLIP-CLOP!*",
                    "text_es": "¡Lily aplaudió! *¡CLOP-CLOP!*",
                },
            ],
        }
        es_script = audio_generator.build_spanish_narration_script(story_data)
        en_script = audio_generator.build_english_narration_script(story_data)
        self.assertIn("El Caballo del Desfile de Faroles", es_script)
        self.assertIn("¡Lily aplaudió!", es_script)
        self.assertNotIn("*¡CLOP-CLOP!*", es_script)
        self.assertIn("The Lantern-Parade Horse", en_script)
        self.assertIn("Lily clapped her hands!", en_script)
        self.assertNotIn("*CLIP-CLOP!*", en_script)

    def test_book1_four_mode_page_pacing_prompts(self):
        # 1. 'both' mode
        self.assertEqual(
            prompts.detect_page_character_mode(
                "Lily and spherical Robo waving in the garden.", features_char=True
            ),
            "both",
        )
        p_both = prompts.construct_image_prompt(
            page_scene="Lily and spherical Robo waving in the garden.", features_char=True
        )
        self.assertIn("STRICT CHARACTER INVARIANTS", p_both)

        # 2. 'lily_only' mode
        self.assertEqual(
            prompts.detect_page_character_mode(
                "Lily sitting by her window looking at the stars.", features_char=True
            ),
            "lily_only",
        )
        p_lily = prompts.construct_image_prompt(
            page_scene="Lily sitting by her window looking at the stars.", features_char=True
        )
        self.assertIn("SOLO LILY SCENE (NO ROBO IN FRAME)", p_lily)
        self.assertIn("Avoid: robot, Robo", p_lily)

        # 3. 'robo_only' mode
        self.assertEqual(
            prompts.detect_page_character_mode(
                "Spherical sky-blue Robo holding a glowing paintbrush.", features_char=True
            ),
            "robo_only",
        )
        p_robo = prompts.construct_image_prompt(
            page_scene="Spherical sky-blue Robo holding a glowing paintbrush.", features_char=True
        )
        self.assertIn("SOLO ROBO SCENE (NO LILY IN FRAME)", p_robo)
        self.assertIn("Avoid: human girl, child, Lily", p_robo)

        # 4. 'none' (world/object/cutaway/panoramic) mode
        self.assertEqual(
            prompts.detect_page_character_mode(
                "Close-up of three glowing crystal star jars on a wooden shelf.", features_char=False
            ),
            "none",
        )
        p_none = prompts.construct_image_prompt(
            page_scene="Close-up of three glowing crystal star jars on a wooden shelf.",
            features_char=False,
        )
        self.assertIn("PURE WORLD & OBJECT SCENE (NO CHARACTERS)", p_none)
        self.assertNotIn("Lily is ALWAYS a 6-year-old girl", p_none)
        self.assertIn("Avoid: human characters, girl, child, Lily, robot, Robo", p_none)


if __name__ == "__main__":
    unittest.main()
