"""Book-level Google Vids builder (all languages) using an approved reference book as the template.

Why clone instead of importing slides from scratch: the reference book's Vids projects (Book 2) already
carry the approved layout, the Lennon AI avatar placement and the 20% background music. For a new book we:

1. Copy each reference-language Vids project into the new book's Drive folder (Drive API `files.copy`).
2. Open each copy in a *background* Chrome tab (reusing duplicate tabs; never activating Chrome and never
   touching the frontmost window's active tab).
3. Retarget every scene: replace the 16:9 stage slide with the new book's localized slide and replace the
   scene script with the new book's localized voiceover script (`vids_exporter.extract_page_voiceover`).
4. Regenerate the Lennon avatar for every scene in parallel across tabs (avatar languages), or run the
   Chinese MP4-audio flow (`vids_chinese_flow`) for ZH.

All browser automation goes through `exec_js`, which installs main-world hooks via a `javascript:` URL and
then runs DOM code with AppleScript `execute javascript` on the tab whose URL contains the Vids doc id.
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import wave
import zipfile
from typing import Any, Dict, List, Optional

import vids_exporter

AVATAR_LANGS = ["en", "es", "pt", "ja", "ko", "fr", "it", "de"]
ALL_LANGS = ["en", "es", "zh", "yue", "pt", "ja", "ko", "fr", "it", "de"]

# Google Vids Classic bucket avatars (Workspace account): picker card title -> presetavatars actor id.
AVATAR_ACTORS: Dict[str, str] = {
    # Realistic Classic (actor01..actor12)
    "aditi": "actor01",
    "aditi classic": "actor01",
    "amal": "actor02",
    "amal classic": "actor02",
    "caleb": "actor03",
    "caleb classic": "actor03",
    "emilia": "actor04",
    "emilia classic": "actor04",
    "eric": "actor05",
    "eric classic": "actor05",
    "farah": "actor06",
    "farah classic": "actor06",
    "jada": "actor07",
    "jada classic": "actor07",
    "james": "actor08",
    "james classic": "actor08",
    "kim": "actor09",
    "kim classic": "actor09",
    "leo": "actor10",
    "leo classic": "actor10",
    "olivia": "actor11",
    "olivia classic": "actor11",
    "samir": "actor12",
    "samir classic": "actor12",
    # 2D Cartoon Classic (actor13..actor18)
    "aaron": "actor13",
    "aaron classic": "actor13",
    "alan": "actor14",
    "alan classic": "actor14",
    "diego": "actor15",
    "diego classic": "actor15",
    "lennon": "actor16",
    "lennon classic": "actor16",
    "rio": "actor17",
    "rio classic": "actor17",
    "sue": "actor18",
    "sue classic": "actor18",
    # 3D Cartoon Classic (actor19..actor23)
    "aden": "actor19",
    "aden classic": "actor19",
    "alexis": "actor20",
    "alexis classic": "actor20",
    "kai": "actor21",
    "kai classic": "actor21",
    "kevin": "actor22",
    "kevin classic": "actor22",
    "paul": "actor23",
    "paul classic": "actor23",
}

# Matching TTS voice persona (Cloud TTS Chirp3-HD + Gemini TTS): Orus for male avatars, Aoede for female avatars.
AVATAR_VOICES: Dict[str, str] = {
    "actor01": "Aoede",
    "actor02": "Aoede",
    "actor03": "Orus",
    "actor04": "Aoede",
    "actor05": "Orus",
    "actor06": "Aoede",
    "actor07": "Aoede",
    "actor08": "Orus",
    "actor09": "Aoede",
    "actor10": "Orus",
    "actor11": "Aoede",
    "actor12": "Orus",
    "actor13": "Orus",   # Aaron Classic (male)
    "actor14": "Orus",   # Alan Classic (male)
    "actor15": "Orus",   # Diego Classic (male)
    "actor16": "Aoede",  # Lennon Classic (female)
    "actor17": "Aoede",  # Rio Classic (female)
    "actor18": "Aoede",  # Sue Classic (female)
    "actor19": "Aoede",  # Aden Classic (female)
    "actor20": "Aoede",  # Alexis Classic (female)
    "actor21": "Orus",   # Kai Classic (male)
    "actor22": "Orus",   # Kevin Classic (male)
    "actor23": "Orus",   # Paul Classic (male)
}

# 11 Cartoon Classic avatars in Google Vids (6 2D Cartoon Classic + 5 3D Cartoon Classic), rotated 1 per book.
CLASSIC_ROTATION: List[str] = [
    "Rio Classic",
    "Lennon Classic",
    "Alan Classic",
    "Sue Classic",
    "Diego Classic",
    "Aaron Classic",
    "Aden Classic",
    "Alexis Classic",
    "Kai Classic",
    "Kevin Classic",
    "Paul Classic",
]

# Canonical Classic avatar per book folder name (1 avatar per book across all languages; zero Amelia).
BOOK_AVATARS: Dict[str, str] = {
    # Books 1-14: Lily & Robo Adventures (STEM)
    "Long Running Agents": "Rio Classic",               # Book 1  -> actor17 (Aoede)
    "Year of the Horse": "Lennon Classic",              # Book 2  -> actor16 (Aoede, reference template)
    "Agent Sandbox": "Alan Classic",                    # Book 3  -> actor14 (Orus)
    "Agent Memory Bank": "Sue Classic",                 # Book 4  -> actor18 (Aoede)
    "Agent Vibe Coding": "Diego Classic",               # Book 5  -> actor15 (Orus)
    "Agent Skills": "Aaron Classic",                    # Book 6  -> actor13 (Orus)
    "Agent Computer Use": "Aden Classic",               # Book 7  -> actor19 (Aoede)
    "Agent Observability": "Alexis Classic",             # Book 8  -> actor20 (Aoede)
    "Agent Evaluation": "Kai Classic",                  # Book 9  -> actor21 (Orus)
    "Agent To Agent": "Kevin Classic",                  # Book 10 -> actor22 (Orus)
    "Agent MCP Connectors": "Paul Classic",             # Book 11 -> actor23 (Orus)
    "Agent Human In The Loop": "Rio Classic",           # Book 12 -> actor17 (Aoede)
    "Agent Grounding": "Lennon Classic",                # Book 13 -> actor16 (Aoede)
    "Agent Evolution": "Alan Classic",                  # Book 14 -> actor14 (Orus)
    # Books 15-26: Mei, Ming & Baobao (Chinese Heritage)
    "Iron Rod Into A Needle": "Sue Classic",            # Book 15 -> actor18 (Aoede)
    "Kong Rong Shares The Pears": "Diego Classic",      # Book 16 -> actor15 (Orus)
    "Sima Guang Smashes The Water Vat": "Aaron Classic",  # Book 17 -> actor13 (Orus)
    "The Frog In The Well": "Aden Classic",             # Book 18 -> actor19 (Aoede)
    "Painting The Dragons Eyes": "Alexis Classic",      # Book 19 -> actor20 (Aoede)
    "Waiting By The Tree Stump": "Kai Classic",         # Book 20 -> actor21 (Orus)
    "Pulling Up Sprouts": "Kevin Classic",              # Book 21 -> actor22 (Orus)
    "The Old Man Who Moved Mountains": "Paul Classic",  # Book 22 -> actor23 (Orus)
    "Adding Feet To A Snake": "Rio Classic",            # Book 23 -> actor17 (Aoede)
    "Touching The Elephant": "Lennon Classic",          # Book 24 -> actor16 (Aoede)
    "Mending The Sheepfold": "Alan Classic",            # Book 25 -> actor14 (Orus)
    "The Fox And The Tiger": "Sue Classic",             # Book 26 -> actor18 (Aoede)
}


def avatar_actor(avatar_name: str) -> str:
    key = re.sub(r"\s+", " ", (avatar_name or "").strip().lower())
    return AVATAR_ACTORS.get(key, "")


def avatar_matches(src: str, avatar_name: str) -> bool:
    actor = avatar_actor(avatar_name)
    if not actor:
        return False
    return actor in (src or "").lower()


def avatar_preview_url(avatar_name: str) -> str:
    actor = avatar_actor(avatar_name) or "actor16"
    return f"https://www.gstatic.com/docs/videos/videos/presetavatars/preview_{actor}.mp4"


def avatar_voice(avatar_name: str, default: str = "Aoede") -> str:
    return AVATAR_VOICES.get(avatar_actor(avatar_name), default)


def book_avatar(book_dir: str, override: str = "") -> str:
    if (override or "").strip():
        if not avatar_actor(override):
            raise ValueError(
                f"Avatar {override!r} is not a Google Vids Classic avatar (allowed cartoon rotation: {', '.join(CLASSIC_ROTATION)})."
            )
        return override.strip()
    folder = os.path.basename(os.path.normpath(book_dir))
    return BOOK_AVATARS.get(folder, CLASSIC_ROTATION[0])

# Main-world hooks (installed idempotently on every call):
# - __saTick: lets background tabs run their timers promptly (Chrome throttles background timers) and sweeps
#   <audio>/<video> elements created via attributes/constructors so background tabs decode preview/avatar media.
# - __saPendingFile: feeds a prepared File into the next <input type=file>.click() (slide uploads).
# - media src hook: decodes real durations of avatar speech/video (usercontent URLs) with
#   OfflineAudioContext, because background tabs defer <video> metadata loading.
BOOK_HOOKS_JS = r"""javascript:(() => {
    if (!window.__saTick) {
        try {
            Object.defineProperty(document, "hidden", {configurable: true, get: () => false});
            Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "visible"});
            document.hasFocus = () => true;
            window.addEventListener("visibilitychange", e => e.stopImmediatePropagation(), true);
        } catch (e) {}
        const origST = window.setTimeout.bind(window);
        const origCT = window.clearTimeout.bind(window);
        const origSI = window.setInterval.bind(window);
        const origCI = window.clearInterval.bind(window);
        const timers = new Map();
        let nextId = 900000;
        window.setTimeout = function(fn, delay, ...args) {
            const d = typeof delay === "number" ? delay : 0;
            const id = ++nextId;
            const rawId = origST(() => { timers.delete(id); if (typeof fn === "function") fn(...args); }, d);
            timers.set(id, {fn, args, due: Date.now() + d, rawId, interval: 0});
            return id;
        };
        window.clearTimeout = function(id) {
            const e = timers.get(id);
            if (e) { timers.delete(id); origCT(e.rawId); } else origCT(id);
        };
        window.setInterval = function(fn, delay, ...args) {
            const d = typeof delay === "number" ? Math.max(delay, 10) : 10;
            const id = ++nextId;
            const rawId = origSI(() => { const e = timers.get(id); if (e) e.due = Date.now() + d; if (typeof fn === "function") fn(...args); }, d);
            timers.set(id, {fn, args, due: Date.now() + d, rawId, interval: d});
            return id;
        };
        window.clearInterval = function(id) {
            const e = timers.get(id);
            if (e) { timers.delete(id); origCI(e.rawId); } else origCI(id);
        };
        window.__saTick = function(advanceMs) {
            const now = Date.now() + (advanceMs || 0);
            for (const [id, e] of Array.from(timers.entries())) {
                if (now >= e.due) {
                    if (e.interval) { e.due = Date.now() + e.interval; try { e.fn(...e.args); } catch (err) {} }
                    else { timers.delete(id); origCT(e.rawId); try { e.fn(...e.args); } catch (err) {} }
                }
            }
            document.querySelectorAll("audio, video").forEach(el => {
                const u = el.src || el.currentSrc || "";
                if ((u.startsWith("blob:") || u.includes("usercontent.google.com")) && !el.__saDecoded) {
                    el.__saDecoded = 1;
                    if (u.startsWith("blob:")) {
                        const dur = window.__saLastMediaDur || 10;
                        try { Object.defineProperty(el, "duration", {configurable: true, get: () => dur}); } catch (e) {}
                        try { Object.defineProperty(el, "readyState", {configurable: true, get: () => 4}); } catch (e) {}
                        if (el.tagName === "VIDEO") {
                            try { Object.defineProperty(el, "videoWidth", {configurable: true, get: () => 1280}); } catch (e) {}
                            try { Object.defineProperty(el, "videoHeight", {configurable: true, get: () => 720}); } catch (e) {}
                        }
                        ["loadedmetadata", "loadeddata", "canplay", "canplaythrough", "durationchange"].forEach(ev => {
                            el.dispatchEvent(new Event(ev));
                        });
                        return;
                    }
                    fetch(u, {credentials: "include"}).then(r => r.arrayBuffer()).then(buf => {
                        return new OfflineAudioContext(1, 1, 44100).decodeAudioData(buf);
                    }).then(ab => {
                        const dur = ab.duration || 10;
                        try { Object.defineProperty(el, "duration", {configurable: true, get: () => dur}); } catch (e) {}
                        try { Object.defineProperty(el, "readyState", {configurable: true, get: () => 4}); } catch (e) {}
                        if (el.tagName === "VIDEO") {
                            try { Object.defineProperty(el, "videoWidth", {configurable: true, get: () => 1280}); } catch (e) {}
                            try { Object.defineProperty(el, "videoHeight", {configurable: true, get: () => 720}); } catch (e) {}
                        }
                        ["loadedmetadata", "loadeddata", "canplay", "canplaythrough", "durationchange"].forEach(ev => {
                            el.dispatchEvent(new Event(ev));
                        });
                    }).catch(() => {});
                }
            });
        };
    }
    if (!window.__saFileHooked) {
        window.__saFileHooked = true;
        const origClick = HTMLInputElement.prototype.click;
        HTMLInputElement.prototype.click = function() {
            if (this.type === "file" && window.__saPendingFile) {
                const pf = window.__saPendingFile;
                window.__saPendingFile = null;
                const bin = atob(pf.b64);
                const arr = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
                const dt = new DataTransfer();
                dt.items.add(new File([arr], pf.name, {type: pf.mime}));
                this.files = dt.files;
                this.dispatchEvent(new Event("change", {bubbles: true}));
                this.dispatchEvent(new Event("input", {bubbles: true}));
                window.__saLastUploaded = pf.name;
                document.body.setAttribute("data-last-up", pf.name);
                return;
            }
            return origClick.apply(this, arguments);
        };
    }
    if (!window.__saMediaHooked && !window.__saZhHooked) {
        window.__saMediaHooked = true;
        const proto = HTMLMediaElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, "src");
        if (desc && desc.set) {
            const origSet = desc.set, origGet = desc.get;
            Object.defineProperty(proto, "src", {
                configurable: true, enumerable: true, get: origGet,
                set: function(val) {
                    const el = this;
                    const url = String(val || "");
                    origSet.call(el, val);
                    if (url.startsWith("blob:")) {
                        el.__saDecoded = 1;
                        const dur = window.__saLastMediaDur || 10.0;
                        try { Object.defineProperty(el, "duration", {configurable: true, get: () => dur}); } catch (e) {}
                        try { Object.defineProperty(el, "readyState", {configurable: true, get: () => 4}); } catch (e) {}
                        try { Object.defineProperty(el, "videoWidth", {configurable: true, get: () => 1280}); } catch (e) {}
                        try { Object.defineProperty(el, "videoHeight", {configurable: true, get: () => 720}); } catch (e) {}
                        setTimeout(() => {
                            ["loadedmetadata", "loadeddata", "canplay", "canplaythrough", "durationchange"].forEach(ev => {
                                el.dispatchEvent(new Event(ev));
                            });
                        }, 40);
                        return;
                    }
                    if (!url.includes("usercontent.google.com")) return;
                    el.__saDecoded = 1;
                    fetch(url, {credentials: "include"}).then(r => r.arrayBuffer()).then(buf => {
                        let width = 0, height = 0;
                        try {
                            const dv = new DataView(buf);
                            const walk = (offset, end) => {
                                while (offset + 8 <= end) {
                                    const size = dv.getUint32(offset);
                                    const type = String.fromCharCode(dv.getUint8(offset+4), dv.getUint8(offset+5), dv.getUint8(offset+6), dv.getUint8(offset+7));
                                    if (size <= 8) break;
                                    const next = offset + size;
                                    if (type === "moov" || type === "trak" || type === "mdia") walk(offset + 8, Math.min(next, end));
                                    else if (type === "tkhd") {
                                        const ver = dv.getUint8(offset + 8);
                                        const wOff = ver === 1 ? offset + 92 : offset + 84;
                                        if (wOff + 8 <= next) {
                                            const w = dv.getUint32(wOff) >>> 16, h = dv.getUint32(wOff + 4) >>> 16;
                                            if (w > 0 && h > 0) { width = w; height = h; }
                                        }
                                    }
                                    offset = next;
                                }
                            };
                            walk(0, buf.byteLength);
                        } catch (e) {}
                        return new OfflineAudioContext(1, 1, 44100).decodeAudioData(buf).then(ab => ({dur: ab.duration, width, height}));
                    }).then(({dur, width, height}) => {
                        Object.defineProperty(el, "duration", {configurable: true, get: () => dur});
                        try { Object.defineProperty(el, "readyState", {configurable: true, get: () => 4}); } catch (e) {}
                        if (width > 0 && height > 0) {
                            Object.defineProperty(el, "videoWidth", {configurable: true, get: () => width});
                            Object.defineProperty(el, "videoHeight", {configurable: true, get: () => height});
                        } else if (url.includes("contribution")) {
                            Object.defineProperty(el, "videoWidth", {configurable: true, get: () => 1280});
                            Object.defineProperty(el, "videoHeight", {configurable: true, get: () => 720});
                        }
                        ["loadedmetadata", "loadeddata", "canplay", "canplaythrough", "durationchange"].forEach(ev => {
                            el.dispatchEvent(new Event(ev));
                        });
                    }).catch(() => {});
                }
            });
        }
    }
    if (!window.__saUnloadHooked) {
        window.__saUnloadHooked = true;
        try { window.onbeforeunload = null; } catch (e) {}
        window.addEventListener("beforeunload", e => { e.stopImmediatePropagation(); }, true);
    }
    window.__saTick(10000);
})();void(0);""".replace("\n", " ")

# Long-running workers set this (seconds) so a Chrome restart pauses them instead of crashing them.
CHROME_WAIT_S = 0.0


def _main_chrome_pid() -> int:
    deadline = time.time() + CHROME_WAIT_S
    while True:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "^/Applications/Google Chrome.app/Contents/MacOS/Google Chrome$"]
            ).decode().split()
            return int(out[0])
        except subprocess.CalledProcessError:
            if time.time() >= deadline:
                raise
            time.sleep(5.0)


_EXEC_JXA = r"""
function run(argv) {
    const pid = parseInt(argv[0], 10);
    const docId = argv[1];
    const mainHook = argv[2];
    let mainExtra = argv[3];
    const isoJs = argv[4];
    if (mainExtra && mainExtra.indexOf("@FILE:") === 0) {
        ObjC.import("Foundation");
        const ns = $.NSString.stringWithContentsOfFileEncodingError(mainExtra.slice(6), $.NSUTF8StringEncoding, null);
        mainExtra = ns ? ns.js : "";
    }
    const app = Application(pid);
    const wins = app.windows();
    for (let wi = 0; wi < wins.length; wi++) {
        try {
            const wid = wins[wi].id();
            const w = app.windows.byId(wid);
            let actIdx = Number(w.activeTabIndex());
            const tabs = w.tabs();
            const tlen = tabs.length;
            let isAutoWin = false;
            if (tlen > 0 && (tabs[0].url() || "").indexOf("story-agent-automation") !== -1) {
                isAutoWin = true;
                if (actIdx !== 1) {
                    w.activeTabIndex = 1;
                    actIdx = 1;
                }
            }
            for (let ti = 0; ti < tlen; ti++) {
                if (ti + 1 === actIdx) continue;
                try {
                    const tid = tabs[ti].id();
                    const t = w.tabs.byId(tid);
                    const u = t.url();
                    if (u && u.indexOf(docId) !== -1) {
                        if (mainHook) t.url = mainHook;
                        if (mainExtra) t.url = mainExtra;
                        const res = app.execute(t, {javascript: isoJs});
                        if (isAutoWin && Number(w.activeTabIndex()) !== 1) {
                            try { w.activeTabIndex = 1; } catch (e2) {}
                        }
                        return res === undefined || res === null ? "" : String(res);
                    }
                } catch (e) {}
            }
        } catch (e) {}
    }
    return "TAB_NOT_FOUND";
}
"""


def exec_js(doc_id: str, iso_js: str, extra_main_js: str = "", hooks: bool = True) -> str:
    """Runs `iso_js` in the background tab showing `doc_id` (never activates Chrome)."""
    pid = str(_main_chrome_pid())
    clean_extra = (extra_main_js or "").replace("\n", " ")
    tmp_path = ""
    try:
        if len(clean_extra) > 200_000:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".js", delete=False) as tf:
                tf.write(clean_extra)
                tmp_path = tf.name
            clean_extra = f"@FILE:{tmp_path}"
        proc = subprocess.run(
            [
                "osascript",
                "-l",
                "JavaScript",
                "-e",
                _EXEC_JXA,
                pid,
                doc_id,
                BOOK_HOOKS_JS if hooks else "",
                clean_extra,
                iso_js,
            ],
            capture_output=True,
            text=True,
            timeout=25.0,
        )
        return (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return "TAB_NOT_FOUND"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------------------------
# Chrome tab management (background only)
# ---------------------------------------------------------------------------------------------

def list_tabs() -> List[Dict[str, Any]]:
    pid = _main_chrome_pid()
    jxa = f"""
    const app = Application({pid});
    const out = [];
    const wins = app.windows;
    const wlen = wins.length;
    for (let wi = 0; wi < wlen; wi++) {{
        const w = wins[wi];
        try {{
            const wid = Number(w.id());
            const widx = Number(w.index());
            let act = Number(w.activeTabIndex());
            const tlen = w.tabs.length;
            if (tlen > 0 && (w.tabs[0].url() || "").indexOf("story-agent-automation") !== -1 && act !== 1) {{
                try {{ w.activeTabIndex = 1; act = 1; }} catch (e2) {{}}
            }}
            for (let ti = 0; ti < tlen; ti++) {{
                const t = w.tabs[ti];
                let scriptable = false;
                try {{ app.execute(t, {{javascript: "1"}}); scriptable = true; }} catch (e) {{}}
                out.push({{win_id: wid, win_index: widx, active_index: act, tab_index: ti + 1, tab_id: Number(t.id()), url: t.url(), scriptable: scriptable}});
            }}
        }} catch (e) {{}}
    }}
    JSON.stringify(out);
    """
    proc = subprocess.run(["osascript", "-l", "JavaScript", "-e", jxa], capture_output=True, text=True)
    try:
        return json.loads((proc.stdout or "").strip() or "[]")
    except Exception:
        return []


def _vids_doc_id(url: str) -> Optional[str]:
    m = re.search(r"/videos/(?:u/\d+/)?d/([^/?#]+)", url)
    return m.group(1) if m else None


def open_in_background_tab(doc_url: str, avoid_doc_ids: List[str]) -> str:
    """Shows `doc_url` in a background tab in the scriptable Chrome window without changing what the user sees."""
    pid = _main_chrome_pid()
    doc_id = _vids_doc_id(doc_url) or ""
    tabs = [t for t in list_tabs() if t.get("scriptable")]
    auto_wins = {t["win_id"] for t in tabs if "story-agent-automation" in (t.get("url") or "")}
    if auto_wins:
        tabs = [t for t in tabs if t["win_id"] in auto_wins]
    if any(doc_id and doc_id in t["url"] and t["tab_index"] != t["active_index"] for t in tabs):
        return "ALREADY_OPEN"
    candidates = []
    for t in sorted(tabs, key=lambda x: (x["win_index"], x["tab_index"])):
        did = _vids_doc_id(t["url"])
        if not did:
            continue
        if did not in avoid_doc_ids and t["tab_index"] != t["active_index"]:
            candidates.append(t)
    if candidates:
        t = candidates[0]
        jxa = """
        function run(argv) {
            const app = Application(parseInt(argv[0], 10));
            const winId = parseInt(argv[1], 10);
            const tabIdx = parseInt(argv[2], 10) - 1;
            const url = argv[3];
            const wins = app.windows;
            for (let i = 0; i < wins.length; i++) {
                const w = wins[i];
                if (Number(w.id()) === winId) {
                    w.tabs[tabIdx].url = url;
                    return "OK";
                }
            }
        }
        """
        subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", jxa, str(pid), str(t["win_id"]), str(t["tab_index"]), doc_url],
            capture_output=True,
        )
        return f"REUSED win={t['win_id']} tab={t['tab_index']}"
    if not tabs:
        raise RuntimeError("No scriptable Chrome window available.")
    win_id = tabs[0]["win_id"]
    active_idx = tabs[0]["active_index"]
    jxa = """
    function run(argv) {
        const app = Application(parseInt(argv[0], 10));
        const winId = parseInt(argv[1], 10);
        const url = argv[2];
        const actIdx = parseInt(argv[3], 10);
        const wins = app.windows;
        for (let i = 0; i < wins.length; i++) {
            const w = wins[i];
            if (Number(w.id()) === winId) {
                w.tabs.push(app.Tab({url: url}));
                w.activeTabIndex = actIdx;
                return "OK";
            }
        }
    }
    """
    subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", jxa, str(pid), str(win_id), doc_url, str(active_idx)],
        capture_output=True,
    )
    return f"CREATED in win={win_id} (active tab restored to {active_idx})"


# ---------------------------------------------------------------------------------------------
# Scene-level operations
# ---------------------------------------------------------------------------------------------

_FIRE = "const fire = (el, x, y) => ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, clientX: x || 0, clientY: y || 0, button: 0})));"

_SIDEBAR_JS = """
    const __fireSb = (el) => {
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        const hit = (r.width > 0 && r.height > 0) ? document.elementFromPoint(x, y) : null;
        const tgt = (hit && el.contains(hit)) ? hit : el;
        try { tgt.focus(); } catch (e) {}
        tgt.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window}));
        tgt.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1, view: window}));
        tgt.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 0, view: window}));
        tgt.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window}));
    };
    let sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
    if (!sidebar || sidebar.offsetParent === null) {
        const avBtn = document.getElementById("content-library-rail-avatars-element") || document.querySelector("[aria-label='Generate an avatar']");
        if (avBtn) __fireSb(avBtn);
        sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
    }
"""


def scene_count(doc_id: str) -> int:
    js = """(() => {
        const thumbs = [...document.querySelectorAll("g.punch-filmstrip-thumbnail")].filter(e => e.getBoundingClientRect().height > 0);
        if (thumbs.length) return String(thumbs.length);
        const lbl = document.querySelector(".appsFlixScriptsSidebarSingleSceneNumber");
        const m = lbl ? lbl.innerText.match(/\\/\\s*(\\d+)/) : null;
        return m ? m[1] : "0";
    })()"""
    try:
        return int(exec_js(doc_id, js) or 0)
    except ValueError:
        return 0


def navigate_to_scene(doc_id: str, scene_num: int) -> str:
    js = f"""(() => {{
        {_SIDEBAR_JS}
        {_FIRE}
        if (!sidebar) return "NO_SIDEBAR";
        let lbl0 = sidebar.querySelector(".appsFlixScriptsSidebarSingleSceneNumber");
        if (!lbl0 || lbl0.offsetParent === null) {{
            const curTab = sidebar.querySelector(".docs-sidebar-toggle-tab-left-title");
            if (curTab) fire(curTab.parentElement);
        }}
        const prev = sidebar.querySelector("div[aria-label='Previous script']");
        const next = sidebar.querySelector("div[aria-label='Next script']");
        for (let i = 0; i < 30; i++) {{
            const lbl = sidebar.querySelector(".appsFlixScriptsSidebarSingleSceneNumber");
            const m = lbl ? lbl.innerText.trim().match(/Scene\\s+(\\d+)\\s*\\/\\s*(\\d+)/i) : null;
            if (!m) break;
            const cur = parseInt(m[1], 10);
            if (cur === {scene_num}) return lbl.innerText.trim();
            const btn = cur < {scene_num} ? next : prev;
            if (btn) fire(btn);
        }}
        const lbl = sidebar.querySelector(".appsFlixScriptsSidebarSingleSceneNumber");
        return lbl ? lbl.innerText.trim() : "NO_LBL";
    }})()"""
    res = exec_js(doc_id, js)
    time.sleep(0.3)
    return res


def scene_state(doc_id: str) -> Dict[str, Any]:
    """Avatar sidebar state for the current scene (buttons, out-of-sync flag, selected avatar preview, script preview)."""
    js = f"""(() => {{
        {_SIDEBAR_JS}
        if (!sidebar) return JSON.stringify({{state: "NO_SIDEBAR"}});
        const txt = sidebar.innerText || "";
        const lbl = sidebar.querySelector(".appsFlixScriptsSidebarSingleSceneNumber");
        const vid = sidebar.querySelector("video") || sidebar.querySelector("img.appsFlixPluginsVoiceoversSidebarSidebarVoicecardThumbnail");
        const visBtns = [...sidebar.querySelectorAll("div[role='button'], button")].filter(e => e.offsetParent !== null);
        const btns = visBtns.map(e => e.innerText.trim()).filter(Boolean);
        const upd = visBtns.find(b => /^(Update|Insert)$/i.test(b.innerText.trim()));
        // Quota banners: "You've hit your limits for generating avatars in Vids" (in-session) or
        // "You've reached your monthly limit. Wait until Nov 1 at 12 AM for limits to reset." (fresh load).
        const limited = /hit your limits? for generating avatars?|reached your monthly limit/i.test(txt);
        return JSON.stringify({{
            scene: lbl ? lbl.innerText.trim() : null,
            avatarSrc: vid ? (vid.src || "") : "",
            updateDisabled: Boolean(upd && (upd.disabled || upd.getAttribute("aria-disabled") === "true")),
            limited: limited,
            busy: !limited && /This might take a few minutes|Generating avatar/i.test(txt),
            outOfSync: /out of sync|Avatar outdated/i.test(txt),
            buttons: btns,
            pct: (txt.match(/(\\d+)%/) || [null, null])[1],
            text: txt.slice(0, 2000)
        }});
    }})()"""
    raw = exec_js(doc_id, js)
    try:
        return json.loads(raw)
    except Exception:
        return {"state": "ERR", "raw": raw[:200]}


def select_avatar(doc_id: str, avatar_name: str) -> str:
    """Selects `avatar_name` (e.g. 'Rio Classic', 'Alan Classic', 'Sue Classic') in the current scene's AI avatar picker."""
    if not (avatar_name or "").strip():
        return "NO_TARGET"
    target = re.sub(r"\s+", " ", avatar_name.strip().lower())
    actor = avatar_actor(avatar_name)
    st = scene_state(doc_id)
    if avatar_matches(st.get("avatarSrc", ""), avatar_name):
        return f"ALREADY_{actor}"
    open_res = exec_js(
        doc_id,
        f"""(() => {{
            {_SIDEBAR_JS}
            if (!sidebar) return "NO_SIDEBAR";
            const already = [...document.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogCustomDialogStyles")]
                .find(d => getComputedStyle(d).display !== "none" && d.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogAvatarContainer").length > 0);
            if (already) return "OPENED";
            const btn = sidebar.querySelector("[aria-label='Change the avatar']")
                || [...sidebar.querySelectorAll("div[role='button'], button")].find(b => b.innerText.trim() === "Change");
            if (!btn || btn.offsetParent === null) return "NO_CHANGE_BTN";
            btn.scrollIntoView({{block: "center"}});
            __fireSb(btn);
            return "OPENED";
        }})()""",
    )
    if open_res != "OPENED":
        return open_res
    pick_res = "NO_CARD"
    for _ in range(5):
        time.sleep(0.5)
        pick_res = exec_js(
            doc_id,
            f"""(() => {{
                const fireCard = (el) => {{
                    el.scrollIntoView({{block: "center"}});
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2, y = r.top + r.height / 2;
                    try {{ el.focus(); }} catch (e) {{}}
                    el.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true, cancelable: true, clientX: x, clientY: y, view: window}}));
                    el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1, view: window}}));
                    el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 0, view: window}}));
                    el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window}}));
                }};
                const want = {json.dumps(target)};
                const actor = {json.dumps(actor)};
                const dlg = [...document.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogCustomDialogStyles")]
                    .find(d => getComputedStyle(d).display !== "none" && d.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogAvatarContainer").length > 0);
                if (!dlg) return "WAIT_DIALOG";
                const cards = [...dlg.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogAvatarContainer")];
                if (!cards.length) return "WAIT_DIALOG";
                const card = cards.find(c => {{
                    if (actor && c.id === actor) return true;
                    const t = (c.textContent || "").trim().replace(/\\s+/g, " ").toLowerCase();
                    const media = ((c.querySelector("img") || {{}}).src || (c.querySelector("video") || {{}}).src || "").toLowerCase();
                    return t === want || t === want + " classic" || (actor && media.includes(actor));
                }});
                if (!card) {{
                    const closeBtn = dlg.querySelector(".appsFlixPluginsVoiceoversAvatarsSelectionDialogDialogCloseButton, .docs-material-gm-dialog-cancel-button");
                    if (closeBtn) fireCard(closeBtn);
                    return "NO_CARD";
                }}
                fireCard(card);
                return "CARD_CLICKED";
            }})()""",
        )
        if pick_res != "WAIT_DIALOG":
            break
    if pick_res != "CARD_CLICKED":
        return pick_res
    time.sleep(0.4)
    sel_res = exec_js(
        doc_id,
        """(() => {
            const dlg = [...document.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogCustomDialogStyles")]
                .find(d => getComputedStyle(d).display !== "none" && d.querySelectorAll(".appsFlixPluginsVoiceoversAvatarsSelectionDialogAvatarContainer").length > 0) || document;
            const sel = dlg.querySelector(".docs-material-gm-dialog-call-to-action-button");
            if (!sel) return "NO_SELECT_BTN";
            const r = sel.getBoundingClientRect();
            const x = r.left + r.width / 2, y = r.top + r.height / 2;
            try { sel.focus(); } catch (e) {}
            sel.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window}));
            sel.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1, view: window}));
            sel.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 0, view: window}));
            sel.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window}));
            return "SELECTED";
        })()""",
    )
    time.sleep(0.6)
    after = scene_state(doc_id)
    if avatar_matches(after.get("avatarSrc", ""), avatar_name):
        return f"SELECTED_{actor or target}"
    return sel_res


def delete_current_scene(doc_id: str) -> str:
    js = f"""(() => {{
        {_FIRE}
        const sceneMenu = [...document.querySelectorAll("[role=menubar] [role=menuitem]")].find(e => e.innerText.trim() === "Scene");
        if (!sceneMenu) return "NO_SCENE_MENU";
        fire(sceneMenu);
        const delItem = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => e.offsetParent !== null && e.innerText.includes("Delete scene"));
        if (!delItem) return "NO_DELETE_ITEM";
        const r = delItem.getBoundingClientRect();
        fire(delItem, r.left + 10, r.top + 5);
        return "DELETED";
    }})()"""
    return exec_js(doc_id, js)


def update_scene_script(doc_id: str, script_text: str) -> str:
    """Replaces the current scene's script text in the AI avatar sidebar (same method as the ZH flow)."""
    clean_text = script_text.replace("\n", " ").strip()
    exec_js(doc_id, """(() => {
        const sidebar = document.querySelector("div[aria-label='AI avatar'][role='complementary']");
        if (!sidebar) return "NO_SIDEBAR";
        const editBtn = sidebar.querySelector("button.appsFlixScriptsSidebarKeyboardEditButton");
        if (editBtn) editBtn.click();
        return "OK";
    })()""")
    time.sleep(0.2)
    js = f"""(() => {{
        const iframe = document.querySelector("iframe.docs-texteventtarget-iframe");
        if (!iframe || !iframe.contentDocument) return "NO_IFRAME";
        const target = iframe.contentDocument.querySelector("[contenteditable='true']") || iframe.contentDocument.body;
        target.focus();
        const key = (k, code, kc, shift) => target.dispatchEvent(new KeyboardEvent("keydown", {{key: k, code, keyCode: kc, which: kc, shiftKey: !!shift, bubbles: true, cancelable: true}}));
        for (let i = 0; i < 40; i++) key("ArrowUp", "ArrowUp", 38);
        key("Home", "Home", 36);
        for (let i = 0; i < 40; i++) {{
            key("ArrowDown", "ArrowDown", 40, true);
            target.dispatchEvent(new KeyboardEvent("keyup", {{key: "ArrowDown", code: "ArrowDown", keyCode: 40, which: 40, shiftKey: true, bubbles: true}}));
        }}
        key("End", "End", 35, true);
        key("Backspace", "Backspace", 8);
        const dt = new DataTransfer();
        dt.setData("text/plain", {json.dumps(clean_text)});
        target.dispatchEvent(new ClipboardEvent("paste", {{clipboardData: dt, bubbles: true, cancelable: true}}));
        return "PASTED";
    }})()"""
    res = exec_js(doc_id, js)
    time.sleep(0.3)
    return res


def replace_scene_slide(doc_id: str, slide_jpg_path: str, upload_name: str) -> str:
    """Replaces the current scene's widest image element (the book slide) via Replace image -> Upload."""
    with open(slide_jpg_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    pending = (
        "javascript:(() => {window.__saPendingFile = {b64: " + json.dumps(b64) + ", name: "
        + json.dumps(upload_name) + ', mime: "image/jpeg"}; document.body.setAttribute("data-last-up", "");})();void(0);'
    )
    js = f"""(() => {{
        {_FIRE}
        const more = document.getElementById("moreButton");
        if (more && more.offsetParent !== null && more.getAttribute("aria-pressed") !== "true") {{
            const mr = more.getBoundingClientRect();
            fire(more, mr.left + mr.width / 2, mr.top + mr.height / 2);
        }}
        const leaves = [...document.querySelectorAll("svg g[id^='editor-']")].filter(g => {{
            const r = g.getBoundingClientRect();
            return r.width > 20 && r.height > 20 && !g.id.endsWith("-bg") && g.querySelectorAll("g[id^='editor-']").length === 0;
        }}).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
        if (!leaves.length) return "NO_SLIDE_G";
        const sr = leaves[0].getBoundingClientRect();
        const target = document.elementFromPoint(sr.left + 60, sr.top + 60) || leaves[0];
        fire(target, sr.left + 60, sr.top + 60);
        const repBtn = [...document.querySelectorAll("#replaceImageMenu, [role=toolbar] [aria-label='Replace image'], [data-tooltip='Replace image']")].find(e => e.offsetParent !== null);
        if (!repBtn) return "NO_REPLACE_IMAGE_BTN";
        fire(repBtn);
        const upItem = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => e.offsetParent !== null && e.innerText.includes("Upload from computer"));
        if (!upItem) return "NO_UPLOAD_ITEM";
        const ur = upItem.getBoundingClientRect();
        fire(upItem, ur.left + 10, ur.top + 10);
        return document.body.getAttribute("data-last-up") || "CLICKED";
    }})()"""
    res = exec_js(doc_id, js, extra_main_js=pending)
    for _ in range(2):
        time.sleep(0.15)
        exec_js(doc_id, "1")
    return res


MAX_UPLOAD_MP4_BYTES = 650_000


def build_timed_avatar_mp4(base_mp4_path: str, wav_path: str, out_mp4_path: str) -> float:
    """Loops `base_mp4_path` at 1.0x speed to match `wav_path` exact duration and muxes the audio track."""
    with wave.open(wav_path, "rb") as wf:
        dur = wf.getnframes() / float(wf.getframerate())

    if (
        os.path.exists(out_mp4_path)
        and os.path.getsize(out_mp4_path) > 10000
        and os.path.getmtime(out_mp4_path) >= os.path.getmtime(wav_path)
        and os.path.getmtime(out_mp4_path) >= os.path.getmtime(base_mp4_path)
    ):
        return dur

    tiers = [("28", "128k"), ("34", "64k"), ("38", "48k")] if dur <= 18.0 else [("34", "64k"), ("38", "48k")]
    for crf, ba in tiers:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                base_mp4_path,
                "-i",
                wav_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                crf,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                ba,
                "-movflags",
                "+faststart",
                "-t",
                f"{dur:.3f}",
                out_mp4_path,
            ],
            check=True,
            capture_output=True,
        )
        if os.path.getsize(out_mp4_path) <= MAX_UPLOAD_MP4_BYTES:
            break
    return dur


def replace_scene_avatar_video(
    doc_id: str, mp4_path: str, dur: float, scene_num: int, upload_name: Optional[str] = None
) -> str:
    """Replaces the current scene's avatar video clip with an exact-timed MP4 via Replace video -> Upload."""
    with open(mp4_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    fname = upload_name or f"scene_{scene_num:02d}_zh_avatar.mp4"
    pending = (
        f"javascript:(() => {{window.__saLastMediaDur = {dur:.3f}; "
        f"window.__saPendingFile = {{b64: {json.dumps(b64)}, name: {json.dumps(fname)}, mime: \"video/mp4\"}}; "
        f"document.body.setAttribute(\"data-last-up\", \"\");}})();void(0);"
    )
    js = f"""(() => {{
        {_FIRE}
        const leaves = [...document.querySelectorAll("svg g[id^='editor-']")].filter(g => {{
            const r = g.getBoundingClientRect();
            return r.width > 20 && r.height > 20 && !g.id.endsWith("-bg") && g.querySelectorAll("g[id^='editor-']").length === 0;
        }}).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
        if (leaves.length < 2) return "NO_AVATAR_G";
        const avatarG = leaves[1];
        const ar = avatarG.getBoundingClientRect();
        const aTarget = document.elementFromPoint(ar.left + ar.width/2, ar.top + ar.height/2) || avatarG;
        fire(aTarget, ar.left + ar.width/2, ar.top + ar.height/2);
        const more = document.getElementById("moreButton");
        if (more && more.offsetParent !== null && more.getAttribute("aria-pressed") !== "true") {{
            const mr = more.getBoundingClientRect();
            fire(more, mr.left + mr.width / 2, mr.top + mr.height / 2);
        }}
        const repBtn = [...document.querySelectorAll("#replaceVideoMenu, [role=toolbar] [aria-label='Replace video'], [data-tooltip='Replace video']")].find(e => e.offsetParent !== null);
        if (!repBtn) return "NO_REP_VID_BTN";
        fire(repBtn);
        const upItem = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => e.offsetParent !== null && e.innerText.trim() === "Upload");
        if (!upItem) return "NO_UP_ITEM";
        const ur = upItem.getBoundingClientRect();
        fire(upItem, ur.left + 10, ur.top + 10);
        return document.body.getAttribute("data-last-up") || "CLICKED";
    }})()"""
    res = exec_js(doc_id, js, extra_main_js=pending)
    for _ in range(3):
        time.sleep(0.2)
        exec_js(doc_id, "1")
    return res


def insert_scene_avatar_video(
    doc_id: str, mp4_path: str, dur: float, scene_num: int, upload_name: Optional[str] = None
) -> str:
    """Inserts an exact-timed avatar MP4 onto a scene that has no avatar element via Insert -> Upload."""
    with open(mp4_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    fname = upload_name or f"scene_{scene_num:02d}_zh_avatar.mp4"
    pending = (
        f"javascript:(() => {{window.__saLastMediaDur = {dur:.3f}; "
        f"window.__saPendingFile = {{b64: {json.dumps(b64)}, name: {json.dumps(fname)}, mime: \"video/mp4\"}}; "
        f"document.body.setAttribute(\"data-last-up\", \"\");}})();void(0);"
    )
    open_js = f"""(() => {{
        {_FIRE}
        const visUp = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => e.offsetParent !== null && e.innerText.trim() === "Upload");
        if (visUp) return "ALREADY_OPEN";
        const insMenu = [...document.querySelectorAll("[role=menubar] [role=menuitem]")].find(e => e.innerText.trim() === "Insert");
        if (!insMenu) return "NO_INSERT_MENU";
        const mr = insMenu.getBoundingClientRect();
        fire(insMenu, mr.left + 10, mr.top + 10);
        return "OPENED";
    }})()"""
    exec_js(doc_id, open_js, extra_main_js=pending)
    time.sleep(0.3)
    click_js = f"""(() => {{
        {_FIRE}
        const upItem = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => e.offsetParent !== null && e.innerText.trim() === "Upload");
        if (!upItem) return "NO_UPLOAD_ITEM";
        const ur = upItem.getBoundingClientRect();
        fire(upItem, ur.left + 15, ur.top + 10);
        return document.body.getAttribute("data-last-up") || "CLICKED";
    }})()"""
    res = exec_js(doc_id, click_js)
    for _ in range(4):
        time.sleep(0.25)
        exec_js(doc_id, "1")
    return res


def extract_stage_slides(book_dir: str, lang: str) -> List[str]:
    """Extracts `page_XX_stage.jpg` frames from the book's `<title>_Google_Vids_<LANG>.pptx`."""
    with open(os.path.join(book_dir, "story.json"), encoding="utf-8") as sf:
        story = json.load(sf)
    safe_title = re.sub(r"[^A-Za-z0-9 _-]+", "", story.get("title", "")).strip()
    pptx = os.path.join(book_dir, "google_vids", f"{safe_title}_Google_Vids_{lang.upper()}.pptx")
    out_dir = os.path.join(book_dir, "google_vids", f"{lang}_slides")
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with zipfile.ZipFile(pptx, "r") as zf:
        for i in range(1, len(story["pages"]) + 1):
            name = f"page_{i:02d}_stage.jpg"
            out = os.path.join(out_dir, name)
            with open(out, "wb") as jf:
                jf.write(zf.read(f"ppt/media/{name}"))
            paths.append(out)
    return paths


def load_scripts(book_dir: str, lang: str) -> List[str]:
    with open(os.path.join(book_dir, "story.json"), encoding="utf-8") as sf:
        story = json.load(sf)
    pages = sorted(story["pages"], key=lambda p: int(p["page_number"]))
    return [vids_exporter.extract_page_voiceover(story, p, len(pages), lang=lang) for p in pages]


def load_projects(book_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(book_dir, "google_vids", "vids_projects.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as pf:
        return json.load(pf)


def save_projects(book_dir: str, projects: Dict[str, Dict[str, str]]) -> None:
    path = os.path.join(book_dir, "google_vids", "vids_projects.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
