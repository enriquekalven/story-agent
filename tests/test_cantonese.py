"""Hermetic tests for Cantonese (yue) support and the script-paste safety net (no network, no browser)."""

import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import audio_generator
import vids_book_pipeline as vbp
import vids_drive_sync as vds
import vids_exporter
from storybook import StorybookGenerator


def _story():
    pages = []
    for n in range(1, 21):
        pages.append({"page_number": n, "text": f"English page {n}", "text_yue": f"粵語第{n}頁"})
    pages[0]["text_yue"] = "花燈小馬大巡遊\n作者：Enrique Chan"
    pages[1]["text"] = "Curious Little Minds Publishing\nText and illustrations copyright © 2026 Enrique Chan."
    pages[1]["text_yue"] = "好奇小腦瓜出版社\n圖文版權所有 © 2026 Enrique Chan。保留所有權利。"
    pages[18]["text_yue"] = "科學探索小角落！\n1. 第一題？\n2. 第二題？\n活動：試吓啦！"
    pages[19]["text"] = "The End"
    pages[19]["text_yue"] = "完"
    return {"title": "The Lantern-Parade Horse", "title_yue": "花燈小馬大巡遊", "author": "Enrique Chan", "pages": pages}


class CantoneseVoiceoverTests(unittest.TestCase):
    def setUp(self):
        self.story = _story()

    def vo(self, n):
        return vids_exporter.extract_page_voiceover(self.story, self.story["pages"][n - 1], 20, lang="yue")

    def test_cover_intro_is_cantonese_with_localized_title(self):
        line = self.vo(1)
        self.assertIn("歡迎嚟到《好奇小腦瓜》", line)
        self.assertIn("《花燈小馬大巡遊》", line)

    def test_colophon_page_becomes_world_intro(self):
        self.assertTrue(self.vo(2).startswith("喺呢個溫馨又奇妙嘅世界入面"))

    def test_stem_corner_header_is_not_read_twice(self):
        line = self.vo(19)
        self.assertTrue(line.startswith("而家嚟到我哋嘅科學探索小角落！一齊諗吓："))
        self.assertEqual(line.count("科學探索小角落"), 1)
        self.assertIn("活動：試吓啦！", line)

    def test_last_page_sign_off(self):
        self.assertIn("早唞啦", self.vo(20))

    def test_story_pages_are_verbatim(self):
        self.assertEqual(self.vo(7), "粵語第7頁")

    def test_registered_language(self):
        self.assertEqual(vids_exporter.SUPPORTED_LANG_LABELS["yue"], "Cantonese")
        self.assertEqual(vids_exporter.SUPPORTED_XML_LANGS["yue"], "zh-HK")


class CjkWrapTests(unittest.TestCase):
    def test_closing_punctuation_never_starts_a_line(self):
        text = "「樂樂，快啲睇呀！」莉莉驚訝咁叫。喺廣場中間，企咗一隻金色嘅花燈小馬，佢係用摺紙整成嘅。"
        lines = StorybookGenerator._wrap_text_lines(text, max_chars_cjk=22)
        self.assertEqual("".join(lines), text)
        for line in lines[1:]:
            self.assertNotIn(line[0], "，。！？、；：」』）》")

    def test_short_text_unchanged(self):
        self.assertEqual(StorybookGenerator._wrap_text_lines("完"), ["完"])


class CloudTtsTests(unittest.TestCase):
    def test_requests_explicit_cantonese_locale_and_writes_wav(self):
        wav = b"RIFF" + b"\x00" * 2000
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["headers"] = dict(req.header_items())
            return io.BytesIO(json.dumps({"audioContent": base64.b64encode(wav).decode()}).encode())

        creds = mock.MagicMock(token="tok")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("google.auth.default", return_value=(creds, "p")), \
                mock.patch.object(audio_generator.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = os.path.join(tmp, "p.wav")
            self.assertTrue(audio_generator.synthesize_speech_cloud_tts("早唞啦", out))
            self.assertEqual(open(out, "rb").read(), wav)
        self.assertEqual(captured["body"]["voice"], {"languageCode": "yue-HK", "name": "yue-HK-Chirp3-HD-Aoede"})
        self.assertEqual(captured["body"]["input"]["text"], "早唞啦")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer tok")

    def test_failure_returns_false(self):
        with mock.patch("google.auth.default", side_effect=RuntimeError("no adc")):
            self.assertFalse(audio_generator.synthesize_speech_cloud_tts("x", "/nonexistent/x.wav"))


class CloneFallbackTests(unittest.TestCase):
    def test_cantonese_clones_from_chinese_project(self):
        self.assertEqual(vbp.CLONE_FALLBACK["yue"], "zh")
        self.assertNotIn("yue", vbp.AVATAR_LANGS)  # Vids' AI avatar can't speak Cantonese: MP4 clips instead


class StrayTextTests(unittest.TestCase):
    SCRIPT = "Grazie per aver seguito Lily e Robo"

    def test_deletes_only_boxes_holding_script_text(self):
        calls = []
        boxes = [json.dumps(["GrazieperaverseguitoLilyeRobo"]), json.dumps([])]

        def fake_exec(doc_id, js, *a, **k):
            if js is vbp.CANVAS_TEXT_JS:
                return boxes.pop(0)
            calls.append(js)
            return "TEXT_SELECTED" if js is vbp.SELECT_TEXT_BOX_JS else "DELETED"

        with mock.patch.object(vbp.vbf, "exec_js", side_effect=fake_exec), mock.patch.object(vbp.time, "sleep"):
            log = vbp.remove_stray_text("DOC", self.SCRIPT)
        self.assertEqual(log, ["TEXT_SELECTED", "DELETED"])
        self.assertEqual(calls, [vbp.SELECT_TEXT_BOX_JS, vbp.EDIT_DELETE_JS])

    def test_leaves_unrelated_text_alone(self):
        with mock.patch.object(vbp.vbf, "exec_js", return_value=json.dumps(["Someothercaption"])) as ex:
            self.assertEqual(vbp.remove_stray_text("DOC", self.SCRIPT), [])
        ex.assert_called_once()

    def test_never_deletes_when_selection_is_not_a_text_box(self):
        def fake_exec(doc_id, js, *a, **k):
            if js is vbp.CANVAS_TEXT_JS:
                return json.dumps(["GrazieperaverseguitoLilyeRobo"])
            return "WRONG_SELECTION"

        with mock.patch.object(vbp.vbf, "exec_js", side_effect=fake_exec) as ex:
            self.assertEqual(vbp.remove_stray_text("DOC", self.SCRIPT), ["WRONG_SELECTION"])
        self.assertNotIn(vbp.EDIT_DELETE_JS, [c.args[1] for c in ex.call_args_list])


class DriveSyncLangFilterTests(unittest.TestCase):
    def test_single_language_sync_keeps_other_languages_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = os.path.join(tmp, "Book")
            os.makedirs(os.path.join(book, "google_vids"))
            json.dump({"title": "T", "pages": []}, open(os.path.join(book, "story.json"), "w"))
            json.dump({"google_vids": {"decks": {"en": "en.pptx"}, "drive_vids_urls": {"en": "u-en"}}},
                      open(os.path.join(book, "manifest.json"), "w"))
            json.dump({"yue": {"id": "Y", "url": "old"}, "zh": {"id": "Z", "url": "z"}},
                      open(os.path.join(book, "google_vids", "vids_projects.json"), "w"))
            files = mock.MagicMock()
            files.get.return_value.execute.return_value = {"id": "Y", "parents": ["F"], "resourceKey": None}
            drive = mock.MagicMock()
            drive.files.return_value = files
            with mock.patch.object(vds.drive_sync, "init_drive_service", return_value=drive), \
                    mock.patch.object(vds.drive_sync, "get_or_create_folder", return_value="SUB"):
                gv = vds.sync_book(book, folder_id="F", vids_only=True, langs=["yue"])
            self.assertEqual(gv["decks"], {"en": "en.pptx"})
            self.assertEqual(gv["drive_vids_urls"], {"en": "u-en", "yue": "https://docs.google.com/videos/d/Y/edit"})
            self.assertEqual(files.get.call_count, 1)  # zh project untouched


class ClipReplaceSafetyTests(unittest.TestCase):
    def test_clip_in_place_requires_the_new_clip_length(self):
        import vids_zh_avatars as vza

        with mock.patch.object(vza.vbf, "exec_js", return_value="[17.6]"):
            self.assertFalse(vza.clip_in_place("DOC", 14.16, wait=0))  # old Mandarin clip still playing
        with mock.patch.object(vza.vbf, "exec_js", return_value="[14.17]"):
            self.assertTrue(vza.clip_in_place("DOC", 14.16, wait=0))

    def test_never_uploads_when_navigation_is_unconfirmed(self):
        import vids_zh_avatars as vza

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "google_vids"))
            json.dump({"pages": [{"page_number": 1}]}, open(os.path.join(tmp, "story.json"), "w"))
            with mock.patch.object(vbp, "ensure_tab", return_value=True), \
                    mock.patch.object(vza.vbf, "navigate_to_scene", return_value="TAB_NOT_FOUND"), \
                    mock.patch.object(vza.vcf, "replace_scene_avatar_video") as upload, \
                    mock.patch.object(vza.time, "sleep"):
                vza.replace_all(tmp, "DOC", lang="yue")
            upload.assert_not_called()
            self.assertFalse(os.path.exists(vza.state_path(tmp, "yue")))


if __name__ == "__main__":
    unittest.main()
