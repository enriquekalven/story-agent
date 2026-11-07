"""Unit tests verifying repository bug fixes, security hardening, and performance optimizations."""

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from PIL import Image

import audio_generator
import config
import drive_sync
import epub_packager
import kdp_epub_optimize
import kdp_listings
import main as main_cli
import refresh_tokens
import vids_book_flow as vbf
import vids_zh_avatars as vza
from storybook import StorybookGenerator


def _jpeg_bytes(width: int = 240, height: int = 240, color: tuple[int, int, int] = (90, 140, 210)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    # Add gradient detail so JPEG quality levels produce distinct byte sizes.
    px = img.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = ((x * 7 + y * 3) % 256, (x * 5 + y * 11) % 256, (x * 13 + y * 17) % 256)
    img.save(buf, format="JPEG", quality=95)
    img.close()
    return buf.getvalue()


def _write_wav(path: str, seconds: float = 1.5, rate: int = 24000) -> None:
    frames = int(seconds * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)


class DriveSyncImprovementsTests(unittest.TestCase):
    def test_escape_drive_q_value_escapes_backslashes_and_quotes(self):
        raw = r"Mei's \ 'Book'"
        escaped = drive_sync._escape_drive_q_value(raw)
        self.assertEqual(escaped, r"Mei\'s \\ \'Book\'")

    def test_no_overwrite_skips_existing_without_creating_duplicate(self):
        svc = mock.MagicMock()
        files_api = svc.files.return_value
        index = {
            "story.json": {
                "id": "EXISTING_ID",
                "name": "story.json",
                "mimeType": "application/json",
                "md5Checksum": "deadbeef",
                "webViewLink": "https://drive.google.com/file/d/EXISTING_ID/view",
            }
        }
        fid = drive_sync.upsert_bytes_to_drive(
            svc,
            data_bytes=b'{"changed": true}',
            filename="story.json",
            folder_id="FOLDER_1",
            mimetype="application/json",
            overwrite_existing=False,
            folder_index=index,
        )
        self.assertEqual(fid, "EXISTING_ID")
        files_api.create.assert_not_called()
        files_api.update.assert_not_called()

        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "story.json")
            with open(local, "wb") as f:
                f.write(b'{"changed": true}')
            existing_id = drive_sync.upsert_local_file(
                svc,
                local_file_path=local,
                folder_id="FOLDER_1",
                overwrite_existing=False,
                folder_index=index,
            )
        self.assertEqual(existing_id, "EXISTING_ID")
        files_api.create.assert_not_called()
        files_api.update.assert_not_called()

    def test_md5_match_skips_redundant_drive_upload(self):
        payload = b"unchanged-ebook-bytes"
        md5_hex = hashlib.md5(payload).hexdigest()
        svc = mock.MagicMock()
        files_api = svc.files.return_value
        index = {
            "book.epub": {
                "id": "EPUB_1",
                "name": "book.epub",
                "mimeType": "application/epub+zip",
                "md5Checksum": md5_hex,
                "webViewLink": "https://drive.google.com/file/d/EPUB_1/view",
            }
        }
        fid = drive_sync.upsert_bytes_to_drive(
            svc,
            data_bytes=payload,
            filename="book.epub",
            folder_id="FOLDER_1",
            mimetype="application/epub+zip",
            overwrite_existing=True,
            folder_index=index,
        )
        self.assertEqual(fid, "EPUB_1")
        files_api.update.assert_not_called()
        files_api.create.assert_not_called()

    def test_recursive_folder_sync_handles_nested_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_dir = os.path.join(tmp, "Test Book")
            nested = os.path.join(book_dir, "google_vids", "zh_slides")
            os.makedirs(nested)
            with open(os.path.join(book_dir, "story.json"), "w", encoding="utf-8") as f:
                json.dump({"title": "Test Book", "pages": []}, f)
            with open(os.path.join(nested, "page_01_stage.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff\xd9")

            uploaded = []

            def fake_upsert(svc, local_file_path, folder_id, **kwargs):
                uploaded.append((os.path.basename(local_file_path), folder_id))
                return f"id_{len(uploaded)}"

            svc = mock.MagicMock()
            with (
                mock.patch.object(
                    drive_sync,
                    "get_or_create_folder",
                    side_effect=lambda s, name, parent_id=None: f"fld:{name}",
                ),
                mock.patch.object(drive_sync, "list_folder_index", return_value={}),
                mock.patch.object(drive_sync, "upsert_local_file", side_effect=fake_upsert),
            ):
                root_id = drive_sync.sync_directory_to_drive(tmp, drive_service=svc)

            self.assertIsNotNone(root_id)
            self.assertIn(("story.json", "fld:Test Book"), uploaded)
            self.assertIn(("page_01_stage.jpg", "fld:zh_slides"), uploaded)


class RefreshTokensSecurityTests(unittest.TestCase):
    def test_write_private_file_enforces_0600_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".netrc"
            target.write_text("old", encoding="utf-8")
            os.chmod(target, 0o644)

            refresh_tokens._write_private_file(str(target), "machine github.com login token password secret\n")
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertIn("secret", target.read_text(encoding="utf-8"))

    def test_main_returns_nonzero_when_token_fetch_fails(self):
        failed_proc = mock.MagicMock(returncode=1, stderr="gcloud not authenticated", stdout="")
        with mock.patch.object(refresh_tokens.subprocess, "run", return_value=failed_proc):
            rc = refresh_tokens.main()
        self.assertEqual(rc, 1)


class KdpOptimizationAndListingCacheTests(unittest.TestCase):
    def test_epub_optimize_binary_search_picks_highest_fitting_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_epub = Path(tmp) / "src.epub"
            pages = [(f"Page {i}", _jpeg_bytes(480, 480)) for i in range(1, 5)]
            raw_bytes = epub_packager.create_epub("Optimization Test", pages, "English", author="Enrique Chan")
            self.assertEqual(epub_packager.validate_epub_bytes(raw_bytes), {"valid": True, "errors": []})
            src_epub.write_bytes(raw_bytes)

            mimetype_bytes, entries = kdp_epub_optimize._load_epub_entries(src_epub)
            try:
                size_85 = len(kdp_epub_optimize._build_from_entries(mimetype_bytes, entries, 85))
                size_78 = len(kdp_epub_optimize._build_from_entries(mimetype_bytes, entries, 78))
            finally:
                kdp_epub_optimize._close_entry_images(entries)

            target_mb = (size_78 + 64) / 1e6
            quality, blob = kdp_epub_optimize.plan_for(src_epub, target_mb=target_mb)
            if size_85 > size_78 + 64:
                self.assertIn(quality, (82, 80, 78))
                self.assertGreaterEqual(quality, 78)
            self.assertLessEqual(len(blob) / 1e6, target_mb)
            self.assertEqual(epub_packager.validate_epub_bytes(blob), {"valid": True, "errors": []})

    def test_kdp_description_uses_disk_cache_until_regen_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            book_folder = out_root / "Agent Sandbox"
            book_folder.mkdir()
            valid_html = "<p><b>A cozy bedtime STEM adventure!</b> " + ("Lily and Robo explore safe playpens together. " * 25) + "</p>"
            cached_path = book_folder / "kdp_description.txt"
            cached_path.write_text(valid_html, encoding="utf-8")

            fake_listing = mock.MagicMock(folder="Agent Sandbox", kind="stem")
            with mock.patch.object(kdp_listings, "OUTPUT_DIR", out_root):
                desc = kdp_listings.generate_description(fake_listing, regen=False)
            self.assertEqual(desc, valid_html.strip())


class AudioGenreAndMainCliTests(unittest.TestCase):
    def test_spanish_gemini_tts_uses_spanish_language_prompt(self):
        story = {
            "title": "The Night-Light Helpers",
            "title_es": "Los Ayudantes Nocturnos",
            "pages": [{"page_number": 3, "text": "Hi", "text_es": "Hola amigos."}],
        }
        captured_langs = []

        def fake_gemini_tts(script_text, out_wav, voice_name="Aoede", model_id="", language="Mandarin Chinese"):
            captured_langs.append(language)
            _write_wav(out_wav, seconds=0.5)
            return True

        def fake_mix(voice_aiff_path="", output_mp3_path="", bgm_path="", bgm_volume=0.12, **kwargs):
            Path(output_mp3_path).write_bytes(b"ID3")
            return True

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(audio_generator, "synthesize_speech_gemini_tts", side_effect=fake_gemini_tts),
            mock.patch.object(audio_generator, "mix_voice_and_bgm", side_effect=fake_mix),
        ):
            out_mp3 = os.path.join(tmp, "es.mp3")
            bg = os.path.join(tmp, "bg.mp3")
            Path(bg).write_bytes(b"ID3")
            ok = audio_generator.generate_spanish_audio_file(story, out_mp3, bg, tts_provider="gemini")
            self.assertTrue(ok)
        self.assertEqual(captured_langs, ["Spanish"])

    def test_chinese_heritage_narration_outro_uses_series_title(self):
        story = {
            "title": "Little Sparks",
            "title_zh": "铁杵成针",
            "genre": "chinese_heritage",
            "pages": [{"page_number": 3, "text_zh": "老婆婆在溪边磨铁棒。"}],
        }
        script = audio_generator.build_chinese_narration_script(story)
        outro_lines = script.splitlines()[-1]
        self.assertIn("《好奇小脑瓜·美美、明明与宝宝的成语故事》", outro_lines)
        self.assertNotIn("这是《好奇小脑瓜》陪伴", outro_lines)

    def test_storybook_genre_propagation_and_parallel_translations(self):
        gen = StorybookGenerator(drive_service=None)
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with tempfile.TemporaryDirectory() as tmp:
            heritage_sheet = Path(tmp) / "heritage_sheet.png"
            heritage_sheet.write_bytes(fake_png)
            with mock.patch.object(config, "HERITAGE_REFERENCE_SHEET_PATH", heritage_sheet):
                refs = gen._load_character_reference_images(character_ids=None, genre="chinese_heritage")
            self.assertEqual(refs, [fake_png])

        call_order = []
        name_to_code = {"Spanish": "es", "Chinese Simplified": "zh", "Cantonese": "yue"}

        def fake_translate(sd, lang_name):
            lang_code = name_to_code[lang_name]
            call_order.append(lang_code)
            if lang_code == "yue":
                # Verify zh translation already ran and populated story_data before yue started!
                self.assertIn("title_zh", sd)
            return {
                "title": f"{sd['title']} ({lang_code})",
                "pages": [
                    {"page_number": 1, "text": "Cover"},
                    {"page_number": 2, "text": f"Page 2 {lang_code}"},
                ],
            }

        story_data = {
            "title": "Test Story",
            "pages": [
                {"page_number": 1, "text": "Cover"},
                {"page_number": 2, "text": "Page 2"},
            ],
        }
        with mock.patch.object(gen, "translate_story", side_effect=fake_translate):
            gen.ensure_multilingual_translations(story_data, target_langs=["es", "zh", "yue"])

        self.assertEqual(story_data["title_es"], "Test Story (es)")
        self.assertEqual(story_data["title_zh"], "Test Story (zh)")
        self.assertEqual(story_data["title_yue"], "Test Story (yue)")
        self.assertEqual(story_data["pages"][1]["text_yue"], "Page 2 yue")
        self.assertLess(call_order.index("zh"), call_order.index("yue"))

    def test_main_cli_routes_default_to_fast_publishing_workflow(self):
        fake_gen = mock.MagicMock()
        with (
            mock.patch.object(sys, "argv", ["main.py", "--topic", "agent-sandbox", "--max-pages", "2", "--no-drive", "--no-audio"]),
            mock.patch.object(drive_sync, "init_drive_service", return_value=None),
            mock.patch.object(main_cli, "StorybookGenerator", return_value=fake_gen),
        ):
            main_cli.main()
        fake_gen.run_fast_publishing_workflow.assert_called_once()
        kwargs = fake_gen.run_fast_publishing_workflow.call_args.kwargs
        self.assertEqual(kwargs["max_pages"], 2)
        self.assertFalse(kwargs["generate_audio"])
        self.assertEqual(kwargs["genre"], "stem")


class VidsFlowAndGeneralizedAvatarTests(unittest.TestCase):
    def test_exec_js_uses_single_osascript_invocation_with_extra_main_js(self):
        with (
            mock.patch.object(vbf, "_main_chrome_pid", return_value=4242),
            mock.patch.object(vbf.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = mock.MagicMock(stdout="PASTED\n")
            res = vbf.exec_js("DOC_123", "document.title", extra_main_js="javascript:void(0);")
        self.assertEqual(res, "PASTED")
        self.assertEqual(mock_run.call_count, 1)

    def test_build_timed_avatar_mp4_caches_files_above_650kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = os.path.join(tmp, "page_01.wav")
            mp4_path = os.path.join(tmp, "scene_01.mp4")
            base_path = os.path.join(tmp, "base.mp4")
            _write_wav(wav_path, seconds=2.0)
            Path(base_path).write_bytes(b"\x00" * 20000)
            # Write an 800 KB cached MP4 newer than wav_path (previously >650 KB forced re-encode!)
            Path(mp4_path).write_bytes(b"\x00" * 800_000)
            now = os.path.getmtime(wav_path) + 5.0
            os.utime(mp4_path, (now, now))

            with mock.patch.object(vbf.subprocess, "run") as mock_ffmpeg:
                dur = vbf.build_timed_avatar_mp4(base_path, wav_path, mp4_path)
            self.assertAlmostEqual(dur, 2.0, places=2)
            mock_ffmpeg.assert_not_called()

    def test_vids_zh_avatars_supports_all_10_languages_with_fallback(self):
        self.assertEqual(set(vza.MP4_AVATAR_LANGS), {"en", "es", "zh", "yue", "pt", "ja", "ko", "fr", "it", "de"})
        with (
            mock.patch.object(audio_generator, "synthesize_speech_cloud_tts", return_value=False) as cloud_tts,
            mock.patch.object(audio_generator, "synthesize_speech_gemini_tts", return_value=True) as gemini_tts,
        ):
            ok = vza.synthesize("fr", "Bonjour les amis", "/tmp/fr.wav", "Aoede")
        self.assertTrue(ok)
        cloud_tts.assert_called_once_with(
            "Bonjour les amis", "/tmp/fr.wav", language_code="fr-FR", voice_name="fr-FR-Chirp3-HD-Aoede"
        )
        gemini_tts.assert_called_once_with(
            "Bonjour les amis", "/tmp/fr.wav", voice_name="Aoede", language="French"
        )


if __name__ == "__main__":
    unittest.main()
