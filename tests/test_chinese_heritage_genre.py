"""Regression suite for the Chinese Heritage & Proverbs genre (Mei, Ming & Baobao).

Guards every seam that previously assumed the Lily & Robo STEM cast: the character
registry, story/image prompts, reference-image conditioning, Visual QA presence rules,
the publishing pipeline + CLI, KDP metadata, narration scripts, and Google Vids
voiceovers. All tests are hermetic (no network, no ADC).
"""

import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml
from PIL import Image

import audio_generator
import config
import prompts
import publish_pipeline
import storybook
import vids_exporter
from storybook import StorybookGenerator

HERITAGE_CAST = ["mei", "ming", "baobao"]
IRON_ROD_TOPIC = (
    "Grinding an Iron Rod into a Needle (铁杵磨成针 / 鐵杵磨成針 — how Mei, Ming, and their "
    "red-panda spirit guide Baobao discover the classic Chinese proverb)"
)
STEM_NAMES = ("Lily", "莉莉", "リリー", "릴리")
HERITAGE_SERIES = f"{prompts.SERIES_BRAND}: {prompts.HERITAGE_SERIES_NAME}"
SOLO_SCENES = {
    "mei": "Mei practicing gentle brushstrokes by a round moon window.",
    "ming": "Ming skipping smooth stones across the bamboo stream.",
    "baobao": "Baobao curled up asleep on a mossy river rock.",
}
TOGETHER_SCENE = "Mei, Ming, and Baobao crossing an arched stone bridge."
WORLD_SCENE = "Close-up of a smooth river stone beside a shining sewing needle."
CLEAN_QA = {
    "passed": True,
    "score": 1.0,
    "has_unwanted_text": False,
    "robo_off_model": False,
    "lily_off_model": False,
    "lily_extra_hands": False,
    "issues": [],
}


def _image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(240, 235, 225)).save(buf, format=fmt)
    return buf.getvalue()


def _image_response(data: bytes) -> MagicMock:
    part = MagicMock()
    part.inline_data.data = data
    response = MagicMock()
    response.parts = [part]
    return response


def _sent_parts(mock_client: MagicMock) -> tuple[list[bytes], list[str]]:
    """Returns (attached image bytes, text parts) from the last generate_content call."""
    contents = mock_client.models.generate_content.call_args.kwargs["contents"]
    images = [p.inline_data.data for p in contents if getattr(p, "inline_data", None)]
    texts = [p.text for p in contents if getattr(p, "text", None)]
    return images, texts


def _model_calls(mock_client: MagicMock, model: str) -> list:
    """Returns generate_content call kwargs for one model (image generation vs. vision QA)."""
    return [c.kwargs for c in mock_client.models.generate_content.call_args_list if c.kwargs.get("model") == model]


def _lineup_client(images: list[bytes], qa_counts: list[tuple[int, int]]) -> MagicMock:
    """Mock GenAI client: image-model calls return `images` in order; QA calls return (children, pandas) counts."""
    image_queue, qa_queue = list(images), list(qa_counts)

    def _generate(**kwargs):
        if kwargs.get("model") == config.IMAGE_MODEL:
            return _image_response(image_queue.pop(0))
        children, pandas = qa_queue.pop(0)
        return MagicMock(text=json.dumps({**CLEAN_QA, "lily_count": children, "robo_count": pandas}))

    client = MagicMock()
    client.models.generate_content.side_effect = _generate
    return client


def _png_of_color(color: tuple[int, int, int], size: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _heritage_image_prompt(scene: str, features_char: bool = True) -> str:
    return prompts.construct_image_prompt(
        page_scene=scene,
        main_char_desc=prompts.HERITAGE_CHARACTER,
        style_desc=prompts.HERITAGE_STYLE,
        features_char=features_char,
        character_ids=HERITAGE_CAST,
    )


class TestHeritageRegistryAndTopics(unittest.TestCase):
    def test_heritage_cast_resolves_without_lily_or_robo(self):
        ids = sorted(c["id"] for c in config.get_active_characters(HERITAGE_CAST))
        self.assertEqual(ids, sorted(HERITAGE_CAST))

    def test_genre_alone_resolves_the_heritage_cast(self):
        ids = sorted(c["id"] for c in config.get_active_characters(None, genre="chinese_heritage"))
        self.assertEqual(ids, sorted(HERITAGE_CAST))

    def test_stem_default_cast_is_unchanged(self):
        ids = {c["id"] for c in config.get_active_characters(None)}
        self.assertTrue({"lily", "robo"} <= ids)
        self.assertFalse(ids & set(HERITAGE_CAST))

    def test_heritage_topics_are_registered_with_genre_cast_and_exact_folder(self):
        topics = {t["id"]: t for t in config.load_topics()}
        for topic_id, folder in (
            ("iron-rod-into-needle", "Iron Rod Into A Needle"),
            ("painting-the-dragons-eyes", "Painting The Dragons Eyes"),
        ):
            with self.subTest(topic=topic_id):
                self.assertEqual(topics[topic_id]["genre"], "chinese_heritage")
                self.assertEqual(topics[topic_id]["characters"], HERITAGE_CAST)
                # Exact match matters: update_topic_status() matches folder_name after publishing.
                self.assertEqual(topics[topic_id]["folder_name"], folder)

    def test_approved_heritage_reference_art_is_checked_in(self):
        # The pipeline silently regenerates missing refs, which would redesign the cast; guard the approved art.
        char_dir = config.PROJECT_ROOT / "assets" / "characters"
        for name in ("mei_ref.png", "ming_ref.png", "baobao_ref.png"):
            with self.subTest(asset=name):
                self.assertGreater((char_dir / name).stat().st_size, 10_000)
        self.assertTrue(config.HERITAGE_REFERENCE_SHEET_PATH.exists())
        refs = config.get_character_reference_paths(HERITAGE_CAST)
        self.assertEqual(refs, [config.HERITAGE_REFERENCE_SHEET_PATH])

    def test_next_pending_topic_can_be_filtered_by_genre(self):
        data = {
            "topics": [
                {"id": "computer-use", "topic_name": "computer use", "folder_name": "Agent Computer Use"},
                {
                    "id": "iron-rod-into-needle",
                    "genre": "chinese_heritage",
                    "characters": HERITAGE_CAST,
                    "topic_name": IRON_ROD_TOPIC,
                    "folder_name": "Iron Rod Into A Needle",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "topics.yaml"
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            self.assertEqual(config.get_next_pending_topic(path)["id"], "computer-use")
            self.assertEqual(
                config.get_next_pending_topic(path, genre="chinese_heritage")["id"], "iron-rod-into-needle"
            )


class TestHeritagePrompts(unittest.TestCase):
    def test_genre_detection_never_captures_stem_topics(self):
        self.assertTrue(prompts.is_heritage_genre(genre="chinese_heritage"))
        self.assertTrue(prompts.is_heritage_genre(character_ids=HERITAGE_CAST))
        self.assertTrue(prompts.is_heritage_genre(topic=IRON_ROD_TOPIC))
        stem_topics = [t["topic_name"] for t in config.DEFAULT_TOPICS]
        stem_topics += [t["topic_name"] for t in config.load_topics() if t.get("genre") != "chinese_heritage"]
        for topic in stem_topics:
            with self.subTest(topic=topic[:40]):
                self.assertFalse(prompts.is_heritage_genre(topic=topic))
        self.assertFalse(prompts.is_heritage_genre(character_ids=["lily", "robo", "mia"]))

    def test_story_prompt_uses_heritage_cast_and_proverb_corner(self):
        heritage = prompts.get_story_generation_prompt(IRON_ROD_TOPIC, "4-6", genre="chinese_heritage")
        self.assertIn(prompts.HERITAGE_SERIES_NAME, heritage)
        self.assertIn("Proverb Wisdom & Heritage Corner", heritage)
        self.assertIn("Mei (美美)", heritage)
        self.assertIn("铁杵磨成针", heritage)
        self.assertNotIn("chestnut-brown", heritage)
        self.assertNotIn("STEM Exploration Corner", heritage)

        stem = prompts.get_story_generation_prompt("agent sandbox", "4-6")
        self.assertIn("STEM Exploration Corner", stem)
        self.assertNotIn("Proverb Wisdom", stem)

    def test_image_prompts_cover_all_five_heritage_presence_modes(self):
        cases = [
            (TOGETHER_SCENE, True, "both", "STRICT CHINESE HERITAGE CHARACTER INVARIANTS"),
            (SOLO_SCENES["mei"], True, "mei_only", "SOLO MEI SCENE"),
            (SOLO_SCENES["ming"], True, "ming_only", "SOLO MING SCENE"),
            (SOLO_SCENES["baobao"], True, "baobao_only", "SOLO BAOBAO SCENE"),
            (WORLD_SCENE, False, "none", "PURE WORLD & OBJECT SCENE (NO CHARACTERS)"),
        ]
        for scene, features_char, mode, marker in cases:
            with self.subTest(mode=mode):
                self.assertEqual(
                    prompts.detect_page_character_mode(
                        scene, features_char=features_char, character_ids=HERITAGE_CAST
                    ),
                    mode,
                )
                prompt = _heritage_image_prompt(scene, features_char)
                self.assertIn(marker, prompt)
                self.assertIn("shuimo", prompt)
                self.assertNotIn("chestnut-brown", prompt)
                self.assertNotIn("equator seam", prompt)


class TestHeritageImageConditioning(unittest.TestCase):
    def test_each_solo_page_is_conditioned_only_on_its_own_character_reference(self):
        refs = {"mei": b"MEI-REF", "ming": b"MING-REF", "baobao": b"BAOBAO-REF"}
        headers = {
            "mei": "STRICT MEI CHARACTER MATCH REQUIREMENT",
            "ming": "STRICT MING CHARACTER MATCH REQUIREMENT",
            "baobao": "STRICT BAOBAO CHARACTER MATCH REQUIREMENT",
        }
        with tempfile.TemporaryDirectory() as tmp:
            char_dir = pathlib.Path(tmp) / "assets" / "characters"
            char_dir.mkdir(parents=True)
            for cid, data in refs.items():
                (char_dir / f"{cid}_ref.png").write_bytes(data)
            with (
                patch.object(config, "PROJECT_ROOT", pathlib.Path(tmp)),
                patch.object(
                    config, "HERITAGE_REFERENCE_SHEET_PATH", char_dir / "chinese_heritage_reference_sheet.png"
                ),
            ):
                for cid, scene in SOLO_SCENES.items():
                    with self.subTest(character=cid):
                        client = MagicMock()
                        client.models.generate_content.return_value = _image_response(b"PAGE")
                        gen = StorybookGenerator(drive_service=None, ai_client=client)
                        image = gen.generate_page_image(_heritage_image_prompt(scene), character_ids=HERITAGE_CAST)
                        images, texts = _sent_parts(client)
                        self.assertEqual(image, b"PAGE")
                        self.assertEqual(images, [refs[cid]])
                        self.assertTrue(texts[0].startswith(headers[cid]), texts[0][:120])


class TestHeritageReferenceSheetBootstrap(unittest.TestCase):
    REFS = {"mei": (230, 80, 70), "ming": (90, 190, 120), "baobao": (210, 120, 50)}

    def _bootstrap(self, client: MagicMock):
        """Runs ensure_heritage_reference_sheet() twice in a sandbox; returns (first, second, sheet, refs)."""
        refs = {cid: _png_of_color(color, 1024) for cid, color in self.REFS.items()}
        with tempfile.TemporaryDirectory() as tmp:
            char_dir = pathlib.Path(tmp) / "assets" / "characters"
            char_dir.mkdir(parents=True)
            for cid, data in refs.items():
                (char_dir / f"{cid}_ref.png").write_bytes(data)
            sheet_path = char_dir / "chinese_heritage_reference_sheet.png"
            gen = StorybookGenerator(drive_service=None, ai_client=client)
            with (
                patch.object(config, "PROJECT_ROOT", pathlib.Path(tmp)),
                patch.object(config, "HERITAGE_REFERENCE_SHEET_PATH", sheet_path),
            ):
                first = gen.ensure_heritage_reference_sheet()
                second = gen.ensure_heritage_reference_sheet()
            return first, second, sheet_path.read_bytes(), refs

    def test_master_sheet_is_composed_once_from_the_three_character_references(self):
        client = _lineup_client([_image_bytes("JPEG")], [(2, 1)])
        first, second, sheet, refs = self._bootstrap(client)
        image_calls = _model_calls(client, config.IMAGE_MODEL)
        self.assertTrue(sheet.startswith(b"\x89PNG"), "sheet must be a real PNG")
        self.assertEqual(first, second)
        self.assertEqual(len(image_calls), 1)
        contents = image_calls[0]["contents"]
        self.assertEqual(
            [p.inline_data.data for p in contents if getattr(p, "inline_data", None)],
            [refs["mei"], refs["ming"], refs["baobao"]],
        )
        prompt_text = " ".join(p.text for p in contents if getattr(p, "text", None))
        for name in ("Mei", "Ming", "Baobao"):
            self.assertIn(name, prompt_text)

    def test_master_sheet_is_regenerated_when_vision_qa_finds_a_missing_character(self):
        # Observed in production: the first lineup dropped Mei (1 child + 1 panda).
        rejected, accepted = _image_bytes("JPEG"), _png_of_color((10, 20, 30))
        client = _lineup_client([rejected, accepted], [(1, 1), (2, 1)])
        _, _, sheet, _ = self._bootstrap(client)
        self.assertEqual(len(_model_calls(client, config.IMAGE_MODEL)), 2)
        self.assertEqual(sheet, storybook._to_png_bytes(accepted))

    def test_master_sheet_falls_back_to_a_deterministic_lineup_of_the_approved_refs(self):
        client = _lineup_client([_image_bytes("JPEG")] * 3, [(1, 1)] * 3)
        _, _, sheet, _ = self._bootstrap(client)
        self.assertEqual(len(_model_calls(client, config.IMAGE_MODEL)), 3)
        with Image.open(io.BytesIO(sheet)) as im:
            self.assertEqual(im.size, (3 * 1024, 1024))
            self.assertEqual(im.convert("RGB").getpixel((512, 512)), self.REFS["mei"])
            self.assertEqual(im.convert("RGB").getpixel((1536, 512)), self.REFS["ming"])
            self.assertEqual(im.convert("RGB").getpixel((2560, 512)), self.REFS["baobao"])


class TestHeritageVisualQA(unittest.TestCase):
    def _run_qa(self, scene: str, features_char: bool, **counts):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text=json.dumps({**CLEAN_QA, **counts}))
        gen = StorybookGenerator(drive_service=None, ai_client=client)
        report = gen.evaluate_page_image_qa(
            _image_bytes("JPEG"),
            page_num=7,
            scene_description=scene,
            features_char=features_char,
            character_ids=HERITAGE_CAST,
        )
        _, texts = _sent_parts(client)
        return report, texts[-1]

    def test_together_page_passes_and_prompt_audits_the_heritage_cast(self):
        report, qa_prompt = self._run_qa(TOGETHER_SCENE, True, lily_count=2, robo_count=1)
        self.assertTrue(report["passed"], report["issues"])
        self.assertIn("Baobao", qa_prompt)
        self.assertNotIn("LILY & ROBO: DAY & NIGHT", qa_prompt)

    def test_solo_baobao_page_fails_when_the_children_appear(self):
        report, _ = self._run_qa(SOLO_SCENES["baobao"], True, lily_count=2, robo_count=1)
        self.assertFalse(report["passed"])

    def test_solo_mei_page_fails_when_baobao_appears(self):
        report, _ = self._run_qa(SOLO_SCENES["mei"], True, lily_count=1, robo_count=1)
        self.assertFalse(report["passed"])

    def test_world_only_page_fails_when_any_character_appears(self):
        report, _ = self._run_qa(WORLD_SCENE, False, lily_count=0, robo_count=1)
        self.assertFalse(report["passed"])

    def test_duplicated_baobao_fails_a_together_page(self):
        report, _ = self._run_qa(TOGETHER_SCENE, True, lily_count=2, robo_count=2)
        self.assertFalse(report["passed"])


class TestHeritagePublishingPipeline(unittest.TestCase):
    def _run(self, topic_cfg: dict) -> dict:
        with (
            patch.object(publish_pipeline, "StorybookGenerator") as gen_cls,
            patch.object(publish_pipeline, "init_drive_service", return_value=None),
            patch.object(publish_pipeline, "get_or_create_root_drive_folder", return_value=None),
            patch.object(config, "update_topic_status"),
            tempfile.TemporaryDirectory() as tmp,
        ):
            gen_cls.return_value.run_fast_publishing_workflow.return_value = {"status": "success", "title": "T"}
            publish_pipeline.run_pipeline(
                [topic_cfg], no_audio=True, no_drive=True, output_base_dir=tmp, export_vids=False
            )
            return gen_cls.return_value.run_fast_publishing_workflow.call_args.kwargs

    def test_heritage_topic_publishes_with_heritage_style_cast_and_genre(self):
        kwargs = self._run(
            {
                "id": "iron-rod-into-needle",
                "genre": "chinese_heritage",
                "characters": HERITAGE_CAST,
                "topic_name": IRON_ROD_TOPIC,
                "folder_name": "Iron Rod Into A Needle",
                "age_group": "4-6",
            }
        )
        self.assertEqual(kwargs["override_style"], prompts.HERITAGE_STYLE)
        self.assertEqual(kwargs["override_char"], prompts.HERITAGE_CHARACTER)
        self.assertEqual(kwargs["genre"], "chinese_heritage")
        self.assertEqual(kwargs["character_ids"], HERITAGE_CAST)

    def test_stem_topic_keeps_the_series_style_and_cast(self):
        kwargs = self._run(
            {
                "id": "computer-use",
                "topic_name": "computer use (how AI agents learn to click, type, and navigate)",
                "folder_name": "Agent Computer Use",
                "age_group": "4-6",
            }
        )
        self.assertEqual(kwargs["override_style"], prompts.SERIES_STYLE)
        self.assertEqual(kwargs["override_char"], prompts.SERIES_CHARACTER)
        self.assertIsNone(kwargs.get("genre"))

    def test_cli_genre_flag_selects_the_next_heritage_topic(self):
        heritage_topic = {
            "id": "iron-rod-into-needle",
            "genre": "chinese_heritage",
            "characters": HERITAGE_CAST,
            "topic_name": IRON_ROD_TOPIC,
            "folder_name": "Iron Rod Into A Needle",
        }
        with (
            patch.object(config, "get_next_pending_topic", return_value=heritage_topic) as next_topic,
            patch.object(
                publish_pipeline, "run_pipeline", return_value=[{"status": "success", "title": "T"}]
            ) as run_pipeline,
        ):
            rc = publish_pipeline.main(["--next", "--genre", "chinese_heritage", "--no-drive"])
        self.assertEqual(rc, 0)
        next_topic.assert_called_once_with(genre="chinese_heritage")
        self.assertEqual(run_pipeline.call_args.kwargs["topics"], [heritage_topic])
        self.assertEqual(run_pipeline.call_args.kwargs["genre"], "chinese_heritage")

    def test_cli_all_with_genre_only_publishes_that_genre(self):
        with patch.object(
            publish_pipeline,
            "run_pipeline",
            side_effect=lambda **kw: [{"status": "success", "title": "T"} for _ in kw["topics"]],
        ) as run_pipeline:
            publish_pipeline.main(["--all", "--genre", "chinese_heritage", "--no-drive"])
        topics = run_pipeline.call_args.kwargs["topics"]
        self.assertIn("iron-rod-into-needle", [t["id"] for t in topics])
        self.assertTrue(all(t.get("genre") == "chinese_heritage" for t in topics))


class TestHeritageNarrationAndMetadata(unittest.TestCase):
    STORY = {
        "genre": "chinese_heritage",
        "title": "The Iron Rod and the Needle",
        "title_es": "La barra de hierro y la aguja",
        "title_zh": "铁杵磨成针",
        "pages": [
            {
                "page_number": 1,
                "text": "The Iron Rod and the Needle\nWritten by Enrique Chan",
                "text_es": "La barra de hierro y la aguja\nEscrito por Enrique Chan",
                "text_zh": "铁杵磨成针\n作者：Enrique Chan",
            },
            {
                "page_number": 2,
                "text": "Mei and Ming followed Baobao into the bamboo grove.",
                "text_es": "Mei y Ming siguieron a Baobao hasta el bosque de bambú.",
                "text_zh": "美美和明明跟着宝宝走进了竹林。",
            },
        ],
    }

    def test_chinese_narration_uses_the_heritage_series_title(self):
        script = audio_generator.build_chinese_narration_script(self.STORY)
        self.assertIn(prompts.HERITAGE_SERIES_NAME_ZH, script)
        self.assertIn("美美和明明跟着宝宝走进了竹林。", script)
        self.assertNotIn("莉莉", script)

    def test_english_and_spanish_narration_feature_the_heritage_cast(self):
        for script in (
            audio_generator.build_english_narration_script(self.STORY),
            audio_generator.build_spanish_narration_script(self.STORY),
        ):
            self.assertIn("Mei", script)
            self.assertNotIn("Lily", script)

    def test_kdp_description_prompt_describes_the_heritage_series(self):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text="KDP")
        gen = StorybookGenerator(drive_service=None, ai_client=client)
        gen.generate_kdp_description(
            "The Iron Rod and the Needle", IRON_ROD_TOPIC, "4-6", language="English", genre="chinese_heritage"
        )
        sent = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn(prompts.HERITAGE_SERIES_NAME, sent)
        self.assertNotIn("Lily", sent)

    def test_kdp_metadata_uses_the_series_of_each_genre(self):
        heritage = storybook.build_kdp_metadata(
            title="T", topic=IRON_ROD_TOPIC, language="English", age_group="4-6", genre="chinese_heritage"
        )
        self.assertEqual(heritage["series"], HERITAGE_SERIES)
        self.assertFalse(any("Lily" in k for k in heritage["keywords"]))
        stem = storybook.build_kdp_metadata(
            title="T", topic="agent sandbox (a safe digital playground)", language="English", age_group="4-6"
        )
        self.assertEqual(stem["series"], f"{prompts.SERIES_BRAND}: {prompts.SERIES_NAME}")


class TestHeritageVidsVoiceover(unittest.TestCase):
    NAMES = {"en": "Mei", "es": "Mei", "pt": "Mei", "fr": "Mei", "it": "Mei", "de": "Mei", "zh": "美美", "ja": "メイ", "ko": "메이"}

    @staticmethod
    def _story() -> dict:
        pages = [{"page_number": n, "text": f"Story page {n}."} for n in range(1, 21)]
        pages[0]["text"] = "The Iron Rod and the Needle\nWritten by Enrique Chan"
        pages[1]["text"] = (
            "Curious Little Minds Publishing\nText and illustrations copyright © 2026 Enrique Chan. All rights reserved."
        )
        pages[19]["text"] = "The End"
        return {"genre": "chinese_heritage", "title": "The Iron Rod and the Needle", "author": "Enrique Chan", "pages": pages}

    def test_presenter_lines_feature_the_heritage_cast_in_all_nine_languages(self):
        story = self._story()
        for lang, name in self.NAMES.items():
            for page in (story["pages"][0], story["pages"][1], story["pages"][19]):
                with self.subTest(lang=lang, page=page["page_number"]):
                    line = vids_exporter.extract_page_voiceover(story, page, 20, lang=lang)
                    self.assertIn(name, line)
                    for stem_name in STEM_NAMES:
                        self.assertNotIn(stem_name, line)

    def test_story_pages_stay_verbatim(self):
        story = self._story()
        self.assertEqual(vids_exporter.extract_page_voiceover(story, story["pages"][6], 20, lang="en"), "Story page 7.")


class TestHeritageStoryValidationAndWorkflow(unittest.TestCase):
    def test_validation_preserves_heritage_names_series_and_style(self):
        gen = StorybookGenerator(drive_service=None)
        raw = {
            "title": "The Iron Rod and the Needle",
            "pages": [
                {"page_number": 1, "text": "Cover", "features_main_character": True, "scene_description": TOGETHER_SCENE},
                {
                    "page_number": 2,
                    "text": "Mei and Ming followed Baobao into the bamboo grove. *SHHH-SHHH!*",
                    "features_main_character": True,
                    "scene_description": "Baobao leading Mei and Ming through a misty bamboo grove.",
                },
                {"page_number": 3, "text": "The End", "features_main_character": False, "scene_description": WORLD_SCENE},
            ],
        }
        out = gen.validate_and_correct_story(
            raw, topic=IRON_ROD_TOPIC, language="English", character_ids=HERITAGE_CAST, genre="chinese_heritage"
        )
        self.assertEqual(out["genre"], "chinese_heritage")
        self.assertEqual(out["series"], HERITAGE_SERIES)
        self.assertIn("Mei and Ming followed Baobao", out["pages"][1]["text"])
        self.assertIn("shuimo", out["pages"][1]["image_prompt"])
        self.assertNotIn("chestnut-brown", out["pages"][1]["image_prompt"])
        self.assertIn("PURE WORLD & OBJECT SCENE (NO CHARACTERS)", out["pages"][2]["image_prompt"])

    @patch("storybook.StorybookGenerator.ensure_heritage_reference_sheet")
    @patch("storybook.StorybookGenerator.ensure_character_reference_image")
    @patch("storybook.StorybookGenerator.generate_story")
    @patch("storybook.StorybookGenerator.generate_page_image")
    @patch("storybook.StorybookGenerator.overlay_text_on_image")
    @patch("storybook.StorybookGenerator.generate_kdp_description")
    def test_heritage_workflow_bootstraps_references_and_heritage_metadata(
        self, mock_kdp, mock_overlay, mock_gen_img, mock_gen_story, _mock_ensure_char, mock_ensure_sheet
    ):
        jpeg = _image_bytes("JPEG")
        mock_gen_story.return_value = {
            "title": "The Iron Rod and the Needle",
            "genre": "chinese_heritage",
            "pages": [
                {"page_number": 1, "text": "Cover", "features_main_character": True, "scene_description": TOGETHER_SCENE},
                {"page_number": 2, "text": "The End", "features_main_character": False, "scene_description": WORLD_SCENE},
            ],
        }
        mock_gen_img.return_value = jpeg
        mock_overlay.return_value = jpeg
        mock_kdp.return_value = "KDP"
        gen = StorybookGenerator(drive_service=None)
        with tempfile.TemporaryDirectory() as tmp:
            res = gen.run_fast_publishing_workflow(
                topic=IRON_ROD_TOPIC,
                genre="chinese_heritage",
                max_pages=2,
                generate_audio=False,
                export_vids=False,
                output_dir=tmp,
            )
            meta = json.loads(pathlib.Path(res["kdp_metadata_path"]).read_text(encoding="utf-8"))
        self.assertTrue(mock_ensure_sheet.called)
        self.assertEqual(mock_gen_story.call_args.kwargs["genre"], "chinese_heritage")
        self.assertEqual(mock_gen_story.call_args.kwargs["character_ids"], HERITAGE_CAST)
        self.assertEqual(mock_kdp.call_args.kwargs.get("genre"), "chinese_heritage")
        self.assertEqual(meta["series"], HERITAGE_SERIES)
        self.assertTrue(mock_gen_img.call_args_list)
        for call in mock_gen_img.call_args_list:
            self.assertEqual(call.kwargs.get("character_ids"), HERITAGE_CAST)


if __name__ == "__main__":
    unittest.main()
