"""Uploaded-MP4 "avatars" for languages Vids' AI avatar can't speak: Chinese (zh) and Cantonese (yue).

Per page: TTS narration -> the Lennon presenter clip looped to the narration's exact length -> Replace video.

Steps (resumable; outputs cached under <book>/google_vids/<lang>_audio):
  1. tts     - synthesize page_XX_<voice>.wav for all pages, in parallel.
               zh: Gemini TTS. yue: the Cloud TTS yue-HK Chirp 3 HD voice of the same name (same "Aoede" persona),
               because Gemini TTS can't be pinned to Cantonese.
  2. mp4     - loop the cached EN Lennon avatar clip to each page's exact audio length and mux the narration.
  3. replace - for each scene: select the avatar video (retrying until the video toolbar appears, since the first
               click after a script edit only blurs the editor) and Replace video -> Upload the timed MP4. The
               element keeps its crop / mask / position.

Usage:
  python vids_zh_avatars.py --book-dir "output/Long Running Agents" --steps tts mp4
  python vids_zh_avatars.py --book-dir "output/Long Running Agents" --zh-vid-id <DOC_ID> --steps replace
  python vids_zh_avatars.py --book-dir "output/Year of the Horse" --lang yue --vid-id <DOC_ID> --steps tts mp4 replace
"""

import argparse
import base64
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

import audio_generator
import vids_book_flow as vbf
import vids_chinese_flow as vcf
import vids_exporter

# Maps each supported book language to (Cloud TTS locale, Gemini TTS language name).
LANG_TTS_CONFIG: Dict[str, Tuple[str, str]] = {
    "zh": ("cmn-CN", "Mandarin Chinese"),
    "yue": ("yue-HK", "Cantonese"),
    "en": ("en-US", "English"),
    "es": ("es-US", "Spanish"),
    "pt": ("pt-BR", "Portuguese"),
    "ja": ("ja-JP", "Japanese"),
    "ko": ("ko-KR", "Korean"),
    "fr": ("fr-FR", "French"),
    "it": ("it-IT", "Italian"),
    "de": ("de-DE", "German"),
}

MP4_AVATAR_LANGS = tuple(LANG_TTS_CONFIG.keys())

SELECT_AVATAR_JS = r"""(() => {
  const fire = (el, x, y) => ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0})));
  const btn = () => document.getElementById('replaceVideoMenu');
  const ready = () => { const b = btn(); return b && b.offsetParent !== null && !/disabled/.test(b.className); };
  const leaves = [...document.querySelectorAll("svg g[id^='editor-']")].filter(g => {
    const r = g.getBoundingClientRect();
    return r.width > 20 && r.height > 20 && !g.id.endsWith('-bg') && g.querySelectorAll("g[id^='editor-']").length === 0;
  }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
  if (leaves.length < 2) return 'NO_AVATAR_G';
  const r = leaves[1].getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  fire(document.elementFromPoint(x, y) || leaves[1], x, y);
  const more = document.getElementById('moreButton');
  if (more && more.offsetParent !== null && more.getAttribute('aria-pressed') !== 'true') {
    const mr = more.getBoundingClientRect();
    fire(more, mr.left + mr.width / 2, mr.top + mr.height / 2);
  }
  return ready() ? 'SELECTED' : 'NOT_SELECTED';
})()"""


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def audio_dir(book_dir: str, lang: str) -> str:
    return os.path.join(book_dir, "google_vids", f"{lang}_audio")


def page_wav(book_dir: str, lang: str, page: int, voice: str) -> str:
    return os.path.join(audio_dir(book_dir, lang), f"page_{page:02d}_{voice.lower()}.wav")


def scene_mp4(book_dir: str, lang: str, scene: int, voice: str, avatar_name: str = "") -> str:
    actor = vbf.avatar_actor(avatar_name)
    suffix = f"_{actor}" if actor and actor != "actor16" else ""
    return os.path.join(audio_dir(book_dir, lang), f"scene_{scene:02d}_{lang}_{voice.lower()}{suffix}_avatar.mp4")


def state_path(book_dir: str, lang: str) -> str:
    return os.path.join(book_dir, "google_vids", f"vids_{lang}_state.json")


RENDERED_AVATARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "classic_avatars")


def rendered_avatar_clip_path(avatar_name_or_actor: str) -> str:
    actor = vbf.avatar_actor(avatar_name_or_actor) or avatar_name_or_actor or "actor16"
    return os.path.join(RENDERED_AVATARS_DIR, f"rendered_{actor}.mp4")


def _strip_audio_to_base_mp4(src_mp4: str, dst_mp4: str) -> str:
    os.makedirs(os.path.dirname(dst_mp4), exist_ok=True)
    tmp_mp4 = f"{dst_mp4}.tmp.{os.getpid()}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            src_mp4,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            tmp_mp4,
        ],
        check=True,
        capture_output=True,
    )
    os.replace(tmp_mp4, dst_mp4)
    return dst_mp4


def render_classic_avatar_once(
    doc_id: str,
    avatar_name: str,
    scene_num: int = 1,
    donor_dir: str = "output/Year of the Horse",
    timeout: float = 240.0,
) -> Optional[str]:
    """Generates `avatar_name` ONCE in Google Vids on `scene_num` and caches `assets/classic_avatars/rendered_<actor>.mp4`."""
    import vids_book_pipeline as vbp

    actor = vbf.avatar_actor(avatar_name) or "actor16"
    out_clip = rendered_avatar_clip_path(actor)
    if os.path.exists(out_clip) and os.path.getsize(out_clip) > 10000:
        return out_clip

    donor = os.path.join(donor_dir, "google_vids", "zh_audio", "scene_01_en_avatar.mp4")
    if actor == "actor16" and os.path.exists(donor) and os.path.getsize(donor) > 10000:
        return _strip_audio_to_base_mp4(donor, out_clip)

    nav = vbf.navigate_to_scene(doc_id, scene_num)
    if not (nav or "").startswith(f"Scene {scene_num} /"):
        time.sleep(1.0)
        nav = vbf.navigate_to_scene(doc_id, scene_num)
    if not (nav or "").startswith(f"Scene {scene_num} /"):
        print(f"  [render-once {actor}] could not navigate to scene {scene_num}: {nav}", flush=True)
        return None

    before_urls_raw = vbf.exec_js(
        doc_id,
        """(() => JSON.stringify([...document.querySelectorAll("svg g[id^='editor-'] video")]
            .filter(v => v.getBoundingClientRect().width > 20)
            .map(v => v.currentSrc || v.src || "")))()""",
        hooks=False,
    )
    try:
        before_urls = set(json.loads(before_urls_raw or "[]"))
    except json.JSONDecodeError:
        before_urls = set()

    sel = vbf.select_avatar(doc_id, avatar_name)
    st = vbf.scene_state(doc_id)
    if vbp._is_limited(st):
        print(f"  [render-once {actor}] Google Vids avatar quota reached; using Classic preview fallback.", flush=True)
        return None

    if st.get("updateDisabled") or ("Update" in (st.get("buttons") or []) and not st.get("outOfSync")):
        cur_txt = (st.get("text") or "").strip()
        # Nudge script slightly so Update becomes clickable when switching characters on an existing scene.
        vbf.exec_js(
            doc_id,
            """(() => {
                const sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
                const editBtn = sidebar && sidebar.querySelector("button.appsFlixScriptsSidebarKeyboardEditButton");
                if (editBtn) editBtn.click();
                const iframe = document.querySelector("iframe.docs-texteventtarget-iframe");
                const target = iframe && iframe.contentDocument && (iframe.contentDocument.querySelector("[contenteditable='true']") || iframe.contentDocument.body);
                if (!target) return "NO_TARGET";
                target.focus();
                const dt = new DataTransfer();
                dt.setData("text/plain", "\\u200b");
                target.dispatchEvent(new ClipboardEvent("paste", {clipboardData: dt, bubbles: true, cancelable: true}));
                return "NUDGED";
            })()""",
        )
        time.sleep(0.5)
        _ = cur_txt

    clicked = vbp.click_update_or_insert(doc_id)
    print(f"  [render-once {avatar_name} ({actor})] select={sel} trigger={clicked}", flush=True)
    if not clicked.startswith("CLICKED"):
        return None

    deadline = time.time() + timeout
    rendered_url = ""
    while time.time() < deadline:
        time.sleep(3.0)
        st = vbf.scene_state(doc_id)
        if vbp._is_limited(st):
            print(f"  [render-once {actor}] hit quota banner during render.", flush=True)
            return None
        if st.get("busy"):
            continue
        urls_raw = vbf.exec_js(
            doc_id,
            """(() => JSON.stringify([...document.querySelectorAll("svg g[id^='editor-'] video")]
                .filter(v => v.getBoundingClientRect().width > 20)
                .map(v => v.currentSrc || v.src || "")))()""",
        )
        try:
            now_urls = [
                u
                for u in json.loads(urls_raw or "[]")
                if "usercontent.google.com" in u or "c.drive.google.com/videoplayback" in u
            ]
        except json.JSONDecodeError:
            now_urls = []
        new_urls = [u for u in now_urls if u not in before_urls]
        if new_urls:
            rendered_url = new_urls[0]
            break
        if now_urls and not st.get("outOfSync"):
            rendered_url = now_urls[0]
            break

    if not rendered_url:
        print(f"  [render-once {actor}] timed out waiting for rendered video URL.", flush=True)
        return None

    vbf.exec_js(
        doc_id,
        f"""(() => {{
            document.documentElement.setAttribute("data-sa-clip", "FETCHING");
            fetch({json.dumps(rendered_url)}, {{credentials: "include"}})
                .then(r => r.arrayBuffer())
                .then(buf => {{
                    const bytes = new Uint8Array(buf);
                    let bin = "";
                    const chunk = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunk) {{
                        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                    }}
                    document.documentElement.setAttribute("data-sa-clip", btoa(bin));
                }})
                .catch(e => document.documentElement.setAttribute("data-sa-clip", "ERR:" + e));
            return "STARTED";
        }})()""",
        hooks=False,
    )
    b64_data = ""
    for _ in range(30):
        time.sleep(0.5)
        val = vbf.exec_js(doc_id, 'document.documentElement.getAttribute("data-sa-clip") || ""', hooks=False)
        if val and val != "FETCHING":
            b64_data = val
            break
    vbf.exec_js(doc_id, 'document.documentElement.removeAttribute("data-sa-clip")', hooks=False)
    if not b64_data or b64_data.startswith("ERR:"):
        print(f"  [render-once {actor}] failed to download rendered video: {b64_data[:120]}", flush=True)
        return None

    os.makedirs(RENDERED_AVATARS_DIR, exist_ok=True)
    raw_mp4 = os.path.join(RENDERED_AVATARS_DIR, f"raw_{actor}_{os.getpid()}.mp4")
    try:
        with open(raw_mp4, "wb") as f:
            f.write(base64.b64decode(b64_data))
        _strip_audio_to_base_mp4(raw_mp4, out_clip)
        print(f"  [render-once {avatar_name} ({actor})] cached {out_clip} ({os.path.getsize(out_clip) // 1024} KB)", flush=True)
        return out_clip
    finally:
        if os.path.exists(raw_mp4):
            os.remove(raw_mp4)


def ensure_base_avatar_mp4(
    book_dir: str,
    lang: str,
    avatar_name: str = "",
    donor_dir: str = "output/Year of the Horse",
) -> str:
    """Returns a local 1280x720 24fps H.264 MP4 of the requested Vids Classic avatar to loop under TTS audio."""
    os.makedirs(audio_dir(book_dir, lang), exist_ok=True)
    actor = vbf.avatar_actor(avatar_name) or "actor16"
    rendered = rendered_avatar_clip_path(actor)
    base = os.path.join(audio_dir(book_dir, lang), f"base_{actor}.mp4")

    if os.path.exists(rendered) and os.path.getsize(rendered) > 10000:
        if not os.path.exists(base) or os.path.getmtime(base) < os.path.getmtime(rendered):
            shutil.copy2(rendered, base)
        return base

    if actor == "actor16":
        legacy_base = os.path.join(audio_dir(book_dir, lang), "scene_01_en_avatar.mp4")
        donor = os.path.join(donor_dir, "google_vids", "zh_audio", "scene_01_en_avatar.mp4")
        if os.path.exists(donor) and os.path.getsize(donor) > 10000:
            _strip_audio_to_base_mp4(donor, rendered)
            shutil.copy2(rendered, base)
            return base
        if os.path.exists(legacy_base) and os.path.getsize(legacy_base) > 10000:
            return legacy_base

    if os.path.exists(base) and os.path.getsize(base) > 10000:
        return base
    url = vbf.avatar_preview_url(avatar_name or "Lennon Classic")
    return _strip_audio_to_base_mp4(url, base)


def synthesize(lang: str, text: str, wav: str, voice: str) -> bool:
    cloud_code, gemini_lang = LANG_TTS_CONFIG.get(lang, ("cmn-CN", "Mandarin Chinese"))
    if lang == "yue":
        return audio_generator.synthesize_speech_cloud_tts(
            text, wav, language_code=cloud_code, voice_name=f"{cloud_code}-Chirp3-HD-{voice}"
        )
    if lang == "zh":
        if audio_generator.synthesize_speech_gemini_tts(text, wav, voice_name=voice, language=gemini_lang):
            return True
        return audio_generator.synthesize_speech_cloud_tts(
            text, wav, language_code=cloud_code, voice_name=f"{cloud_code}-Chirp3-HD-{voice}"
        )
    if audio_generator.synthesize_speech_cloud_tts(
        text, wav, language_code=cloud_code, voice_name=f"{cloud_code}-Chirp3-HD-{voice}"
    ):
        return True
    return audio_generator.synthesize_speech_gemini_tts(text, wav, voice_name=voice, language=gemini_lang)


def synth_all(book_dir: str, voice: str = "Aoede", lang: str = "zh") -> None:
    story = _load_json(os.path.join(book_dir, "story.json"))
    pages = sorted(story["pages"], key=lambda p: int(p["page_number"]))
    os.makedirs(audio_dir(book_dir, lang), exist_ok=True)

    def one(i: int) -> str:
        wav = page_wav(book_dir, lang, i, voice)
        if os.path.exists(wav) and os.path.getsize(wav) > 1000:
            return f"p{i:02d}:cached"
        text = vids_exporter.extract_page_voiceover(story, pages[i - 1], len(pages), lang=lang)
        for attempt in range(3):
            if synthesize(lang, text, wav, voice):
                return f"p{i:02d}:ok"
            time.sleep(2 * (attempt + 1))
        return f"p{i:02d}:FAILED"

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        print(" ".join(pool.map(one, range(1, len(pages) + 1))), flush=True)


def build_mp4s(
    book_dir: str,
    voice: str = "Aoede",
    donor_dir: str = "output/Year of the Horse",
    lang: str = "zh",
    avatar_name: str = "",
) -> None:
    base = ensure_base_avatar_mp4(book_dir, lang, avatar_name=avatar_name, donor_dir=donor_dir)
    n = len(_load_json(os.path.join(book_dir, "story.json"))["pages"])
    for s in range(1, n + 1):
        out = scene_mp4(book_dir, lang, s, voice, avatar_name=avatar_name)
        dur = vcf.build_timed_zh_avatar_mp4(base, page_wav(book_dir, lang, s, voice), out)
        print(f"scene {s:02d}: {dur:.2f}s -> {os.path.basename(out)} ({os.path.getsize(out) // 1024} KB)", flush=True)


VISIBLE_SCENE_VIDEO_DUR_JS = r"""(() => JSON.stringify([...document.querySelectorAll("svg g[id^='editor-'] video")]
  .filter(v => v.getBoundingClientRect().width > 20).map(v => Math.round(v.duration * 100) / 100)))()"""


def clip_in_place(doc_id: str, dur: float, tol: float = 0.2, wait: float = 10.0, upload_name: str = "") -> bool:
    """True once the current scene's visible video has the expected length (or timeline shows `upload_name`)."""
    deadline = time.time() + wait
    while True:
        try:
            durs = json.loads(vbf.exec_js(doc_id, VISIBLE_SCENE_VIDEO_DUR_JS, hooks=False) or "[]")
        except json.JSONDecodeError:
            durs = []
        if any(isinstance(d, (int, float)) and abs(d - dur) <= tol for d in durs):
            return True
        if upload_name:
            body = vbf.exec_js(doc_id, "document.body ? document.body.innerText : ''", hooks=False) or ""
            if upload_name in body:
                return True
        if time.time() >= deadline:
            return False
        time.sleep(1.0)


def replace_all(book_dir: str, doc_id: str, voice: str = "Aoede", lang: str = "zh", avatar_name: str = "") -> None:
    import vids_book_pipeline as vbp  # local import: pipeline helpers for (re)opening the doc's background tab

    proj = vbp._read_json(os.path.join(book_dir, "google_vids", "vids_projects.json")).get(lang, {})
    url = proj.get("url") if proj.get("id") == doc_id and proj.get("url") else f"https://docs.google.com/videos/d/{doc_id}/edit"
    vbp.ensure_tab(doc_id, url)
    spath = state_path(book_dir, lang)
    saved = _load_json(spath) if os.path.exists(spath) else {}
    actor = vbf.avatar_actor(avatar_name) or "actor16"
    same_avatar = bool(saved.get("actor")) and saved.get("actor") == actor
    done = set(saved.get("replaced", [])) if same_avatar else set()
    base = ensure_base_avatar_mp4(book_dir, lang, avatar_name=avatar_name)
    scripts = vbf.load_scripts(book_dir, lang)
    n = len(scripts)
    tag = re.sub(r"[^a-z0-9]+", "_", os.path.basename(os.path.normpath(book_dir)).lower())
    for _ in range(3):
        for s in range(1, n + 1):
            if s in done:
                continue
            nav = vbf.navigate_to_scene(doc_id, s)
            if not (nav or "").startswith(f"Scene {s} /"):
                time.sleep(1.5)
                nav = vbf.navigate_to_scene(doc_id, s)
            if not (nav or "").startswith(f"Scene {s} /"):
                print(f"[{lang.upper()} scene {s:02d}] navigation failed ({nav}); RETRY", flush=True)
                continue  # never upload into whatever scene happens to be open
            time.sleep(0.4)
            # Ensure sidebar script matches story.json 100% (fixes any legacy 12-char prefix collisions).
            if not vbp.script_landed(doc_id, scripts[s - 1]):
                vbf.update_scene_script(doc_id, scripts[s - 1])
                time.sleep(0.4)
                vbp.remove_stray_text(doc_id, scripts[s - 1])
            mp4 = scene_mp4(book_dir, lang, s, voice, avatar_name=avatar_name)
            dur = vcf.build_timed_zh_avatar_mp4(base, page_wav(book_dir, lang, s, voice), mp4)
            up_name = f"{tag}_scene_{s:02d}_{lang}_{actor}_avatar.mp4"
            body_txt = vbf.exec_js(doc_id, "document.body ? document.body.innerText : ''", hooks=False) or ""
            if same_avatar and up_name in body_txt:
                res, sel = "ALREADY_IN_PLACE", "-"
            else:
                sel = ""
                for _ in range(4):
                    sel = vbf.exec_js(doc_id, SELECT_AVATAR_JS)
                    if sel == "SELECTED":
                        break
                    time.sleep(0.5)
                if sel == "SELECTED":
                    res = vcf.replace_scene_avatar_video(
                        doc_id, mp4, dur, s, upload_name=up_name
                    )
                elif sel == "NO_AVATAR_G":
                    res = vbf.insert_scene_avatar_video(
                        doc_id, mp4, dur, s, upload_name=up_name
                    )
                else:
                    res = f"SELECT_FAILED:{sel}"
            ok = (res == "ALREADY_IN_PLACE" or res.endswith(".mp4")) and clip_in_place(doc_id, dur, upload_name=up_name)
            print(f"[{lang.upper()} scene {s:02d}] ({nav}) select={sel} replace={res} dur={dur:.2f}s verified={ok} {'OK' if ok else 'RETRY'}", flush=True)
            if ok:
                done.add(s)
                with open(spath, "w", encoding="utf-8") as f:
                    json.dump({"actor": actor, "avatar": avatar_name, "voice": voice, "replaced": sorted(done)}, f)
            time.sleep(0.8)
        if len(done) >= n:
            break
    print(f"{lang.upper()} avatar videos replaced ({avatar_name or actor}): {len(done)}/{n}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--book-dir", required=True)
    ap.add_argument("--lang", default="zh", choices=MP4_AVATAR_LANGS)
    ap.add_argument("--vid-id", "--zh-vid-id", dest="vid_id", default="", help="Vids doc id for the replace step.")
    ap.add_argument("--avatar", default="", help="Vids avatar character (e.g. 'Rio Classic', 'Alan Classic', 'Sue Classic'; defaults to the book's canonical avatar).")
    ap.add_argument("--voice", default="", help="TTS voice persona (defaults to Orus for male avatars, Aoede for female avatars).")
    ap.add_argument("--steps", nargs="*", default=["tts", "mp4"], choices=["tts", "mp4", "replace"])
    args = ap.parse_args()
    vbf.CHROME_WAIT_S = 300.0
    avatar_name = vbf.book_avatar(args.book_dir, args.avatar)
    voice = args.voice or vbf.avatar_voice(avatar_name)
    if "tts" in args.steps:
        synth_all(args.book_dir, voice, lang=args.lang)
    if "mp4" in args.steps:
        build_mp4s(args.book_dir, voice, lang=args.lang, avatar_name=avatar_name)
    if "replace" in args.steps:
        if not args.vid_id:
            ap.error("--vid-id is required for the replace step")
        replace_all(args.book_dir, args.vid_id, voice, lang=args.lang, avatar_name=avatar_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
