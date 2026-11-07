"""Automated Google Vids Chinese (ZH) Flow: Duplicate EN Vids + Replace Slides + Sync Exact Per-Page Audio Timing.

Workflow:
1. Duplicates the English AI-Avatar Google Vids project via Google Drive API (or targets an existing ZH Vids ID),
   preserving all 20 Lennon AI Avatar scenes and the 20% soft background music track.
2. Extracts all 20 localized Chinese 16:9 stage slide images (`page_01_stage.jpg`..`page_20_stage.jpg`) from
   the book's `_Google_Vids_ZH.pptx` deck.
3. Synthesizes per-page Chinese narration audio (`page_01_zh.mp4`..`page_20_zh.mp4`) for all 20 pages.
4. Steps through Scenes 1..20 in the background Chrome tab (never stealing focus):
   - Updates the scene's Script text to the Chinese script.
   - Replaces the scene's book slide image (`Replace image -> Upload from computer`) with the localized Chinese slide.
   - Time-scales the scene's Lennon AI Avatar video stream (`setpts`) to the exact duration of that page's Chinese
     audio, muxes the Chinese audio track into `scene_XX_zh_avatar.mp4`, and uploads it via `Replace video -> Upload`,
     which automatically adjusts the scene's timeline duration to the exact Chinese audio duration.
"""

import argparse
import concurrent.futures
import json
import os
import re
import time
import zipfile
from typing import Optional

import audio_generator
import vids_book_flow as vbf
import vids_exporter

MAIN_WORLD_HOOK_JS = vbf.BOOK_HOOKS_JS


def _main_chrome_pid() -> int:
    return vbf._main_chrome_pid()


def exec_in_vids_tab(doc_id: str, main_js: str, iso_js: str) -> str:
    """Executes main-world and isolated-world JS in the matching Google Vids tab without activating Chrome."""
    return vbf.exec_js(doc_id, iso_js, extra_main_js=main_js, hooks=True)


def navigate_to_scene(doc_id: str, scene_num: int) -> str:
    """Navigates to Scene `scene_num` (1..20) using the AI avatar / Scripts sidebar."""
    return vbf.navigate_to_scene(doc_id, scene_num)


def update_scene_script(doc_id: str, script_text: str) -> None:
    """Updates the current scene's script text in the Google Vids sidebar."""
    vbf.update_scene_script(doc_id, script_text)


def replace_scene_slide_image(doc_id: str, slide_jpg_path: str, scene_num: int) -> str:
    """Replaces the current scene's 16:9 book slide image via Replace image -> Upload from computer."""
    return vbf.replace_scene_slide(doc_id, slide_jpg_path, f"page_{scene_num:02d}_stage.jpg")


def download_scene_avatar_mp4(doc_id: str, out_mp4_path: str) -> bool:
    """Checks whether a cached English avatar MP4 clip exists locally."""
    if os.path.exists(out_mp4_path) and os.path.getsize(out_mp4_path) > 10000:
        return True
    s1_en = os.path.join(os.path.dirname(out_mp4_path), "scene_01_en_avatar.mp4")
    if os.path.exists(s1_en) and os.path.getsize(s1_en) > 10000:
        return True
    return False


def build_timed_zh_avatar_mp4(en_mp4_path: str, zh_wav_path: str, out_zh_mp4_path: str) -> float:
    """Loops the avatar video stream at natural 1.0x speed to match `zh_wav_path` exact duration and muxes audio."""
    return vbf.build_timed_avatar_mp4(en_mp4_path, zh_wav_path, out_zh_mp4_path)


def replace_scene_avatar_video(
    doc_id: str, zh_mp4_path: str, zh_dur: float, scene_num: int, upload_name: Optional[str] = None
) -> str:
    """Replaces the current scene's avatar video clip with the exact-timed localized MP4."""
    return vbf.replace_scene_avatar_video(doc_id, zh_mp4_path, zh_dur, scene_num, upload_name=upload_name)


def run_chinese_vids_flow(
    book_dir: str,
    zh_vid_id: str,
    start_scene: int = 1,
    end_scene: int = 20,
    voice_name: str = "Aoede",
    skip_slides: bool = False,
) -> None:
    """Runs the complete 20-scene slide replacement + exact-timed Chinese MP4 audio/avatar flow."""
    story_path = os.path.join(book_dir, "story.json")
    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    pages = sorted(story["pages"], key=lambda p: int(p["page_number"]))
    total = len(pages)

    vids_dir = os.path.join(book_dir, "google_vids")
    zh_slides_dir = os.path.join(vids_dir, "zh_slides")
    zh_audio_dir = os.path.join(vids_dir, "zh_audio")
    os.makedirs(zh_slides_dir, exist_ok=True)
    os.makedirs(zh_audio_dir, exist_ok=True)

    # Ensure 20 Chinese stage slide JPEGs are extracted
    title = story.get("title", os.path.basename(book_dir))
    safe_title = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip()
    pptx_zh = os.path.join(vids_dir, f"{safe_title}_Google_Vids_ZH.pptx")
    with zipfile.ZipFile(pptx_zh, "r") as zf:
        for i in range(1, total + 1):
            name = f"page_{i:02d}_stage.jpg"
            out_jpg = os.path.join(zh_slides_dir, name)
            if not os.path.exists(out_jpg):
                with open(out_jpg, "wb") as jf:
                    jf.write(zf.read(f"ppt/media/{name}"))

    v_slug = voice_name.lower()

    # Synthesize all requested page WAVs in parallel if not already present
    def _synth_page(s_idx: int) -> None:
        pg = pages[s_idx - 1]
        sc = vids_exporter.extract_page_voiceover(story, pg, total, lang="zh")
        w_path = os.path.join(zh_audio_dir, f"page_{s_idx:02d}_{v_slug}.wav")
        if not os.path.exists(w_path) or os.path.getsize(w_path) < 1000:
            for attempt in range(3):
                if audio_generator.synthesize_speech_gemini_tts(sc, w_path, voice_name=voice_name):
                    break
                time.sleep(1.5 * (attempt + 1))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_synth_page, range(start_scene, end_scene + 1)))

    for s in range(start_scene, end_scene + 1):
        page = pages[s - 1]
        script = vids_exporter.extract_page_voiceover(story, page, total, lang="zh")
        slide_jpg = os.path.join(zh_slides_dir, f"page_{s:02d}_stage.jpg")
        wav_path = os.path.join(zh_audio_dir, f"page_{s:02d}_{v_slug}.wav")
        en_mp4_path = os.path.join(zh_audio_dir, f"scene_{s:02d}_en_avatar.mp4")
        zh_mp4_path = os.path.join(zh_audio_dir, f"scene_{s:02d}_zh_{v_slug}_avatar.mp4")

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
            audio_generator.synthesize_speech_gemini_tts(script, wav_path, voice_name=voice_name)

        nav_lbl = navigate_to_scene(zh_vid_id, s)
        update_scene_script(zh_vid_id, script)

        # 1. Replace slide image (or skip if slides are already replaced)
        if skip_slides:
            slide_res = "KEPT_ZH_SLIDE"
        else:
            slide_res = replace_scene_slide_image(zh_vid_id, slide_jpg, s)

        # 2. Build exact-timed ZH MP4 from cached English Lennon avatar MP4
        if not os.path.exists(en_mp4_path) or os.path.getsize(en_mp4_path) < 10000:
            en_mp4_path = os.path.join(zh_audio_dir, "scene_01_en_avatar.mp4")

        zh_dur = build_timed_zh_avatar_mp4(en_mp4_path, wav_path, zh_mp4_path)

        # 3. Replace avatar video clip with the exact-timed Chinese MP4
        vid_res = replace_scene_avatar_video(zh_vid_id, zh_mp4_path, zh_dur, s)
        print(
            f"[Scene {s:02d}/{total:02d}] ({nav_lbl}) | voice={voice_name} | slide={slide_res} | avatar_mp4={vid_res} | exact_zh_dur={zh_dur:.2f}s",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Google Vids Chinese (ZH) slide & exact-timed MP4 audio flow.")
    parser.add_argument("--book-dir", default="output/Year of the Horse")
    parser.add_argument("--zh-vid-id", default="1FRw-EZS679nIwvIGN0ZuAM2_IF3vKnzhrGkniVX_NYE")
    parser.add_argument("--start-scene", type=int, default=1)
    parser.add_argument("--end-scene", type=int, default=20)
    parser.add_argument("--voice-name", default="Aoede")
    parser.add_argument("--skip-slides", action="store_true")
    args = parser.parse_args()
    run_chinese_vids_flow(
        book_dir=args.book_dir,
        zh_vid_id=args.zh_vid_id,
        start_scene=args.start_scene,
        end_scene=args.end_scene,
        voice_name=args.voice_name,
        skip_slides=args.skip_slides,
    )


if __name__ == "__main__":
    main()
