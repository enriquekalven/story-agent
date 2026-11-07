"""Build a book's Google Vids projects (all languages) by cloning an approved reference book's projects.

Pipeline per language (resumable; state in `<book>/google_vids/vids_projects.json` + `vids_pipeline_state.json`):

1. clone   - copy the reference book's Vids project for that language. Uses the Drive API when ADC is valid,
             otherwise the editor's `/copy` page in a background tab ("Make a copy" redirects that same tab
             to the new project), then renames it.
2. normalize - drop the reference's 2.5s bumper scene if it has 21 scenes (Book 2 EN).
3. retarget - for every scene: replace the 16:9 stage slide and the localized voiceover script.
4. avatars  - click Update/Insert in the AI avatar sidebar for every scene, round-robin across languages
             (the avatar keeps the reference's crop / mask / position).

Chinese (ZH) uses uploaded MP4 "avatars" (EN Lennon video + Chinese TTS), so step 4 for ZH is handled by
`vids_chinese_flow.py` after the EN avatars are rendered.

All browser work goes through `vids_book_flow.exec_js` on background tabs: Chrome is never activated and no
window's active tab is touched.

Usage:
    python vids_book_pipeline.py --book-dir "output/Long Running Agents" \
        --reference-dir "output/Year of the Horse" --langs en es pt ja ko fr it de zh --steps clone retarget
    python vids_book_pipeline.py --book-dir "output/Long Running Agents" --langs en es pt --steps avatars
"""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import vids_avatar_layout as val
import vids_book_flow as vbf

AVATAR_LANGS = ["en", "es", "pt", "ja", "ko", "fr", "it", "de"]
# Languages without their own project in the reference book clone from a sibling (same MP4-avatar layout).
CLONE_FALLBACK = {"yue": "zh"}

# ------------------------------------------------------------------------------------------------
# Small JXA helpers (read-only tab lookups; never touch active tabs)
# ------------------------------------------------------------------------------------------------


def _jxa(script: str, *args: str) -> str:
    proc = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, *args], capture_output=True, text=True)
    return (proc.stdout or "").strip()


_FIND_TAB = r"""
function run(argv) {
    const app = Application(parseInt(argv[0], 10));
    const needle = argv[1];
    const byId = argv[2] === "id";
    const bgOnly = argv[3] === "bg";
    const wins = app.windows();
    for (let wi = 0; wi < wins.length; wi++) {
        try {
            const wid = wins[wi].id();
            const w = app.windows.byId(wid);
            let act = Number(w.activeTabIndex());
            const tabs = w.tabs();
            const n = tabs.length;
            if (n > 0 && (tabs[0].url() || "").indexOf("story-agent-automation") !== -1 && act !== 1) {
                try { w.activeTabIndex = 1; act = 1; } catch (e2) {}
            }
            for (let ti = 0; ti < n; ti++) {
                if (bgOnly && ti + 1 === act) continue;
                try {
                    const tid = tabs[ti].id();
                    const t = w.tabs.byId(tid);
                    const hit = byId ? String(tid) === needle : (t.url() || "").indexOf(needle) !== -1;
                    if (hit) return JSON.stringify({id: Number(tid), url: t.url(), active: ti + 1 === act});
                } catch (e) {}
            }
        } catch (e) {}
    }
    return "";
}
"""


def find_tab(needle: str, by_id: bool = False, background_only: bool = False) -> Optional[Dict[str, Any]]:
    raw = _jxa(_FIND_TAB, str(vbf._main_chrome_pid()), needle, "id" if by_id else "url", "bg" if background_only else "")
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


# One Chrome window is reserved for automation. Its active tab is this placeholder, so every Vids tab we open
# there is a background tab (exec_js never touches active tabs) and the user's own windows are left alone.
AUTOMATION_MARK = "story-agent-automation"
AUTOMATION_PAGE = "data:text/html;charset=utf-8," + urllib.parse.quote(
    "<title>Story Agent automation</title><p style='font:16px -apple-system,sans-serif;margin:2em'>"
    "Story Agent is building Google Vids in this window's background tabs. Please leave it open.</p>"
    "<!--" + AUTOMATION_MARK + "-->",
    safe="",
)

_ENSURE_WINDOW = r"""
function run(argv) {
    const app = Application(parseInt(argv[0], 10));
    const mark = argv[1];
    const ensureSize = (w) => {
        try {
            const b = w.bounds();
            if (!b || b.width < 1200 || b.height < 820) {
                const wasMin = !!w.minimized();
                if (wasMin) { try { w.minimized = false; delay(0.15); } catch (e0) {} }
                try { w.bounds = {x: 40, y: 40, width: 1440, height: 920}; } catch (e1) {}
                if (wasMin || argv[5] === "1") { try { w.minimized = true; } catch (e2) {} }
            }
        } catch (e) {}
    };
    const wins = app.windows;
    for (let wi = 0; wi < wins.length; wi++) {
        try {
            const w = wins[wi];
            for (let ti = 0; ti < w.tabs.length; ti++) {
                if ((w.tabs[ti].url() || "").indexOf(mark) !== -1) {
                    ensureSize(w);
                    return JSON.stringify({id: Number(w.id()), created: false});
                }
            }
        } catch (e) {}
    }
    if (argv[4] !== "1") return JSON.stringify({id: 0, created: false});
    const w = app.Window().make();
    try { w.bounds = {x: 40, y: 40, width: 1440, height: 920}; } catch (e) {}
    const t = w.tabs[0];
    t.url = argv[2];
    delay(1);
    if ((t.url() || "").indexOf(mark) === -1) t.url = argv[3];
    if (argv[5] === "1") { try { w.minimized = true; } catch (e) {} }
    return JSON.stringify({id: Number(w.id()), created: true});
}
"""


def ensure_window(create: bool = True, minimize: bool = True) -> int:
    """Returns the automation window id (0 if absent and `create` is False), creating it minimized if needed."""
    raw = _jxa(
        _ENSURE_WINDOW, str(vbf._main_chrome_pid()), AUTOMATION_MARK, AUTOMATION_PAGE,
        "about:blank#" + AUTOMATION_MARK, "1" if create else "0", "1" if minimize else "0",
    )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Could not find or create the automation Chrome window: {raw!r}")
    if info.get("created"):
        print(f"  opened automation window {info['id']}", flush=True)
    return int(info["id"])


_OPEN_BG = r"""
function run(argv) {
    const app = Application(parseInt(argv[0], 10));
    const winId = parseInt(argv[1], 10);
    try {
        const w = app.windows.byId(winId);
        w.tabs.push(app.Tab({url: argv[2]}));
        w.activeTabIndex = 1;
        // Chrome re-activates the new tab 2-4s later, once it loads; keep tab 1 active so Vids tabs stay in background.
        for (let k = 0; k < 20; k++) {
            delay(0.2);
            if (Number(w.activeTabIndex()) !== 1) w.activeTabIndex = 1;
        }
        return "OK";
    } catch (e) {}
    return "NO_WINDOW";
}
"""


def _u0_url(url: str) -> str:
    return re.sub(r"https://docs\.google\.com/videos/d/", "https://docs.google.com/videos/u/0/d/", url or "")


def open_background_tab(url: str) -> None:
    """Opens `url` as a background tab of the automation window (its placeholder tab stays active)."""
    _jxa(_OPEN_BG, str(vbf._main_chrome_pid()), str(ensure_window()), _u0_url(url))


_CLOSE_BG = r"""
function run(argv) {
    const app = Application(parseInt(argv[0], 10));
    const winId = parseInt(argv[1], 10);
    const needle = argv[2];
    let closed = 0;
    try {
        const w = app.windows.byId(winId);
        if (Number(w.activeTabIndex()) !== 1) w.activeTabIndex = 1;
        const tabs = w.tabs();
        for (let ti = tabs.length - 1; ti >= 1; ti--) {
            const t = w.tabs.byId(tabs[ti].id());
            if ((t.url() || "").indexOf(needle) !== -1) {
                try { t.url = "about:blank"; delay(0.25); } catch (e2) {}
                t.close();
                closed++;
            }
        }
    } catch (e) {}
    return String(closed);
}
"""


def close_automation_tabs(needle: str) -> int:
    """Closes background tabs whose URL contains `needle`, only inside the automation window."""
    win = ensure_window(create=False)
    if not win:
        return 0
    time.sleep(2.5)
    raw = _jxa(_CLOSE_BG, str(vbf._main_chrome_pid()), str(win), needle)
    return int(raw) if raw.isdigit() else 0


def ensure_tab(doc_id: str, url: str, wait: bool = True, timeout: float = 150.0) -> bool:
    """Makes sure `doc_id` is open in a background tab; with `wait`, returns whether its editor loaded."""
    if not find_tab(doc_id, background_only=True):
        open_background_tab(url)
        print(f"  opened background tab for {doc_id}", flush=True)
        time.sleep(3.0)
    return bool(wait_for_editor(doc_id, timeout)) if wait else True


def doc_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/videos/(?:u/\d+/)?d/([^/?#]+)", url or "")
    return m.group(1) if m else None


def resource_key_from_url(url: str) -> str:
    m = re.search(r"resourcekey=([^&#]+)", url or "")
    return m.group(1) if m else ""


# ------------------------------------------------------------------------------------------------
# Step 1: clone
# ------------------------------------------------------------------------------------------------


def clone_with_drive_api(src_id: str, name: str, folder_id: Optional[str]) -> Optional[Dict[str, str]]:
    try:
        import drive_sync

        svc = drive_sync.init_drive_service(no_drive=False)
        if not svc:
            return None
        body: Dict[str, Any] = {"name": name}
        if folder_id:
            body["parents"] = [folder_id]
        f = svc.files().copy(fileId=src_id, body=body, fields="id,resourceKey", supportsAllDrives=True).execute()
        rk = f.get("resourceKey")
        url = f"https://docs.google.com/videos/d/{f['id']}/edit" + (f"?resourcekey={rk}" if rk else "")
        open_background_tab(url)
        return {"id": f["id"], "url": url, "method": "drive_api"}
    except Exception as exc:  # pragma: no cover - network/auth dependent
        print(f"  Drive API copy unavailable ({exc}); falling back to /copy page.", flush=True)
        return None


def clone_with_copy_page(src_id: str, src_url: str, marker: str, timeout: float = 120.0) -> Dict[str, str]:
    """Opens `/copy` for the source in a background tab, clicks 'Make a copy', returns the new doc."""
    rk = resource_key_from_url(src_url)
    copy_url = f"https://docs.google.com/videos/d/{src_id}/copy?" + (f"resourcekey={rk}&" if rk else "") + marker
    tab = find_tab(marker)
    if not tab:
        open_background_tab(copy_url)
        time.sleep(3.0)
        tab = find_tab(marker)
    if not tab:
        raise RuntimeError(f"Could not open copy page for {src_id}")
    tab_id = str(tab["id"])
    if tab["active"]:
        raise RuntimeError("Copy page tab became active; refusing to automate the user's active tab.")
    deadline = time.time() + 30
    clicked = ""
    while time.time() < deadline:
        clicked = vbf.exec_js(marker, "(() => { const b = document.getElementById('confirmActionButton'); if (!b) return 'WAIT'; b.click(); return 'CLICKED'; })()", hooks=False)
        if clicked == "CLICKED":
            break
        time.sleep(1.5)
    if clicked != "CLICKED":
        raise RuntimeError(f"'Make a copy' button not found for {src_id}: {clicked}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.0)
        t = find_tab(tab_id, by_id=True)
        new_id = doc_id_from_url(t["url"]) if t else None
        if new_id and new_id != src_id and "/edit" in t["url"]:
            return {"id": new_id, "url": t["url"], "method": "copy_page"}
    raise RuntimeError(f"Timed out waiting for the copy of {src_id}")


RENAME_JS = r"""(() => {
  const inp = document.querySelector('input.docs-title-input');
  if (!inp) return 'NO_TITLE_INPUT';
  const name = __NAME__;
  if (document.title === name + ' - Google Vids' && inp.value === name) return inp.value;
  if (inp.value === name) inp.value = '';
  inp.dispatchEvent(new FocusEvent('focus', {bubbles: false}));
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(inp, name);
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.dispatchEvent(new FocusEvent('blur', {bubbles: false}));
  return inp.value;
})()"""


def rename(doc_id: str, name: str) -> str:
    return vbf.exec_js(doc_id, RENAME_JS.replace("__NAME__", json.dumps(name)), hooks=True)


def wait_for_editor(doc_id: str, timeout: float = 120.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = vbf.scene_count(doc_id)
        if n:
            return n
        time.sleep(3.0)
    return 0


# ------------------------------------------------------------------------------------------------
# Steps 2-3: normalize + retarget
# ------------------------------------------------------------------------------------------------


def normalize_scenes(doc_id: str, pages: int) -> int:
    n = vbf.scene_count(doc_id)
    if n == pages + 1:
        nav = vbf.navigate_to_scene(doc_id, 2)
        res = vbf.delete_current_scene(doc_id)
        time.sleep(1.5)
        print(f"  normalized {n} -> {vbf.scene_count(doc_id)} scenes ({nav}: {res})", flush=True)
    return vbf.scene_count(doc_id)


SELECT_SLIDE_JS = r"""(() => {
  const fire = (el, x, y) => ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0})));
  const more = document.getElementById('moreButton');
  if (more && more.offsetParent !== null && more.getAttribute('aria-pressed') !== 'true') {
    const mr = more.getBoundingClientRect();
    fire(more, mr.left + mr.width / 2, mr.top + mr.height / 2);
  }
  const btn = () => { const b = document.getElementById('replaceImageMenu'); return b && b.offsetParent !== null && !/disabled/.test(b.className) ? b : null; };
  const leaves = [...document.querySelectorAll("svg g[id^='editor-']")].filter(g => {
    const r = g.getBoundingClientRect();
    return r.width > 20 && r.height > 20 && !g.id.endsWith('-bg') && g.querySelectorAll("g[id^='editor-']").length === 0;
  }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
  if (!leaves.length) return 'NO_SLIDE_G';
  const sr = leaves[0].getBoundingClientRect();
  const x = sr.left + 60, y = sr.top + 60;
  fire(document.elementFromPoint(x, y) || leaves[0], x, y);
  return btn() ? 'SELECTED' : 'NOT_SELECTED';
})()"""


def replace_slide(doc_id: str, slide_jpg: str, upload_name: str) -> str:
    """Selects the scene's slide image (retrying until the image toolbar shows) and replaces it."""
    sel = ""
    for _ in range(4):
        sel = vbf.exec_js(doc_id, SELECT_SLIDE_JS)
        if sel == "SELECTED":
            break
        time.sleep(0.5)
    if sel != "SELECTED":
        return f"SELECT_FAILED:{sel}"
    return vbf.replace_scene_slide(doc_id, slide_jpg, upload_name)


CANVAS_TEXT_JS = r"""(() => JSON.stringify([...document.querySelectorAll("svg g[id^='editor-'][id*='-paragraph-']")]
  .filter(g => { const r = g.getBoundingClientRect(); return r.width > 2 && r.height > 2; })
  .map(g => (g.textContent || '').replace(/\s+/g, ''))))()"""

# Selects the first canvas text box and leaves text-edit mode; refuses if the slide image or avatar video is what got
# selected (so the Edit > Delete that follows can only ever remove a text box).
SELECT_TEXT_BOX_JS = r"""(() => {
  const fire = (el, x, y) => ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0})));
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 1 && r.height > 1 && el.offsetParent !== null; };
  const para = [...document.querySelectorAll("svg g[id^='editor-'][id*='-paragraph-']")].find(g => { const r = g.getBoundingClientRect(); return r.width > 2 && r.height > 2; });
  if (!para) return 'NONE';
  const r = para.getBoundingClientRect();
  const x = r.left + Math.min(8, r.width / 2), y = r.top + r.height / 2;
  fire(document.elementFromPoint(x, y) || para, x, y);
  const iframe = document.querySelector('iframe.docs-texteventtarget-iframe');
  const tgt = iframe && iframe.contentDocument && (iframe.contentDocument.querySelector("[contenteditable='true']") || iframe.contentDocument.body);
  if (tgt) tgt.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true}));
  if (vis(document.getElementById('replaceImageMenu')) || vis(document.getElementById('replaceVideoMenu'))) return 'WRONG_SELECTION';
  return vis(document.getElementById('docs-font-family')) ? 'TEXT_SELECTED' : 'NOT_TEXT';
})()"""

EDIT_DELETE_JS = r"""(() => {
  const fire = (el, x, y) => ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, clientX: x || 0, clientY: y || 0, button: 0})));
  const edit = [...document.querySelectorAll('[role=menubar] [role=menuitem]')].find(e => e.innerText.trim() === 'Edit');
  if (!edit) return 'NO_EDIT_MENU';
  fire(edit);
  const items = [...document.querySelectorAll('.goog-menu [role=menuitem]')].filter(e => e.offsetParent !== null);
  const del = items.find(e => e.innerText.trim().split('\n')[0].trim() === 'Delete');
  if (!del) { fire(edit); return 'NO_DELETE_ITEM'; }
  if (del.getAttribute('aria-disabled') === 'true') { fire(edit); return 'DELETE_DISABLED'; }
  const r = del.getBoundingClientRect();
  fire(del, r.left + 10, r.top + 5);
  return 'DELETED';
})()"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def remove_stray_text(doc_id: str, script: str) -> List[str]:
    """Deletes canvas text boxes on the current scene that hold script text (a paste that missed the sidebar)."""
    log: List[str] = []
    probe = _norm(script)[:8]
    for _ in range(3):
        try:
            boxes = json.loads(vbf.exec_js(doc_id, CANVAS_TEXT_JS) or "[]")
        except json.JSONDecodeError:
            return log + ["UNREADABLE"]
        stray = [b for b in boxes if b and probe and (probe in b or b[:8] in _norm(script))]
        if not stray:
            return log
        sel = vbf.exec_js(doc_id, SELECT_TEXT_BOX_JS)
        log.append(sel)
        if sel != "TEXT_SELECTED":
            return log
        log.append(vbf.exec_js(doc_id, EDIT_DELETE_JS))
        time.sleep(0.8)
    return log


def script_landed(doc_id: str, script: str) -> bool:
    """True when the AI-avatar sidebar shows the full `script` (and no copy of it sits on the canvas)."""
    norm_script = _norm(script)
    if not norm_script:
        return True
    sidebar = _norm(vbf.scene_state(doc_id).get("text", ""))
    try:
        boxes = json.loads(vbf.exec_js(doc_id, CANVAS_TEXT_JS) or "[]")
    except json.JSONDecodeError:
        boxes = []
    probe = norm_script[:8]
    return norm_script in sidebar and not any(probe in b for b in boxes)


def retarget(doc_id: str, book_dir: str, lang: str, state: Dict[str, Any], save, url: str = "", avatar_name: str = "") -> None:
    slides = vbf.extract_stage_slides(book_dir, lang)
    scripts = vbf.load_scripts(book_dir, lang)
    done = set(state.setdefault("retargeted", []))
    tag = re.sub(r"[^a-z0-9]+", "_", os.path.basename(os.path.normpath(book_dir)).lower())
    for s in range(1, len(slides) + 1):
        if s in done:
            continue
        nav = vbf.navigate_to_scene(doc_id, s)
        if nav == "TAB_NOT_FOUND" and url:
            ensure_tab(doc_id, url)
            nav = vbf.navigate_to_scene(doc_id, s)
        if not (nav or "").startswith(f"Scene {s} /"):
            time.sleep(1.0)
            nav = vbf.navigate_to_scene(doc_id, s)
        if not (nav or "").startswith(f"Scene {s} /"):
            print(f"  [{lang.upper()} scene {s:02d}] navigation failed ({nav}); RETRY_LATER", flush=True)
            continue  # never edit whichever scene happens to be open
        cleanup = remove_stray_text(doc_id, scripts[s - 1])
        sc = "PASTED"
        if not script_landed(doc_id, scripts[s - 1]):
            for _ in range(2):
                sc = vbf.update_scene_script(doc_id, scripts[s - 1])
                time.sleep(0.6)
                cleanup += remove_stray_text(doc_id, scripts[s - 1])
                if script_landed(doc_id, scripts[s - 1]):
                    break
            else:
                sc = "NOT_IN_SIDEBAR"
        av_sel = ""
        if avatar_name and lang in AVATAR_LANGS:
            av_sel = f" avatar={vbf.select_avatar(doc_id, avatar_name)}"
        sl = replace_slide(doc_id, slides[s - 1], f"{tag}_{lang}_page_{s:02d}_stage.jpg")
        ok = sc == "PASTED" and sl.endswith(".jpg")
        extra = f" cleanup={cleanup}" if cleanup else ""
        print(f"  [{lang.upper()} scene {s:02d}] ({nav}) script={sc} slide={sl}{av_sel}{extra} {'OK' if ok else 'RETRY_LATER'}", flush=True)
        if ok:
            done.add(s)
            state["retargeted"] = sorted(done)
            save()


# ------------------------------------------------------------------------------------------------
# Step 4: avatars (round-robin across languages)
# ------------------------------------------------------------------------------------------------


_CLICK_AVATAR_BTN_JS = """(() => {
    const fire = (el) => {
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        const hit = (r.width > 0 && r.height > 0) ? document.elementFromPoint(x, y) : null;
        const tgt = (hit && el.contains(hit)) ? hit : el;
        try { tgt.focus(); } catch (e) {}
        tgt.dispatchEvent(new MouseEvent("mouseover", {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window}));
        tgt.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1, view: window}));
        tgt.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 0, view: window}));
        tgt.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window}));
    };
    let sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
    if (!sidebar || sidebar.offsetParent === null) {
        const avBtn = document.getElementById("content-library-rail-avatars-element") || document.querySelector("[aria-label='Generate an avatar']");
        if (avBtn) fire(avBtn);
        sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
    }
    if (!sidebar) return "NO_SIDEBAR";
    const btns = [...sidebar.querySelectorAll("div[role='button'], button")].filter(e => e.offsetParent !== null);
    const target = btns.find(b => /^(Update|Insert|Generate)$/i.test(b.innerText.trim()));
    if (target) {
        if (target.getAttribute("aria-disabled") === "true" || target.disabled) return "DISABLED";
        fire(target);
        return "CLICKED_" + target.innerText.trim().toUpperCase();
    }
    const prev = btns.find(b => /^Preview$/i.test(b.innerText.trim()));
    if (prev) {
        if (prev.getAttribute("aria-disabled") === "true" || prev.disabled) return "DISABLED";
        fire(prev);
        return "PREVIEW_STARTED";
    }
    if (btns.some(b => /^Creating$/i.test(b.innerText.trim()))) return "PREVIEW_CREATING";
    return "NO_BTN";
})()"""


def click_update_or_insert(doc_id: str) -> str:
    res = vbf.exec_js(doc_id, _CLICK_AVATAR_BTN_JS)
    if res in ("PREVIEW_STARTED", "PREVIEW_CREATING"):
        for _ in range(15):
            time.sleep(0.8)
            r2 = vbf.exec_js(doc_id, _CLICK_AVATAR_BTN_JS)
            if r2.startswith("CLICKED_") or r2 == "NO_BTN":
                return r2
        return "PREVIEW_CREATING"
    return res


LIMIT_BACKOFF_S = 1800.0  # Vids doesn't say when the avatar quota resets; re-probe every 30 minutes.


def _is_limited(state: Dict[str, Any]) -> bool:
    txt = (state.get("text") or "").lower()
    return bool(state.get("limited")) or "hit your limit" in txt or "reached your monthly limit" in txt


def _sync_avatar_choice(st: Dict[str, Any], avatar_name: str, total: int) -> None:
    """When the requested avatar changes, clears completed scenes so all 20 re-render with the new avatar."""
    if not (avatar_name or "").strip():
        return
    want = re.sub(r"\s+", " ", avatar_name.strip().lower())
    cur = re.sub(r"\s+", " ", (st.get("avatar_name") or "").strip().lower())
    if cur == want:
        return
    if st.get("avatars_done") or st.get("finalized") or st.get("avatar_clicked"):
        st["force_refresh"] = list(range(1, total + 1))
    st["avatar_name"] = avatar_name.strip()
    st["avatars_done"] = []
    st["avatar_clicked"] = []
    st["avatar_cursor"] = 1
    st.pop("finalized", None)
    st.pop("layout", None)
    st.pop("limited_at", None)


def avatar_step(
    lang: str,
    doc_id: str,
    st: Dict[str, Any],
    total: int,
    avatar_name: str = "",
    scripts: Optional[List[str]] = None,
) -> str:
    _sync_avatar_choice(st, avatar_name, total)
    done = set(st.setdefault("avatars_done", []))
    cur = st.setdefault("avatar_cursor", 1)
    if len(done) >= total:
        return f"{lang.upper()}:{total}/{total}"
    state = vbf.scene_state(doc_id)
    if _is_limited(state):  # quota banner: the clicked render never started
        st["limited_at"] = time.time()
        return f"{lang.upper()}:S{cur:02d}(LIMIT)[{len(done)}/{total}]"
    if state.get("busy"):
        return f"{lang.upper()}:S{cur:02d}({state.get('pct') or '..'}%)[{len(done)}/{total}]"
    for off in range(total):
        s = ((cur - 1 + off) % total) + 1
        if s in done:
            continue
        nav = vbf.navigate_to_scene(doc_id, s)
        if not (nav or "").startswith(f"Scene {s} /"):
            st["avatar_cursor"] = s
            return f"{lang.upper()}:S{s:02d}(NAV_RETRY)[{len(done)}/{total}]"
        state = vbf.scene_state(doc_id)
        if avatar_name and not vbf.avatar_matches(state.get("avatarSrc", ""), avatar_name):
            vbf.select_avatar(doc_id, avatar_name)
            state = vbf.scene_state(doc_id)
        if _is_limited(state):
            st["avatar_cursor"] = s
            st["limited_at"] = time.time()
            return f"{lang.upper()}:S{s:02d}(LIMIT)[{len(done)}/{total}]"
        if state.get("busy"):
            st["avatar_cursor"] = s
            return f"{lang.upper()}:S{s:02d}(BUSY)[{len(done)}/{total}]"
        btns = state.get("buttons") or []
        has_err = bool(re.search(r"internal error|something went wrong|try again", state.get("text") or "", re.I))
        clicked_before = s in set(st.setdefault("avatar_clicked", []))
        if (
            clicked_before
            and not has_err
            and not state.get("outOfSync")
            and ("Update" in btns or ("Preview" in btns and "Generate" not in btns and "Creating" not in btns))
        ):
            done.add(s)
            st["avatars_done"] = sorted(done)
            continue
        if clicked_before and has_err:
            st["avatar_clicked"] = sorted(set(st["avatar_clicked"]) - {s})
            clicked_before = False
        if (
            not clicked_before
            and "Update" in btns
            and not state.get("outOfSync")
            and scripts
            and s <= len(scripts)
            and (s in set(st.get("force_refresh", [])) or state.get("updateDisabled"))
        ):
            # Switching avatars on an already-rendered scene leaves Update disabled until the script is nudged.
            vbf.update_scene_script(doc_id, scripts[s - 1] + "\u200b")
            time.sleep(0.4)
            remove_stray_text(doc_id, scripts[s - 1])
        res = click_update_or_insert(doc_id)
        if res.startswith("CLICKED"):
            st["avatar_clicked"] = sorted(set(st["avatar_clicked"]) | {s})
        st["avatar_cursor"] = s
        return f"{lang.upper()}:S{s:02d}({res})[{len(done)}/{total}]"
    return f"{lang.upper()}:{len(done)}/{total}"


# ------------------------------------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------------------------------------


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _merge_save(path: str, mine: Dict[str, Any], owned: List[str]) -> None:
    """Re-reads `path` and overwrites only the languages this process owns (other processes own the rest)."""
    cur = _read_json(path)
    for lang in owned:
        if lang in mine:
            cur[lang] = mine[lang]
    _write_json_atomic(path, cur)


def finalize_language(lang: str, doc_id: str, st: Dict[str, Any], ref: Optional[Dict[int, Dict[str, Any]]], total: int) -> str:
    """After all avatars render: check/fix the avatar layout against `ref`, then close the language's tab."""
    if ref:
        try:
            rep = val.fix_doc(doc_id, ref, list(range(1, total + 1)), emit=lambda rec: print("    layout:", json.dumps(rec)[:300], flush=True))
            st["layout"] = {"verified": rep["verified"], "failed": [r.get("page") for r in rep["failed"]]}
        except Exception as exc:  # editor busy / tab reloading: retry on a later tick (max 3 tries)
            st["layout_attempts"] = st.get("layout_attempts", 0) + 1
            st["layout"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            if st["layout_attempts"] < 3:
                return f"{lang.upper()}:LAYOUT_RETRY"
    st["finalized"] = True
    closed = close_automation_tabs(doc_id)
    return f"{lang.upper()}:FINAL layout={st.get('layout')} closed_tabs={closed}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--book-dir", required=True)
    ap.add_argument("--reference-dir", default="output/Year of the Horse")
    ap.add_argument("--langs", nargs="*", default=["en", "es", "pt", "ja", "ko", "fr", "it", "de", "zh"])
    ap.add_argument("--steps", nargs="*", default=["clone", "retarget"], choices=["clone", "retarget", "avatars"])
    ap.add_argument("--drive-folder-id", default="")
    ap.add_argument("--avatar", default="", help="Vids AI avatar name (e.g. 'Rio Classic', 'Alan Classic', 'Sue Classic'; defaults to the book's canonical avatar).")
    ap.add_argument("--layout-json", default="", help="Reference avatar layout (vids_avatar_layout --save-reference) to enforce after avatars render.")
    ap.add_argument("--chrome-wait", type=float, default=900.0, help="Seconds to wait for Chrome to come back after a restart.")
    ap.add_argument(
        "--no-drive-api",
        action="store_true",
        help="Clone only via the editor's /copy page in the automation window, so the copy is owned by that window's account.",
    )
    ap.add_argument(
        "--clone-source",
        nargs="*",
        default=[],
        metavar="LANG=SRC",
        help="Clone LANG from the reference's SRC-language project (e.g. zh=yue when only the YUE template is shared).",
    )
    ap.add_argument(
        "--avatar-mode",
        choices=["default", "mp4"],
        default="default",
        help="Avatar mode: 'default' uses Vids AI avatar UI for AVATAR_LANGS; 'mp4' synthesizes local TTS + timed MP4 avatars for all requested languages (quota-free).",
    )
    args = ap.parse_args()
    vbf.CHROME_WAIT_S = args.chrome_wait

    book_dir = args.book_dir
    avatar_name = vbf.book_avatar(book_dir, args.avatar)
    story = _read_json(os.path.join(book_dir, "story.json"))
    title = story["title"]
    total = len(story["pages"])
    ref_manifest = _read_json(os.path.join(args.reference_dir, "manifest.json"))
    ref_urls: Dict[str, str] = ref_manifest.get("google_vids", {}).get("drive_vids_urls", {})

    vids_dir = os.path.join(book_dir, "google_vids")
    projects_path = os.path.join(vids_dir, "vids_projects.json")
    state_path = os.path.join(vids_dir, "vids_pipeline_state.json")
    avatar_path = os.path.join(vids_dir, "vids_avatar_state.json")
    projects = _read_json(projects_path)
    state = _read_json(state_path)

    def save() -> None:
        _merge_save(projects_path, projects, args.langs)
        _merge_save(state_path, state, args.langs)

    clone_source = dict(pair.split("=", 1) for pair in args.clone_source if "=" in pair)
    if "clone" in args.steps or "retarget" in args.steps:
        for lang in args.langs:
            st = state.setdefault(lang, {})
            src_lang = clone_source.get(lang, lang)
            src_url = ref_urls.get(src_lang) or ref_urls.get(CLONE_FALLBACK.get(lang, ""))
            if not src_url:
                print(f"[{lang.upper()}] no reference Vids URL; skipping", flush=True)
                continue
            src_id = doc_id_from_url(src_url)

            if "clone" in args.steps and lang not in projects:
                name = f"{title} ({lang.upper()}) — Google Vids"
                marker = f"sa_clone={re.sub(r'[^a-z0-9]+', '_', title.lower())}_{lang}"
                api_info = None if args.no_drive_api else clone_with_drive_api(src_id, name, args.drive_folder_id or None)
                info = api_info or clone_with_copy_page(src_id, src_url, marker)
                projects[lang] = {"id": info["id"], "url": info["url"], "source_id": src_id, "name": name, "method": info["method"]}
                save()
                print(f"[{lang.upper()}] cloned {src_id} -> {info['id']} via {info['method']}", flush=True)
                n = wait_for_editor(info["id"])
                if info["method"] == "copy_page":
                    print(f"[{lang.upper()}] rename -> {rename(info['id'], name)!r} (scenes={n})", flush=True)

            if lang not in projects:
                continue
            doc_id = projects[lang]["id"]

            if "retarget" in args.steps and len(st.get("retargeted", [])) < total:
                if not ensure_tab(doc_id, projects[lang]["url"]):
                    print(f"[{lang.upper()}] editor not ready; skipping retarget", flush=True)
                    continue
                n = normalize_scenes(doc_id, total)
                if n != total:
                    print(f"[{lang.upper()}] unexpected scene count {n}; skipping retarget", flush=True)
                    continue
                retarget_av = "" if args.avatar_mode == "mp4" else avatar_name
                for _ in range(2):  # second pass picks up scenes whose slide upload needs a retry
                    retarget(doc_id, book_dir, lang, st, save, url=projects[lang]["url"], avatar_name=retarget_av)
                    if len(st.get("retargeted", [])) >= total:
                        break
                print(f"[{lang.upper()}] retargeted {len(st.get('retargeted', []))}/{total}", flush=True)
                if args.avatar_mode == "mp4":
                    close_automation_tabs(doc_id)

    if "avatars" in args.steps:
        ref = {int(k): v for k, v in _read_json(args.layout_json).items()} if args.layout_json else None
        if args.avatar_mode == "mp4":
            import vids_zh_avatars as vza

            voice = vbf.avatar_voice(avatar_name)
            projects = _read_json(projects_path)
            av = _read_json(avatar_path)
            clip_path = vza.rendered_avatar_clip_path(avatar_name)
            if not (os.path.exists(clip_path) and os.path.getsize(clip_path) > 10000):
                seed_lang = "en" if "en" in projects else next((lc for lc in args.langs if lc in projects), "")
                if seed_lang:
                    ensure_tab(projects[seed_lang]["id"], projects[seed_lang]["url"])
                    vza.render_classic_avatar_once(
                        projects[seed_lang]["id"], avatar_name, scene_num=1, donor_dir=args.reference_dir
                    )
            for lc in args.langs:
                if lc not in projects:
                    print(f"[{lc.upper()}] no Vids project found; skipping mp4 avatars", flush=True)
                    continue
                l_spath = vza.state_path(book_dir, lc)
                l_saved = _read_json(l_spath)
                want_actor = vbf.avatar_actor(avatar_name) or "actor16"
                a_st = av.setdefault(lc, {})
                if (
                    l_saved.get("actor") == want_actor
                    and len(l_saved.get("replaced", [])) >= total
                    and a_st.get("finalized")
                    and a_st.get("avatar_name") == avatar_name
                    and (not ref or a_st.get("layout", {}).get("verified") == total)
                ):
                    print(f"[{lc.upper()}] already 20/20 replaced & layout verified; skipping", flush=True)
                    continue
                vza.synth_all(book_dir, voice, lang=lc)
                vza.build_mp4s(book_dir, voice, donor_dir=args.reference_dir, lang=lc, avatar_name=avatar_name)
                vza.replace_all(book_dir, projects[lc]["id"], voice, lang=lc, avatar_name=avatar_name)
                l_saved = _read_json(l_spath)
                a_st["avatar_name"] = avatar_name
                a_st["avatar_mode"] = "mp4"
                a_st["avatars_done"] = l_saved.get("replaced", []) if l_saved.get("actor") == want_actor else []
                if len(a_st["avatars_done"]) >= total:
                    finalize_language(lc, projects[lc]["id"], a_st, ref, total)
                else:
                    close_automation_tabs(projects[lc]["id"])
                _write_json_atomic(avatar_path, av)
            print("ALL MP4 AVATARS COMPLETE", flush=True)
            return 0

        # Single owner of vids_avatar_state.json; languages join as soon as their retarget completes.
        wanted = [lc for lc in args.langs if lc in AVATAR_LANGS]
        av = _read_json(avatar_path)
        for lc in wanted:
            _sync_avatar_choice(av.setdefault(lc, {}), avatar_name, total)
        _write_json_atomic(avatar_path, av)
        scripts_cache: Dict[str, List[str]] = {}
        tick = 0
        while True:
            if tick % 15 == 0:
                projects = _read_json(projects_path)
                state = _read_json(state_path)
            ready = [lc for lc in wanted if lc in projects and len(state.get(lc, {}).get("retargeted", [])) >= total]
            active = [lc for lc in ready if not av.setdefault(lc, {}).get("finalized")]
            if tick % 15 == 0:
                for lc in active:  # re-open tabs lost to a Chrome restart / tab discard
                    if not av.get(lc, {}).get("limited_at"):
                        ensure_tab(projects[lc]["id"], projects[lc]["url"], wait=False)
            parts = []
            waiting = 0
            for lc in active:
                a = av[lc]
                since = time.time() - a.get("limited_at", 0.0)
                if a.get("limited_at") and since < LIMIT_BACKOFF_S:
                    waiting += 1
                    parts.append(f"{lc.upper()}:LIMIT[{len(a.get('avatars_done', []))}/{total}] retry in {int((LIMIT_BACKOFF_S - since) // 60)}m")
                    continue
                if a.pop("limited_at", None):  # back-off over: a fresh tab drops the stale quota banner
                    close_automation_tabs(projects[lc]["id"])
                    ensure_tab(projects[lc]["id"], projects[lc]["url"])
                if lc not in scripts_cache:
                    scripts_cache[lc] = vbf.load_scripts(book_dir, lc)
                parts.append(avatar_step(lc, projects[lc]["id"], a, total, avatar_name=avatar_name, scripts=scripts_cache[lc]))
                if len(a.get("avatars_done", [])) >= total:
                    _write_json_atomic(avatar_path, av)
                    parts.append(finalize_language(lc, projects[lc]["id"], a, ref, total))
            _write_json_atomic(avatar_path, av)
            done_n = sum(1 for lc in wanted if av.get(lc, {}).get("finalized"))
            print(f"[{avatar_name} | ready {len(ready)}/{len(wanted)} final {done_n}] " + " | ".join(parts), flush=True)
            if done_n == len(wanted):
                break
            tick += 1
            time.sleep(60.0 if active and waiting == len(active) else 2.0)
        print("ALL AVATARS COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
