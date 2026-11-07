"""Hermetic tests for the pure-logic parts of the Vids layout + book pipeline tools (no browser needed)."""

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import vids_avatar_layout as val
import vids_book_flow as vbf
import vids_book_pipeline as vbp
import vids_drive_sync as vds


def _scene(n, has_avatar=True):
    return {"scene": n, "slide_id": f"s{n}", "avatar": {"x": 0, "y": 0, "w": 1, "h": 1} if has_avatar else None}


class PageToSceneTests(unittest.TestCase):
    def test_twenty_scene_doc_maps_one_to_one(self):
        scenes = [_scene(i) for i in range(1, 21)]
        self.assertEqual(val.page_to_scene(scenes), {p: p for p in range(1, 21)})

    def test_twenty_one_scene_doc_skips_bumper_scene_two(self):
        scenes = [_scene(1), _scene(2, has_avatar=False)] + [_scene(i) for i in range(3, 22)]
        mapping = val.page_to_scene(scenes)
        self.assertEqual(mapping[1], 1)
        self.assertEqual(mapping[2], 3)
        self.assertEqual(mapping[20], 21)
        self.assertNotIn(2, mapping.values())

    def test_twenty_one_scenes_with_avatar_on_scene_two_is_not_treated_as_bumper(self):
        scenes = [_scene(i) for i in range(1, 22)]
        self.assertEqual(val.page_to_scene(scenes)[2], 2)


class MaskNameTests(unittest.TestCase):
    def test_rounded_rectangle_has_curves_and_lines(self):
        self.assertEqual(val._mask_name({"nC": 4, "nL": 4}), "Rounded Rectangle")

    def test_oval_has_only_curves(self):
        self.assertEqual(val._mask_name({"nC": 4, "nL": 0}), "Oval")

    def test_rectangle_has_only_lines(self):
        self.assertEqual(val._mask_name({"nC": 0, "nL": 1}), "Rectangle")

    def test_missing_mask(self):
        self.assertIsNone(val._mask_name(None))


class ParsePagesTests(unittest.TestCase):
    def test_ranges_and_singles(self):
        self.assertEqual(val._parse_pages("1-3,5,8-9"), [1, 2, 3, 5, 8, 9])

    def test_default_full_book(self):
        self.assertEqual(val._parse_pages("1-20"), list(range(1, 21)))


class UrlParsingTests(unittest.TestCase):
    URL = "https://docs.google.com/videos/d/1SVmWhV4pWTrOHe0xQLRPvbO-LGC0dWw62ug-PqrQbY0/edit?scene=id.x&resourcekey=0-2ldUh4fkVZJQNV_9tuifXw#scene=id.x"

    def test_doc_id(self):
        self.assertEqual(vbp.doc_id_from_url(self.URL), "1SVmWhV4pWTrOHe0xQLRPvbO-LGC0dWw62ug-PqrQbY0")

    def test_resource_key(self):
        self.assertEqual(vbp.resource_key_from_url(self.URL), "0-2ldUh4fkVZJQNV_9tuifXw")

    def test_missing_resource_key(self):
        self.assertEqual(vbp.resource_key_from_url("https://docs.google.com/videos/d/abc/edit"), "")

    def test_non_vids_url(self):
        self.assertIsNone(vbp.doc_id_from_url("https://mail.google.com/mail/u/0/#inbox"))


class AutomationWindowTests(unittest.TestCase):
    def test_marker_survives_url_encoding(self):
        # ensure_window() finds the automation window by searching tab URLs for the marker.
        self.assertTrue(vbp.AUTOMATION_PAGE.startswith("data:text/html"))
        self.assertIn(vbp.AUTOMATION_MARK, vbp.AUTOMATION_PAGE)


class ChromePidWaitTests(unittest.TestCase):
    MISSING = subprocess.CalledProcessError(1, ["pgrep"])

    def test_fails_fast_by_default(self):
        with mock.patch.object(vbf, "CHROME_WAIT_S", 0.0), \
                mock.patch.object(vbf.subprocess, "check_output", side_effect=self.MISSING):
            with self.assertRaises(subprocess.CalledProcessError):
                vbf._main_chrome_pid()

    def test_waits_for_chrome_to_come_back(self):
        with mock.patch.object(vbf, "CHROME_WAIT_S", 60.0), \
                mock.patch.object(vbf.subprocess, "check_output", side_effect=[self.MISSING, self.MISSING, b"4242\n"]), \
                mock.patch.object(vbf.time, "sleep") as sleep:
            self.assertEqual(vbf._main_chrome_pid(), 4242)
            self.assertEqual(sleep.call_count, 2)


class FinalizeLanguageTests(unittest.TestCase):
    REF = {p: {"x": 1, "y": 1, "w": 1, "h": 1, "mask": "Rounded Rectangle"} for p in range(1, 21)}

    def test_without_reference_just_closes_tab(self):
        st = {}
        with mock.patch.object(vbp, "close_automation_tabs", return_value=1) as close:
            vbp.finalize_language("en", "DOC", st, None, 20)
        self.assertTrue(st["finalized"])
        close.assert_called_once_with("DOC")

    def test_records_layout_result(self):
        st = {}
        rep = {"doc": "DOC", "verified": 19, "failed": [{"page": 7, "ok": False}]}
        with mock.patch.object(vbp.val, "fix_doc", return_value=rep), \
                mock.patch.object(vbp, "close_automation_tabs", return_value=1):
            vbp.finalize_language("en", "DOC", st, self.REF, 20)
        self.assertEqual(st["layout"], {"verified": 19, "failed": [7]})
        self.assertTrue(st["finalized"])

    def test_layout_errors_retry_then_give_up_after_three_tries(self):
        st = {}
        with mock.patch.object(vbp.val, "fix_doc", side_effect=ValueError("editor busy")), \
                mock.patch.object(vbp, "close_automation_tabs", return_value=1) as close:
            self.assertIn("LAYOUT_RETRY", vbp.finalize_language("en", "DOC", st, self.REF, 20))
            self.assertIn("LAYOUT_RETRY", vbp.finalize_language("en", "DOC", st, self.REF, 20))
            self.assertNotIn("finalized", st)
            close.assert_not_called()
            vbp.finalize_language("en", "DOC", st, self.REF, 20)
        self.assertTrue(st["finalized"])
        self.assertIn("error", st["layout"])


class AvatarQuotaTests(unittest.TestCase):
    BANNER = "You've hit your limits for generating avatars in Vids"
    LIMITED = {"scene": "Scene 8 / 20", "limited": True, "busy": False, "outOfSync": True,
               "buttons": ["Edit script", "Change", "Update"], "pct": None, "text": "Avatar outdated\nUpdate\n" + BANNER}

    def _sidebar_regexes(self):
        import re
        with mock.patch.object(vbf, "exec_js", return_value="{}") as ex:
            vbf.scene_state("DOC")
        js = ex.call_args[0][1]
        limited = re.search(r"const limited = /(.+?)/i\.test", js).group(1)
        busy = re.search(r"busy: !limited && /(.+?)/i\.test", js).group(1)
        return re.compile(limited, re.I), re.compile(busy, re.I)

    def test_sidebar_js_tells_the_quota_banner_apart_from_a_render(self):
        limited, busy = self._sidebar_regexes()
        self.assertTrue(limited.search(self.BANNER))
        self.assertTrue(busy.search(self.BANNER))  # why `busy` must be gated on `!limited`
        render = "Generating avatar...\nThis might take a few minutes\n12%"
        self.assertFalse(limited.search(render))
        self.assertTrue(busy.search(render))

    def test_quota_banner_stops_clicking_and_records_the_time(self):
        st = {"avatars_done": list(range(1, 8)), "avatar_cursor": 8, "avatar_clicked": list(range(1, 9))}
        with mock.patch.object(vbf, "scene_state", return_value=self.LIMITED), \
                mock.patch.object(vbp, "click_update_or_insert") as click:
            out = vbp.avatar_step("ko", "DOC", st, 20)
        self.assertIn("S08(LIMIT)", out)
        self.assertIn("limited_at", st)
        click.assert_not_called()
        self.assertEqual(st["avatars_done"], list(range(1, 8)))

    def test_banner_in_text_counts_even_without_the_flag(self):
        st = {}
        with mock.patch.object(vbf, "scene_state", return_value={"busy": True, "text": "Update\n" + self.BANNER}):
            self.assertIn("LIMIT", vbp.avatar_step("it", "DOC", st, 20))

    def test_limit_seen_after_navigating_keeps_the_cursor_there(self):
        free = {"busy": False, "limited": False, "text": ""}
        st = {"avatars_done": [1, 2], "avatar_cursor": 3}
        with mock.patch.object(vbf, "scene_state", side_effect=[free, self.LIMITED]), \
                mock.patch.object(vbf, "navigate_to_scene", return_value="Scene 3 / 20"), \
                mock.patch.object(vbp, "click_update_or_insert") as click:
            self.assertIn("S03(LIMIT)", vbp.avatar_step("de", "DOC", st, 20))
        click.assert_not_called()
        self.assertEqual(st["avatar_cursor"], 3)

    def test_monthly_limit_banner_also_stops_clicking(self):
        monthly = "You've reached your monthly limit.Wait until Nov 1 at 12 AM for limits to reset."
        limited, _ = self._sidebar_regexes()
        self.assertTrue(limited.search(monthly))
        st = {}
        with mock.patch.object(vbf, "scene_state", return_value={"limited": False, "busy": False, "text": monthly}):
            self.assertIn("LIMIT", vbp.avatar_step("de", "DOC", st, 20))

    def test_book_avatar_mappings_and_state_reset_on_avatar_change(self):
        self.assertEqual(vbf.book_avatar("output/Long Running Agents"), "Rio Classic")
        self.assertEqual(vbf.book_avatar("output/Year of the Horse"), "Lennon Classic")
        self.assertEqual(vbf.book_avatar("output/Agent Sandbox"), "Alan Classic")
        self.assertEqual(vbf.book_avatar("output/Agent Memory Bank"), "Sue Classic")
        self.assertEqual(vbf.book_avatar("output/Agent Vibe Coding"), "Diego Classic")
        self.assertEqual(vbf.book_avatar("output/Agent Skills"), "Aaron Classic")
        self.assertEqual(vbf.avatar_actor("Rio Classic"), "actor17")
        self.assertEqual(vbf.avatar_actor("Amelia"), "")
        self.assertEqual(vbf.avatar_actor("Alan Classic"), "actor14")
        self.assertEqual(vbf.avatar_actor("Sue Classic"), "actor18")
        self.assertEqual(vbf.avatar_actor("Aden Classic"), "actor19")
        self.assertEqual(vbf.avatar_actor("Paul Classic"), "actor23")
        self.assertEqual(vbf.avatar_voice("Alan Classic"), "Orus")
        self.assertEqual(vbf.avatar_voice("Kai Classic"), "Orus")
        self.assertEqual(vbf.avatar_voice("Rio Classic"), "Aoede")
        self.assertEqual(vbf.avatar_voice("Aden Classic"), "Aoede")
        st = {"avatar_name": "Lennon Classic", "avatars_done": list(range(1, 21)), "finalized": True}
        vbp._sync_avatar_choice(st, "Rio Classic", 20)
        self.assertEqual(st["avatar_name"], "Rio Classic")
        self.assertEqual(st["avatars_done"], [])
        self.assertNotIn("finalized", st)
        self.assertEqual(st["force_refresh"], list(range(1, 21)))

    def test_classic_rotation_excludes_amelia_and_rotates_consecutive_books(self):
        self.assertEqual(len(vbf.CLASSIC_ROTATION), 11)
        self.assertNotIn("amelia", vbf.AVATAR_ACTORS)
        books = list(vbf.BOOK_AVATARS.items())
        self.assertGreaterEqual(len(books), 26)
        for (_, av1), (_, av2) in zip(books, books[1:]):
            self.assertNotEqual(av1, av2)
        for avatar in vbf.BOOK_AVATARS.values():
            self.assertIn(avatar, vbf.CLASSIC_ROTATION)
        self.assertEqual(vbf.book_avatar("output/Agent Skills", "Diego Classic"), "Diego Classic")
        with self.assertRaises(ValueError):
            vbf.book_avatar("output/Agent Skills", "Amelia")
        with self.assertRaises(ValueError):
            vbf.book_avatar("output/Agent Skills", "Some Unknown Avatar")

    def test_classic_avatar_matching_and_preview_generate_flow(self):
        self.assertTrue(vbf.avatar_matches("https://www.gstatic.com/docs/videos/videos/presetavatars/preview_actor17.mp4", "Rio Classic"))
        self.assertTrue(vbf.avatar_matches("https://www.gstatic.com/docs/videos/videos/presetavatars/preview_actor14.mp4", "Alan Classic"))
        self.assertTrue(vbf.avatar_matches("https://www.gstatic.com/docs/videos/videos/presetavatars/preview_actor18.mp4", "Sue Classic"))
        self.assertFalse(vbf.avatar_matches("https://www.gstatic.com/docs/videos/images/presetavatars/v2/actor36.png", "Rio Classic"))
        self.assertEqual(
            vbf.avatar_preview_url("Sue Classic"),
            "https://www.gstatic.com/docs/videos/videos/presetavatars/preview_actor18.mp4",
        )
        with mock.patch.object(vbf, "exec_js", side_effect=["PREVIEW_STARTED", "PREVIEW_CREATING", "CLICKED_GENERATE"]), \
                mock.patch.object(vbp.time, "sleep"):
            self.assertEqual(vbp.click_update_or_insert("DOC"), "CLICKED_GENERATE")
        st = {"avatar_name": "Rio Classic", "avatars_done": [], "avatar_cursor": 1, "avatar_clicked": [1]}
        idle = {"busy": False, "limited": False, "text": ""}
        s1_done = {"busy": False, "limited": False, "outOfSync": False, "avatarSrc": "preview_actor17.mp4", "buttons": ["Edit script", "Change", "Preview"], "text": "Rio Classic\nPreview"}
        s2_ready = {"busy": False, "limited": False, "outOfSync": False, "avatarSrc": "preview_actor17.mp4", "buttons": ["Edit script", "Change", "Preview"], "text": "Rio Classic\nPreview"}
        with mock.patch.object(vbf, "scene_state", side_effect=[idle, s1_done, s2_ready]), \
                mock.patch.object(vbf, "navigate_to_scene", side_effect=["Scene 1 / 20", "Scene 2 / 20"]), \
                mock.patch.object(vbp, "click_update_or_insert", return_value="CLICKED_GENERATE"):
            out = vbp.avatar_step("en", "DOC", st, 20, avatar_name="Rio Classic")
        self.assertEqual(st["avatars_done"], [1])
        self.assertEqual(st["avatar_clicked"], [1, 2])
        self.assertIn("S02(CLICKED_GENERATE)[1/20]", out)

    def test_script_landed_rejects_shared_twelve_char_prefix_from_template_book(self):
        template_s1 = "Welcome to Curious Little Minds! Today we read 'The Lantern-Parade Horse', written by Enrique Chan."
        target_s1 = "Welcome to Curious Little Minds! Today we read 'The Robot's Treasure Chest', written by Enrique Chan."
        with mock.patch.object(vbf, "scene_state", return_value={"text": "Scene 1 / 20\n" + template_s1}), \
                mock.patch.object(vbf, "exec_js", return_value="[]"):
            self.assertFalse(vbp.script_landed("DOC", target_s1))
        with mock.patch.object(vbf, "scene_state", return_value={"text": "Scene 1 / 20\n" + target_s1}), \
                mock.patch.object(vbf, "exec_js", return_value="[]"):
            self.assertTrue(vbp.script_landed("DOC", target_s1))


class CloneMethodTests(unittest.TestCase):
    def _run_clone(self, extra_args, langs=("en",), ref_urls=None):
        ref_urls = ref_urls or {"en": "https://docs.google.com/videos/d/SRC/edit?resourcekey=0-k"}
        with tempfile.TemporaryDirectory() as tmp:
            book, ref = os.path.join(tmp, "Agent Skills"), os.path.join(tmp, "ref")
            os.makedirs(os.path.join(book, "google_vids"))
            os.makedirs(ref)
            with open(os.path.join(book, "story.json"), "w") as f:
                json.dump({"title": "The Superhero Toolbelt", "pages": [{"page_number": i} for i in range(1, 21)]}, f)
            with open(os.path.join(ref, "manifest.json"), "w") as f:
                json.dump({"google_vids": {"drive_vids_urls": ref_urls}}, f)
            new = {"id": "NEW", "url": "https://docs.google.com/videos/d/NEW/edit", "method": "copy_page"}
            argv = ["vids_book_pipeline.py", "--book-dir", book, "--reference-dir", ref, "--langs", *langs, "--steps", "clone"]
            with mock.patch("sys.argv", argv + extra_args), \
                    mock.patch.object(vbp, "clone_with_drive_api", return_value=None) as api, \
                    mock.patch.object(vbp, "clone_with_copy_page", return_value=new) as page, \
                    mock.patch.object(vbp, "wait_for_editor", return_value=20), \
                    mock.patch.object(vbp, "rename", return_value="ok"):
                self.assertEqual(vbp.main(), 0)
                path = os.path.join(book, "google_vids", "vids_projects.json")
                projects = json.load(open(path)) if os.path.exists(path) else {}
            return api, page, projects

    def test_no_drive_api_clones_in_the_browser_only(self):
        api, page, projects = self._run_clone(["--no-drive-api"])
        api.assert_not_called()
        page.assert_called_once()
        self.assertEqual(projects["en"]["id"], "NEW")
        self.assertEqual(projects["en"]["source_id"], "SRC")

    def test_default_still_tries_drive_api_first(self):
        api, page, _ = self._run_clone([])
        api.assert_called_once()
        page.assert_called_once()

    def test_clone_source_override_clones_zh_from_the_yue_template(self):
        refs = {"yue": "https://docs.google.com/videos/d/YUESRC/edit", "zh": "https://docs.google.com/videos/d/ZHSRC/edit"}
        _, page, projects = self._run_clone(["--no-drive-api", "--clone-source", "zh=yue"], langs=("zh",), ref_urls=refs)
        self.assertEqual(page.call_args[0][0], "YUESRC")
        self.assertEqual(projects["zh"]["source_id"], "YUESRC")

    def test_without_override_a_missing_reference_language_is_skipped(self):
        refs = {"yue": "https://docs.google.com/videos/d/YUESRC/edit"}
        _, page, projects = self._run_clone(["--no-drive-api"], langs=("es",), ref_urls=refs)
        page.assert_not_called()
        self.assertNotIn("es", projects)


class _FakeRequest:
    def __init__(self, result, hook=None):
        self.result, self.hook = result, hook

    def execute(self):
        if self.hook:
            self.hook()
        return self.result


class _FakeFiles:
    def __init__(self, folder_id, on_get=None):
        self.folder_id, self.on_get = folder_id, on_get

    def get(self, fileId, fields="", supportsAllDrives=True):
        return _FakeRequest({"id": fileId, "parents": [self.folder_id], "resourceKey": f"rk-{fileId}"}, self.on_get)

    def update(self, **kwargs):
        return _FakeRequest({})


class _FakeDrive:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


class DriveSyncMergeTests(unittest.TestCase):
    def test_vids_only_keeps_projects_added_by_a_concurrent_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = os.path.join(tmp, "Book")
            os.makedirs(os.path.join(book, "google_vids"))
            with open(os.path.join(book, "story.json"), "w") as f:
                json.dump({"title": "T", "pages": []}, f)
            projects_path = os.path.join(book, "google_vids", "vids_projects.json")
            with open(projects_path, "w") as f:
                json.dump({"en": {"id": "E", "url": "old"}}, f)

            def worker_clones_it():
                cur = json.load(open(projects_path))
                cur.setdefault("it", {"id": "I", "url": "u-it"})
                with open(projects_path, "w") as f:
                    json.dump(cur, f)

            drive = _FakeDrive(_FakeFiles("FOLDER", on_get=worker_clones_it))
            with mock.patch.object(vds.drive_sync, "init_drive_service", return_value=drive), \
                    mock.patch.object(vds.drive_sync, "get_or_create_folder", return_value="SUB"):
                gv = vds.sync_book(book, folder_id="FOLDER", vids_only=True)

            out = json.load(open(projects_path))
            self.assertEqual(out["en"]["url"], "https://docs.google.com/videos/d/E/edit?resourcekey=rk-E")
            self.assertEqual(out["it"], {"id": "I", "url": "u-it"})
            self.assertEqual(gv["drive_vids_urls"], {"en": out["en"]["url"]})
            self.assertEqual(json.load(open(os.path.join(book, "manifest.json")))["drive_folder_id"], "FOLDER")


if __name__ == "__main__":
    unittest.main()
