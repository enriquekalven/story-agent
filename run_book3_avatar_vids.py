"""Retargets and renders the 8 Lennon AI Avatar Google Vids projects for Book 3 (Agent Sandbox).

1. Opens all 8 avatar language Vids projects (`en`, `es`, `pt`, `ja`, `ko`, `fr`, `it`, `de`) in background
   tabs of the scriptable Chrome window (never touching the active tab or stealing focus).
2. Normalizes `en` from 21 scenes -> 20 scenes if Scene 2 is the Book 2 bumper scene.
3. Replaces all 20 stage slides (`page_01_stage.jpg`..`page_20_stage.jpg`) and all 20 localized voiceover
   scripts across all 8 languages, while interleaved-polling any language that has already finished its
   20-scene retargeting so Lennon AI Avatar rendering starts immediately on completed languages.
4. Runs the parallel Lennon AI Avatar generation loop across all 8 tabs until all 20 scenes per language
   are complete.
"""

import argparse
import json
import os
import time
from typing import Dict, List

import vids_book_flow

BOOK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "Agent Sandbox")
AVATAR_LANGS = ["en", "es", "pt", "ja", "ko", "fr", "it", "de"]
ZH_DOC_ID = "10Qk7DnSQuyOdMHF1SBrE11EEHL8G5XyjYlN7lvUH1ew"


def click_update_or_insert(doc_id: str) -> str:
    """Clicks Update (when avatar is out of sync after script replacement) or Insert in the AI avatar sidebar."""
    js = """(() => {
        let sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
        if (!sidebar || sidebar.offsetParent === null) {
            const avBtn = document.querySelector("button[aria-label='Generate an avatar']");
            if (avBtn) avBtn.click();
            sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
        }
        if (!sidebar) return "NO_SIDEBAR";
        const fire = (el) => ["mousedown", "mouseup", "click"].forEach(t =>
            el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, button: 0}))
        );
        const btns = [...sidebar.querySelectorAll("div[role='button'], button")].filter(e => e.offsetParent !== null);
        const target = btns.find(b => /^(Update|Insert)$/i.test(b.innerText.trim()));
        if (!target) return "NO_BTN";
        if (target.getAttribute("aria-disabled") === "true" || target.disabled) return "DISABLED";
        fire(target);
        return "CLICKED_" + target.innerText.trim().toUpperCase();
    })()"""
    return vids_book_flow.exec_js(doc_id, js)


def ensure_tabs_open(projs: Dict[str, Dict[str, str]]) -> None:
    avoid = [ZH_DOC_ID] + [projs[lc]["id"] for lc in AVATAR_LANGS]
    opened_any = False
    for lang in AVATAR_LANGS:
        info = projs[lang]
        res = vids_book_flow.open_in_background_tab(info["url"], avoid_doc_ids=avoid)
        print(f"[{lang.upper()}] tab: {res} ({info['id']})", flush=True)
        if res != "ALREADY_OPEN":
            opened_any = True
            time.sleep(0.5)
    if opened_any:
        print("Waiting 8s for newly opened background Vids tabs to initialize...", flush=True)
        time.sleep(8)


def step_avatar_for_lang(
    lang: str,
    doc_id: str,
    completed: Dict[str, set],
    current_scene: Dict[str, int],
) -> str:
    """Checks or advances avatar generation on a single language tab."""
    if len(completed[lang]) >= 20:
        return f"{lang.upper()}:20/20(DONE)"
    st = vids_book_flow.scene_state(doc_id)
    if st.get("busy"):
        sc_txt = st.get("scene") or ""
        if sc_txt.startswith("Scene "):
            try:
                current_scene[lang] = int(sc_txt.split()[1])
            except Exception:
                pass
        pct = st.get("pct") or ".."
        return f"{lang.upper()}:S{current_scene[lang]:02d}({pct}%)[{len(completed[lang])}/20]"

    for offset in range(20):
        s = ((current_scene[lang] - 1 + offset) % 20) + 1
        if s in completed[lang]:
            continue
        nav_lbl = vids_book_flow.navigate_to_scene(doc_id, s)
        if not (nav_lbl or "").startswith(f"Scene {s} /"):
            current_scene[lang] = s
            return f"{lang.upper()}:S{s:02d}(NAV_RETRY)[{len(completed[lang])}/20]"
        st = vids_book_flow.scene_state(doc_id)
        if not (st.get("scene") or "").startswith(f"Scene {s} /"):
            current_scene[lang] = s
            return f"{lang.upper()}:S{s:02d}(STATE_RETRY)[{len(completed[lang])}/20]"
        if st.get("busy"):
            current_scene[lang] = s
            return f"{lang.upper()}:S{s:02d}(BUSY)[{len(completed[lang])}/20]"
        btns = st.get("buttons") or []
        needs_update = st.get("outOfSync") or ("Insert" in btns) or ("Update" not in btns)
        if not needs_update and "Update" in btns:
            completed[lang].add(s)
            continue
        click_res = click_update_or_insert(doc_id)
        current_scene[lang] = s
        return f"{lang.upper()}:S{s:02d}({click_res})[{len(completed[lang])}/20]"

    return f"{lang.upper()}:20/20(DONE)"


def main() -> None:
    parser = argparse.ArgumentParser(description="Retarget and render 8 avatar Vids projects for a book.")
    parser.add_argument("--book-dir", default=BOOK_DIR, help="Path to the book output directory")
    args = parser.parse_args()
    book_dir = args.book_dir

    projs = vids_book_flow.load_projects(book_dir)
    ensure_tabs_open(projs)

    progress_path = os.path.join(book_dir, "google_vids", "retarget_progress.json")
    done_map: Dict[str, List[int]] = {}
    if os.path.exists(progress_path):
        try:
            with open(progress_path, encoding="utf-8") as pf:
                done_map = json.load(pf)
        except Exception:
            done_map = {}

    completed_avatars: Dict[str, set] = {lang: set() for lang in AVATAR_LANGS}
    current_avatar_scene: Dict[str, int] = {lang: 1 for lang in AVATAR_LANGS}
    last_avatar_poll = 0.0

    for lang in AVATAR_LANGS:
        doc_id = projs[lang]["id"]
        slides = vids_book_flow.extract_stage_slides(book_dir, lang)
        scripts = vids_book_flow.load_scripts(book_dir, lang)
        done_scenes = set(done_map.get(lang, []))

        if len(done_scenes) < 20:
            sc_cnt = vids_book_flow.scene_count(doc_id)
            if lang == "en" and sc_cnt == 21:
                nav2 = vids_book_flow.navigate_to_scene(doc_id, 2)
                del_res = vids_book_flow.delete_current_scene(doc_id)
                time.sleep(1.0)
                print(f"[EN] Deleted bumper Scene 2 ({nav2} -> {del_res})", flush=True)

        for s in range(1, 21):
            if s in done_scenes:
                continue
            nav = vids_book_flow.navigate_to_scene(doc_id, s)
            up_sc = vids_book_flow.update_scene_script(doc_id, scripts[s - 1])
            up_sl = vids_book_flow.replace_scene_slide(
                doc_id, slides[s - 1], f"book3_{lang}_page_{s:02d}_stage.jpg"
            )
            print(f"[{lang.upper()} Scene {s:02d}/20] ({nav}) script={up_sc} slide={up_sl}", flush=True)
            done_scenes.add(s)
            done_map[lang] = sorted(done_scenes)
            with open(progress_path, "w", encoding="utf-8") as pf:
                json.dump(done_map, pf, indent=2)

            # Every 6 seconds during retargeting, advance avatars on already-retargeted languages!
            if time.time() - last_avatar_poll >= 6.0:
                ready_langs = [lc for lc in AVATAR_LANGS if len(done_map.get(lc, [])) >= 20]
                if ready_langs:
                    av_statuses = [
                        step_avatar_for_lang(rl, projs[rl]["id"], completed_avatars, current_avatar_scene)
                        for rl in ready_langs
                    ]
                    print("  [AVATAR-POLL] " + " | ".join(av_statuses), flush=True)
                last_avatar_poll = time.time()

    # Now all 8 languages have 20/20 retargeted slides & scripts; finish all remaining avatars in parallel
    while any(len(completed_avatars[lc]) < 20 for lc in AVATAR_LANGS):
        status_parts = [
            step_avatar_for_lang(lang, projs[lang]["id"], completed_avatars, current_avatar_scene)
            for lang in AVATAR_LANGS
        ]
        print(" | ".join(status_parts), flush=True)
        if all(len(completed_avatars[lc]) >= 20 for lc in AVATAR_LANGS):
            break
        time.sleep(2.0)

    print("ALL 8 AVATAR GOOGLE VIDS FOR BOOK 3 COMPLETE!", flush=True)


if __name__ == "__main__":
    main()
