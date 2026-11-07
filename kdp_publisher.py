"""Amazon KDP browser automation (Kindle eBook publishing).

Amazon KDP has no public publishing API, so this module drives the user's
logged-in KDP session inside a dedicated Google Chrome window launched with
``--remote-debugging-port=9223 --user-data-dir=~/.story-agent-chrome-kdp``
(same raw Chrome DevTools Protocol pattern as ``vids_avatar_automator.py``).

Commands
--------
    python kdp_publisher.py launch                 # open the KDP Chrome window (login once)
    python kdp_publisher.py status                 # login check + screenshot + bookshelf text
    python kdp_publisher.py shot [--name x]        # screenshot of the current KDP tab
    python kdp_publisher.py eval "<js>"            # evaluate JS in the KDP tab (debugging)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("kdp_publisher")

CDP_PORT = int(os.environ.get("KDP_CDP_PORT", "9223"))
CDP_HTTP = f"http://127.0.0.1:{CDP_PORT}"
PROFILE_DIR = Path(os.path.expanduser("~/.story-agent-chrome-kdp"))
BOOKSHELF_URL = "https://kdp.amazon.com/en_US/bookshelf"
REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "output"
KDP_DIR = REPO_ROOT / "kdp"
SHOTS_DIR = KDP_DIR / "screenshots"


# --------------------------------------------------------------------------- #
# Chrome launch
# --------------------------------------------------------------------------- #
def launch_chrome(url: str = BOOKSHELF_URL) -> None:
    """Launch a dedicated Chrome with remote debugging (user logs into KDP once).

    Background throttling/occlusion features are disabled so the automation keeps
    working when the window is covered by other windows.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-features=IntensiveWakeUpThrottling",
        "--window-size=1400,1000",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
    logger.info("Launched KDP Chrome on CDP port %s (profile %s)", CDP_PORT, PROFILE_DIR)


# --------------------------------------------------------------------------- #
# Minimal synchronous CDP client (page-level websocket)
# --------------------------------------------------------------------------- #
class _SendFailed(Exception):
    """A CDP command could not be sent because the socket was already closed (so it is safe to resend)."""


class CDPPage:
    """Synchronous Chrome DevTools Protocol client bound to one page target."""

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._id = 0
        self.events: list[dict[str, Any]] = []
        self._connect()

    def _connect(self) -> None:
        from websockets.sync.client import connect

        # No websocket keepalive pings (CDP clients like Puppeteer don't use them): Chrome missed a pong while
        # the KDP tab sat behind an IP Protect prompt, and the client then killed the connection mid-run.
        self.ws = connect(self.ws_url, max_size=64 * 1024 * 1024, open_timeout=20, ping_interval=None)
        self._call_once("Page.enable")
        self._call_once("Runtime.enable")
        self._call_once("DOM.enable")
        try:
            self._call_once("Emulation.setFocusEmulationEnabled", enabled=True)
        except Exception:  # noqa: BLE001
            pass

    # -- core --------------------------------------------------------------- #
    def call(self, method: str, timeout: float = 60.0, **params: Any) -> dict[str, Any]:
        """Send a CDP command; if the socket was already closed (idle drop), reconnect to the same tab and resend.

        Only a failed *send* is retried (the command never reached Chrome), so clicks are never doubled.
        """
        from websockets.exceptions import ConnectionClosed

        try:
            return self._call_once(method, timeout, **params)
        except _SendFailed:
            logger.warning("CDP socket was closed before %s; reconnecting to the same tab", method)
            self._connect()
            return self._call_once(method, timeout, **params)
        except ConnectionClosed as exc:
            raise RuntimeError(f"CDP connection closed while waiting for {method}: {exc}") from exc

    def _call_once(self, method: str, timeout: float = 60.0, **params: Any) -> dict[str, Any]:
        from websockets.exceptions import ConnectionClosed

        self._id += 1
        msg_id = self._id
        try:
            self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        except ConnectionClosed as exc:
            raise _SendFailed(str(exc)) from exc
        deadline = time.time() + timeout
        while True:
            remaining = max(0.1, deadline - time.time())
            raw = self.ws.recv(timeout=remaining)
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                if len(self.events) > 500:
                    self.events = self.events[-200:]

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    # -- convenience -------------------------------------------------------- #
    def eval(self, expression: str, await_promise: bool = True) -> Any:
        res = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=await_promise,
            returnByValue=True,
            userGesture=True,
        )
        if "exceptionDetails" in res:
            exc = res["exceptionDetails"]
            text = exc.get("exception", {}).get("description") or exc.get("text")
            raise RuntimeError(f"JS error: {text}")
        return res.get("result", {}).get("value")

    @property
    def url(self) -> str:
        return self.eval("location.href", await_promise=False)

    def navigate(self, url: str, settle: float = 2.0, timeout: float = 60.0) -> None:
        self.call("Page.navigate", url=url)
        self.wait_ready(timeout=timeout)
        time.sleep(settle)

    def wait_ready(self, timeout: float = 60.0) -> None:
        """Wait for readyState=complete (or 'interactive' for 8s+); KDP pages often never finish loading."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                state = self.eval("document.readyState", await_promise=False)
                if state == "complete" or (state == "interactive" and time.time() - start > 8):
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        logger.warning("Page did not reach readyState=complete within %ss; continuing", timeout)

    def wait_for(self, js_condition: str, timeout: float = 30.0, poll: float = 0.4) -> Any:
        """Poll a JS expression until it is truthy; returns its value."""
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                val = self.eval(f"(() => {{ return ({js_condition}); }})()", await_promise=False)
                if val:
                    return val
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            time.sleep(poll)
        raise TimeoutError(f"Condition not met in {timeout}s: {js_condition[:120]} ({last_err})")

    def screenshot(self, name: str = "shot") -> Path:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)["data"]
        path = SHOTS_DIR / f"{int(time.time())}_{name}.png"
        path.write_bytes(base64.b64decode(data))
        logger.info("Screenshot: %s", path)
        return path

    def body_text(self) -> str:
        return self.eval("document.body ? document.body.innerText : ''", await_promise=False) or ""

    # -- input helpers ------------------------------------------------------- #
    def _rect(self, selector_js: str) -> dict[str, float]:
        rect = self.eval(
            f"""(() => {{
                const el = {selector_js};
                if (!el) return null;
                el.scrollIntoView({{block: 'center', inline: 'center'}});
                const r = el.getBoundingClientRect();
                return {{x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height}};
            }})()""",
            await_promise=False,
        )
        if not rect:
            raise RuntimeError(f"Element not found: {selector_js[:120]}")
        return rect

    def click(self, selector_js: str, settle: float = 0.6) -> None:
        """Click an element given a JS expression. Uses a real mouse click when the page is visible,
        otherwise (hidden/occluded window) falls back to element.click() + focus()."""
        r = self._rect(selector_js)
        visible = self.eval("document.visibilityState === 'visible'", await_promise=False)
        if visible:
            time.sleep(0.15)
            r = self._rect(selector_js)  # re-measure after scroll
            for ev, extra in (("mouseMoved", {}), ("mousePressed", {"button": "left", "clickCount": 1}),
                              ("mouseReleased", {"button": "left", "clickCount": 1})):
                self.call("Input.dispatchMouseEvent", type=ev, x=r["x"], y=r["y"], **extra)
        else:
            self.eval(
                f"(() => {{ const el = {selector_js}; if (!el) return false; if (el.focus) el.focus(); el.click(); return true; }})()",
                await_promise=False,
            )
        time.sleep(settle)

    def set_value(self, selector_js: str, text: str) -> str:
        """Set an <input>/<textarea> value through the native setter and fire input/change events."""
        return self.eval(
            f"""(() => {{
                const el = {selector_js};
                if (!el) return 'NOT_FOUND';
                const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                if (el.focus) el.focus();
                setter.call(el, {json.dumps(text)});
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true}}));
                if (el.blur) el.blur();
                return 'OK:' + el.value;
            }})()""",
            await_promise=False,
        )

    def type_into(self, selector_js: str, text: str, clear: bool = True, settle: float = 0.3) -> None:
        """Enter text into an input. Prefers real keyboard input (visible page); verifies the value and
        falls back to native-setter assignment so it also works while the window is hidden."""
        visible = self.eval("document.visibilityState === 'visible'", await_promise=False)
        if visible:
            self.click(selector_js, settle=0.2)
            focused = self.eval(f"document.activeElement === ({selector_js})", await_promise=False)
            if focused:
                if clear:
                    self.call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=4, commands=["selectAll"])
                    self.call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=4)
                    self.call("Input.dispatchKeyEvent", type="keyDown", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
                    self.call("Input.dispatchKeyEvent", type="keyUp", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
                if text:
                    self.call("Input.insertText", text=text)
                time.sleep(settle)
        current = self.eval(f"(({selector_js})||{{}}).value", await_promise=False)
        if current != text:
            self.set_value(selector_js, text)
            time.sleep(settle)

    def set_files(self, selector: str, paths: list[str]) -> None:
        doc = self.call("DOM.getDocument", depth=0)
        node = self.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)
        if not node.get("nodeId"):
            raise RuntimeError(f"File input not found: {selector}")
        self.call("DOM.setFileInputFiles", files=[str(Path(p).resolve()) for p in paths], nodeId=node["nodeId"])

    def set_native_select(self, selector_js: str, value: str) -> str:
        """Set a <select> value through React's native setter and fire change/input events."""
        return self.eval(
            f"""(() => {{
                const el = {selector_js};
                if (!el) return 'NOT_FOUND';
                const opt = Array.from(el.options).find(o => o.value === {json.dumps(value)} || o.text.trim() === {json.dumps(value)});
                if (!opt) return 'NO_OPTION:' + Array.from(el.options).map(o => o.value + '|' + o.text).join(',');
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
                setter.call(el, opt.value);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'OK:' + el.value;
            }})()""",
            await_promise=False,
        )

    def set_radio(self, selector_js: str) -> None:
        """Click a radio/checkbox input (real mouse click on the element or its label)."""
        self.click(
            f"""(() => {{ const el = {selector_js}; if (!el) return null;
                 const r = el.getBoundingClientRect();
                 if (r.width > 2 && r.height > 2) return el;
                 const lab = el.id ? document.querySelector('label[for="' + el.id + '"]') : el.closest('label');
                 return lab || el; }})()"""
        )

    def set_ckeditor(self, html: str) -> str:
        """Set the KDP description (CKEditor 4) content via its API."""
        return self.eval(
            f"""(() => {{
                if (!window.CKEDITOR) return 'NO_CKEDITOR';
                const names = Object.keys(CKEDITOR.instances);
                if (!names.length) return 'NO_INSTANCES';
                const ed = CKEDITOR.instances[names[0]];
                ed.setData({json.dumps(html)});
                ed.updateElement();
                ed.fire('change');
                return 'OK:' + ed.getData().length;
            }})()""",
            await_promise=False,
        )

    def wait_saving(self, timeout: float = 60.0) -> None:
        """Wait for KDP's 'Saving...' overlay to disappear."""
        time.sleep(0.8)
        self.wait_for(
            "!Array.from(document.querySelectorAll('[role=dialog], .a-popover, .a-modal')).some(d => d.offsetParent && /Saving/.test(d.innerText))",
            timeout=timeout,
        )
        time.sleep(0.5)

    def dialog_text(self) -> str:
        return self.eval(
            """(() => { const vis = el => el.getBoundingClientRect().width > 0;
                 const ds = Array.from(document.querySelectorAll('[role=dialog], .a-popover, .a-modal, [aria-modal=true]')).filter(vis);
                 return ds.length ? ds[ds.length - 1].innerText : ''; })()""",
            await_promise=False,
        ) or ""

    def page_errors(self) -> list[str]:
        return self.eval(
            """Array.from(document.querySelectorAll('.a-alert-error, .a-box-error, [class*="error"]'))
                 .filter(e => e.getBoundingClientRect().width > 0 && (e.innerText||'').trim())
                 .map(e => e.innerText.trim().slice(0, 160)).slice(0, 20)""",
            await_promise=False,
        ) or []


def list_targets() -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=5) as resp:
        return json.loads(resp.read().decode())


def connect_kdp_tab() -> CDPPage:
    targets = [t for t in list_targets() if t.get("type") == "page"]
    kdp = [t for t in targets if "amazon.com" in (t.get("url") or "")]
    target = (kdp or targets)[0]
    logger.info("Attaching to tab: %s (%s)", target.get("title"), target.get("url"))
    return CDPPage(target["webSocketDebuggerUrl"])


# --------------------------------------------------------------------------- #
# Publishing steps
# --------------------------------------------------------------------------- #
KDP_BASE = "https://kdp.amazon.com/en_US"
MODAL_JS = ("Array.from(document.querySelectorAll('[role=dialog], .a-popover, .a-modal'))"
            ".filter(d => d.getBoundingClientRect().width > 0).pop()")


def _btn_js(text: str, root: str = "document", starts_with: bool = False) -> str:
    """JS expression returning the first visible button-like element with the given text."""
    cmp = "t.startsWith(T)" if starts_with else "t === T"
    return (
        f"(() => {{ const T = {json.dumps(text)}; const R = {root}; if (!R) return null;"
        f" return Array.from(R.querySelectorAll('button, [role=button], input[type=submit], a.a-button-text, .a-button-text, a'))"
        f" .filter(el => el.getBoundingClientRect().width > 0)"
        f" .find(el => {{ const t = (el.innerText || el.value || '').trim(); return {cmp}; }}) || null; }})()"
    )


def _title_id(url: str) -> Optional[str]:
    m = re.search(r"/title-setup/kindle/([A-Z0-9]{8,})/", url)
    return m.group(1) if m else None


def _save_state(listing: Any, **updates: Any) -> None:
    from kdp_listings import save_listing

    for k, v in updates.items():
        setattr(listing, k, v)
    save_listing(listing)


SERIES_SAVED_RE = r"Your changes have been saved|series updates have been submitted"


def _dismiss_series_saved_dialog(page: CDPPage, timeout: float = 20.0) -> bool:
    """Click 'Done' on KDP's post-series 'Your changes have been saved' dialog.

    The dialog overlays the whole page, so while it is open every other click (e.g. 'Choose categories')
    lands on the overlay instead. Returns True if a dialog was dismissed.
    """
    done = _btn_js("Done", MODAL_JS)
    still_open = f"({MODAL_JS}) && new RegExp({json.dumps(SERIES_SAVED_RE)}).test(({MODAL_JS}).innerText)"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.eval(f"!!({still_open})", await_promise=False) and page.eval(f"!!({done})", await_promise=False):
            for attempt in range(3):
                if attempt == 0:
                    page.click(done, settle=1.5)
                else:
                    page.eval(f"(() => {{ const b = {done}; if (b) b.click(); }})()", await_promise=False)
                    time.sleep(1.5)
                if not page.eval(f"!!({still_open})", await_promise=False):
                    logger.info("Dismissed KDP's 'series updates have been submitted' dialog")
                    return True
            page.screenshot("series_saved_dialog_stuck")
            raise RuntimeError("Could not dismiss the 'Your changes have been saved' series dialog")
        time.sleep(1.0)
    return False


def _modal_step(page: CDPPage, button_js: str, expect_regex: str, attempts: int = 5, wait: float = 8.0) -> None:
    """Click a button inside the active modal, retrying until the modal text matches expect_regex."""
    expect = f"({MODAL_JS}) && new RegExp({json.dumps(expect_regex)}).test(({MODAL_JS}).innerText)"
    for i in range(attempts):
        if page.eval(f"!!({button_js})", await_promise=False):
            page.click(button_js, settle=1.5)
        try:
            page.wait_for(expect, timeout=wait)
            return
        except TimeoutError:
            logger.warning("Modal did not advance to /%s/ (attempt %d)", expect_regex, i + 1)
    page.screenshot("modal_step_failed")
    raise RuntimeError(f"Series dialog did not advance to /{expect_regex}/; modal text: {page.dialog_text()[:200]!r}")


def _series_modal(page: CDPPage, listing: Any) -> None:
    """Handle the 'Add title to a series' modal: pick the existing series (or create it)."""
    page.wait_for(f"({MODAL_JS}) && /Add title to a series/.test(({MODAL_JS}).innerText)", timeout=40)
    time.sleep(1.5)  # modal slide-in animation
    _modal_step(page, _btn_js("Select series", MODAL_JS), "Select a series")
    time.sleep(1.0)
    wanted = listing.series_title.strip().lower()
    series_btn = (f"Array.from(({MODAL_JS}).querySelectorAll('button'))"
                  f".find(b => (b.innerText||'').trim().toLowerCase().startsWith({json.dumps(wanted)}))")
    found = page.eval(f"(() => {{ const b = {series_btn}; return b ? b.innerText.trim() : null; }})()", await_promise=False)
    if not found:
        logger.info("Series %r not found; creating it", listing.series_title)
        _modal_step(page, _btn_js("Back", MODAL_JS), "Add title to a series")
        time.sleep(1.0)
        page.click(_btn_js("Create series", MODAL_JS), settle=3.0)
        _create_series_modal(page, listing)
        return
    logger.info("Selecting existing series: %r", found)
    _modal_step(page, series_btn, "How is this title related")
    _modal_step(page, _btn_js("Main content", MODAL_JS), "Confirm or edit")
    confirm_text = page.dialog_text()
    m = re.search(r"is book #(\d+) in the series", confirm_text)
    if m and int(m.group(1)) != int(listing.series_number):
        listing.notes.append(f"KDP assigned series book #{m.group(1)} (planned #{listing.series_number})")
        logger.warning("Series number mismatch: KDP says #%s, planned #%s", m.group(1), listing.series_number)
    page.click(_btn_js("Confirm and continue", MODAL_JS), settle=3.0)
    page.wait_saving(timeout=90)
    _dismiss_series_saved_dialog(page)
    page.wait_for("/Book number/.test(document.body.innerText)", timeout=40)
    logger.info("Series assigned: %s", re.sub(r"\s+", " ", page.body_text()[page.body_text().find("Series Title"):][:80]))


def _create_series_modal(page: CDPPage, listing: Any) -> None:
    """Fill the 'Create series' flow. Explored live; raises with a dump if the layout is unexpected."""
    page.wait_for(f"({MODAL_JS}) && ({MODAL_JS}).innerText.length > 20", timeout=30)
    txt = page.dialog_text()
    logger.info("Create-series dialog text: %s", re.sub(r"\s+", " ", txt)[:400])
    # Series title input
    title_input = (
        f"(() => {{ const m = {MODAL_JS}; return Array.from(m.querySelectorAll('input[type=text], input:not([type]), textarea'))"
        f".filter(i => i.getBoundingClientRect().width > 0)[0] || null; }})()"
    )
    if not page.eval(f"!!({title_input})", await_promise=False):
        page.screenshot("create_series_unexpected")
        raise RuntimeError("Create series dialog has no text input; see screenshot")
    page.type_into(title_input, listing.series_title)
    # Reading order: ordered (numbered) if a radio/option mentions 'Ordered'
    page.eval(
        f"""(() => {{ const m = {MODAL_JS};
            const r = Array.from(m.querySelectorAll('input[type=radio]')).find(r => /ordered|numbered/i.test((r.closest('label')||r.parentElement).innerText) && !/unordered/i.test((r.closest('label')||r.parentElement).innerText));
            if (r) r.click(); return !!r; }})()""",
        await_promise=False,
    )
    time.sleep(0.8)
    page.screenshot("create_series_filled")
    for label in ("Save series", "Create series", "Save and continue", "Continue", "Save"):
        if page.eval(f"!!({_btn_js(label, MODAL_JS)})", await_promise=False):
            page.click(_btn_js(label, MODAL_JS), settle=3.0)
            break
    page.wait_saving(timeout=90)
    time.sleep(2)
    txt = page.dialog_text()
    if txt and re.search(r"How is this title related", txt):
        page.click(_btn_js("Main content", MODAL_JS), settle=2.5)
        page.wait_for(f"({MODAL_JS}) && /Confirm or edit/.test(({MODAL_JS}).innerText)", timeout=30)
        page.click(_btn_js("Confirm and continue", MODAL_JS), settle=3.0)
        page.wait_saving(timeout=90)
    _dismiss_series_saved_dialog(page)
    page.wait_for("/Book number/.test(document.body.innerText)", timeout=60)


def _categories_modal(page: CDPPage, paths: list[list[str]]) -> None:
    _open_modal(page, _btn_js("Choose categories"), "Select categories")
    # Start clean
    if page.eval(f"!!({_btn_js('Reset', MODAL_JS)})", await_promise=False):
        page.click(_btn_js("Reset", MODAL_JS), settle=1.5)
    sel_count = f"({MODAL_JS}).querySelectorAll('select').length"
    for i, path in enumerate(paths):
        if i == 0:
            base = 0  # the modal starts with one empty "Select one" dropdown
        else:
            base = page.eval(sel_count, await_promise=False)
            page.click(_btn_js("Add another category", MODAL_JS), settle=1.5)
            page.wait_for(f"{sel_count} > {base}", timeout=20)
        for lvl, name in enumerate(path[:-1]):
            idx = base + lvl
            page.wait_for(f"{sel_count} > {idx}", timeout=20)
            res = page.eval(
                f"""(() => {{ const m = {MODAL_JS}; const s = m.querySelectorAll('select')[{idx}]; if (!s) return 'NO_SELECT';
                    const opt = Array.from(s.options).find(o => o.text.trim() === {json.dumps(name)});
                    if (!opt) return 'NO_OPTION:' + Array.from(s.options).map(o => o.text).join(' | ');
                    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
                    setter.call(s, opt.value); s.dispatchEvent(new Event('input', {{bubbles: true}})); s.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return 'OK'; }})()""",
                await_promise=False,
            )
            if res != "OK":
                page.screenshot("categories_error")
                raise RuntimeError(f"Category level {lvl} {name!r}: {res}")
            time.sleep(1.4)
        leaf = path[-1]
        res = page.eval(
            f"""(() => {{ const m = {MODAL_JS}; const anchor = m.querySelectorAll('select')[{base}];
                const boxes = Array.from(m.querySelectorAll('input[type=checkbox]'))
                  .filter(c => anchor.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING);
                const b = boxes.find(c => ((c.closest('label') || c.parentElement).innerText || '').trim() === {json.dumps(leaf)});
                if (!b) return 'NO_BOX:' + boxes.map(c => (c.closest('label')||c.parentElement).innerText.trim()).join(' | ');
                if (!b.checked) b.click(); return 'OK'; }})()""",
            await_promise=False,
        )
        if res != "OK":
            page.screenshot("categories_error")
            raise RuntimeError(f"Category leaf {leaf!r}: {res}")
        time.sleep(1.0)
        page.wait_for(f"/{i + 1} out of 3 category placements selected/.test(({MODAL_JS}).innerText)", timeout=15)
    page.screenshot("categories_filled")
    page.click(_btn_js("Save categories", MODAL_JS), settle=3.0)
    page.wait_for(f"!({MODAL_JS}) || !/Select categories/.test(({MODAL_JS}).innerText)", timeout=40)
    page.wait_for("/Your title's current categories|Edit categories/.test(document.body.innerText)", timeout=30)


DETAILS_STATE_JS = """({
  title: (document.querySelector('#data-title')||{}).value || '',
  sub: (document.querySelector('#data-subtitle')||{}).value || '',
  first: (document.querySelector('#data-primary-author-first-name')||{}).value || '',
  last: (document.querySelector('#data-primary-author-last-name')||{}).value || '',
  lang: (document.querySelector('#data-language-native')||{}).value || '',
  kws: Array.from({length: 7}, (_, i) => (document.querySelector('#data-keywords-' + i)||{}).value || ''),
  desc: (window.CKEDITOR && Object.keys(CKEDITOR.instances).length) ? CKEDITOR.instances[Object.keys(CKEDITOR.instances)[0]].getData().length : -1,
  rights: !!(document.querySelector('#non-public-domain')||{}).checked,
  adultNo: !!(Array.from(document.querySelectorAll('input[name="data[is_adult_content]-radio"]')).find(r => r.value === 'false')||{}).checked,
  ageMin: (document.querySelector('#data-reading-interest-age-start-input-native')||{}).value || '',
  ageMax: (document.querySelector('#data-reading-interest-age-end-input-native')||{}).value || '',
  market: (document.querySelector('select[name="data[digital][home_marketplace]"]')||{}).value || '',
  series: /Book number/.test(document.body.innerText),
  categories: (document.body.innerText.match(/Kindle Books › /g) || []).length
})"""


def _fill_details_fields(page: CDPPage, listing: Any, state: dict[str, Any]) -> list[str]:
    """Fill only the fields that differ from the listing; returns the names of fields touched."""
    touched: list[str] = []
    if state["lang"] and state["lang"] != "english":
        page.set_native_select("document.querySelector('#data-language-native')", "english")
        touched.append("lang")
    if state["title"] != listing.title:
        page.type_into("document.querySelector('#data-title')", listing.title)
        touched.append("title")
    if state["sub"] != listing.subtitle:
        page.type_into("document.querySelector('#data-subtitle')", listing.subtitle)
        touched.append("subtitle")
    if state["first"] != listing.author_first:
        page.type_into("document.querySelector('#data-primary-author-first-name')", listing.author_first)
        touched.append("first")
    if state["last"] != listing.author_last:
        page.type_into("document.querySelector('#data-primary-author-last-name')", listing.author_last)
        touched.append("last")
    if state["desc"] < 200:
        res = page.set_ckeditor(listing.description_html)
        touched.append(f"desc:{res}")
    if not state["rights"]:
        page.eval("document.querySelector('#non-public-domain').click()", await_promise=False)
        touched.append("rights")
    if not state["adultNo"]:
        page.eval(
            """(() => { const r = Array.from(document.querySelectorAll('input[name="data[is_adult_content]-radio"]')).find(r => r.value === 'false'); if (r) r.click(); })()""",
            await_promise=False,
        )
        touched.append("adultNo")
    if state["ageMin"] != str(listing.reading_age_min):
        page.set_native_select(
            "document.querySelector('#data-reading-interest-age-start-input-native')",
            str(listing.reading_age_min),
        )
        touched.append("ageMin")
    if state["ageMax"] != str(listing.reading_age_max):
        page.set_native_select(
            "document.querySelector('#data-reading-interest-age-end-input-native')",
            str(listing.reading_age_max),
        )
        touched.append("ageMax")
    if state["market"] and state["market"] != "US":
        page.set_native_select("document.querySelector('select[name=\"data[digital][home_marketplace]\"]')", "US")
        touched.append("market")
    wanted_kws = [(listing.keywords[i] if i < len(listing.keywords) else "") for i in range(7)]
    for i, kw in enumerate(wanted_kws):
        if state["kws"][i] != kw:
            page.type_into(f"document.querySelector('#data-keywords-{i}')", kw)
            touched.append(f"kw{i}")
    return touched


def _details_settled(page: CDPPage, listing: Any, max_rounds: int = 6) -> dict[str, Any]:
    """Fill → wait → re-verify until two consecutive rounds need no repairs."""
    stable = 0
    state: dict[str, Any] = {}
    for rnd in range(max_rounds):
        state = page.eval(DETAILS_STATE_JS, await_promise=False)
        touched = _fill_details_fields(page, listing, state)
        logger.info("Details round %d touched: %s", rnd + 1, touched or "nothing")
        if touched:
            stable = 0
            time.sleep(3.0)
            continue
        stable += 1
        if stable >= 2:
            break
        time.sleep(2.5)
    return page.eval(DETAILS_STATE_JS, await_promise=False)


def _open_modal(page: CDPPage, button_js: str, text_regex: str, attempts: int = 4) -> None:
    for i in range(attempts):
        _dismiss_series_saved_dialog(page, timeout=1.5)  # a leftover overlay would swallow the click
        page.click(button_js, settle=2.5)
        try:
            page.wait_for(f"({MODAL_JS}) && /{text_regex}/.test(({MODAL_JS}).innerText)", timeout=12)
            return
        except TimeoutError:
            logger.warning("Modal /%s/ did not open (attempt %d)", text_regex, i + 1)
            time.sleep(2.0)
    page.screenshot("modal_open_failed")
    raise RuntimeError(f"Could not open modal matching /{text_regex}/")


def step_details(page: CDPPage, listing: Any) -> None:
    """Fill the 'Kindle eBook Details' page and continue to Content."""
    url = f"{KDP_BASE}/title-setup/kindle/{listing.kdp_title_id}/details" if listing.kdp_title_id else f"{KDP_BASE}/title-setup/kindle/new/details"
    page.navigate(url, settle=3.0)
    page.wait_for("document.querySelector('#data-title') && document.querySelector('#data-keywords-6') && window.CKEDITOR && Object.keys(CKEDITOR.instances).length", timeout=90)
    time.sleep(4.0)  # let KDP's late widget initialisation finish before typing

    state = _details_settled(page, listing)

    # Series (this click saves the draft first and gives us the title id)
    if not state["series"] or listing.series_title.lower() not in page.body_text().lower():
        page.click("document.querySelector('#add_series_button button')", settle=2.0)
        page.wait_saving(timeout=90)
        try:
            page.wait_for("/\\/title-setup\\/kindle\\/[A-Z0-9]{8,}\\//.test(location.href)", timeout=60)
        except TimeoutError:
            # No title id in the URL means KDP refused to create the draft (e.g. the daily new-title limit).
            errs, dlg = page.page_errors(), page.dialog_text()
            page.screenshot(f"{listing.slug}_draft_not_created")
            prefix = "KDP_LIMIT: " if LIMIT_RE.search(" ".join(errs) + " " + dlg) else ""
            raise RuntimeError(f"{prefix}Draft was not created (no title id in URL). Errors: {errs[:3]} Dialog: {dlg[:200]!r}")
        _save_state(listing, kdp_title_id=_title_id(page.url))
        _series_modal(page, listing)
        time.sleep(2.0)
        state = _details_settled(page, listing)
    _save_state(listing, kdp_title_id=_title_id(page.url) or listing.kdp_title_id)

    # Categories
    if state["categories"] < len(listing.categories):
        _categories_modal(page, listing.categories)
        time.sleep(1.5)
        state = _details_settled(page, listing)

    logger.info("Details final state: %s", {k: (v if k != 'kws' else sum(1 for x in v if x)) for k, v in state.items()})
    missing = [k for k in ("title", "sub", "first", "last") if state[k] != getattr(listing, {"title": "title", "sub": "subtitle", "first": "author_first", "last": "author_last"}[k])]
    if missing or state["desc"] < 200 or not state["rights"] or not state["adultNo"] or state["categories"] < len(listing.categories) or not state["series"]:
        page.screenshot(f"{listing.slug}_details_incomplete")
        raise RuntimeError(f"Details incomplete before save: missing={missing} state={ {k: v for k, v in state.items() if k != 'kws'} }")
    preorder = page.eval("(document.querySelector('input[name=\"data[preorder][enabled]\"]')||{}).value", await_promise=False)
    if preorder not in (None, "", "false"):
        page.screenshot(f"{listing.slug}_preorder_enabled")
        raise RuntimeError(f"Pre-order is enabled (data[preorder][enabled]={preorder!r}); expected 'release now' like Book 1")
    page.screenshot(f"{listing.slug}_details_filled")

    page.click(_btn_js("Save and Continue"), settle=3.0)
    page.wait_saving(timeout=120)
    try:
        page.wait_for("location.href.includes('/content')", timeout=90)
    except TimeoutError:
        errs = page.page_errors()
        page.screenshot(f"{listing.slug}_details_error")
        raise RuntimeError(f"Details page did not advance to Content. Errors: {errs}")
    _save_state(listing, status="details", kdp_title_id=_title_id(page.url) or listing.kdp_title_id)
    logger.info("Details complete for %s (%s)", listing.slug, listing.kdp_title_id)


DLP_MARK = "justification_page"  # Google-internal "IP Protect by PSS" upload justification page


def _notify(message: str) -> None:
    """macOS notification with sound (message passed via argv to avoid AppleScript quoting issues)."""
    subprocess.run(
        ["osascript", "-e", "on run argv", "-e",
         'display notification (item 1 of argv) with title "KDP Publisher" sound name "Glass"', "-e", "end run", message],
        capture_output=True, check=False,
    )


def _safe_url(page: CDPPage) -> str:
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001  (navigation in progress)
        return ""


def _wait_dlp_approval(page: CDPPage, label: str, timeout: float = 3600.0) -> None:
    """The user must approve the IP Protect prompt themselves; we only alert and wait."""
    logger.warning("IP Protect prompt shown for %s — waiting for the user to approve it", label)
    attention = KDP_DIR / "ATTENTION.txt"
    attention.write_text(f"{time.ctime()} waiting for IP Protect approval: {label}\n", encoding="utf-8")
    try:
        page.call("Page.bringToFront")
    except Exception:  # noqa: BLE001
        pass
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True, check=False)
    _notify(f"Please approve the IP Protect upload prompt: {label}")
    deadline, last_ping = time.time() + timeout, time.time()
    while time.time() < deadline:
        url = _safe_url(page)
        if url and DLP_MARK not in url and "kdp.amazon.com" in url:
            logger.info("IP Protect prompt resolved; back on %s", url[:90])
            attention.unlink(missing_ok=True)
            page.wait_ready(timeout=60)
            time.sleep(3)
            return
        if time.time() - last_ping > 120:
            _notify(f"Still waiting: approve the IP Protect upload prompt ({label})")
            last_ping = time.time()
        time.sleep(2)
    raise TimeoutError(f"IP Protect approval not received within {timeout:.0f}s for {label}")


def _justification_tabs(file_sha256: str) -> list[dict[str, Any]]:
    """IP Protect justification tabs whose payload lists this file's SHA-256 (the extension uses full hex hashes)."""
    try:
        targets = list_targets()
    except Exception:  # noqa: BLE001
        return []
    return [t for t in targets
            if t.get("type") == "page" and DLP_MARK in (t.get("url") or "")
            and file_sha256 in urllib.parse.unquote(t.get("url") or "")]


def _wait_side_tab_approval(file_sha256: str, label: str, grace: float = 8.0, timeout: float = 3600.0) -> None:
    """IP Protect sometimes opens its prompt in a *separate* tab while the upload itself succeeds.

    The user still answers every prompt personally, so show the tab, alert, and wait until it is gone.
    """
    time.sleep(grace)  # the extension opens its tab a moment after the upload
    tabs = _justification_tabs(file_sha256)
    if not tabs:
        return
    logger.warning("IP Protect opened a justification tab for %s — waiting for the user to answer it", label)
    attention = KDP_DIR / "ATTENTION.txt"
    attention.write_text(f"{time.ctime()} waiting for IP Protect approval (separate tab): {label}\n", encoding="utf-8")
    try:
        urllib.request.urlopen(f"{CDP_HTTP}/json/activate/{tabs[0]['id']}", timeout=5).read()  # show it; never fill it
    except Exception:  # noqa: BLE001
        pass
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True, check=False)
    _notify(f"Please answer the IP Protect prompt in Chrome: {label}")
    deadline, last_ping = time.time() + timeout, time.time()
    while time.time() < deadline:
        if not _justification_tabs(file_sha256):
            logger.info("IP Protect justification tab answered for %s", label)
            attention.unlink(missing_ok=True)
            return
        if time.time() - last_ping > 120:
            _notify(f"Still waiting: answer the IP Protect prompt ({label})")
            last_ping = time.time()
        time.sleep(3)
    raise TimeoutError(f"IP Protect approval not received within {timeout:.0f}s for {label}")


def _upload(page: CDPPage, input_selector: str, path: str, success_regex: str, label: str,
            timeout: float = 600.0, max_attempts: int = 4) -> None:
    """Set a file on KDP's hidden AjaxInput and wait for the 'uploaded successfully' confirmation.

    If the IP Protect extension intercepts the upload (the tab is redirected to its justification page),
    alert the user, wait for their approval, then upload the file again. If it instead opens its prompt
    in a separate tab, wait for the user to answer that tab after the upload confirms.
    """
    file_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for attempt in range(1, max_attempts + 1):
        page.wait_for(f"!!document.querySelector({json.dumps(input_selector)})", timeout=90)
        page.set_files(input_selector, [path])
        page.eval(
            f"""(() => {{ const el = document.querySelector({json.dumps(input_selector)});
                el.dispatchEvent(new Event('input', {{bubbles: true}})); el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""",
            await_promise=False,
        )
        started, last = time.time(), ""
        redirected = False
        while time.time() - started < timeout:
            url = _safe_url(page)
            if DLP_MARK in url:
                _wait_dlp_approval(page, label)
                redirected = True
                break
            try:
                body = page.body_text()
            except Exception:  # noqa: BLE001
                time.sleep(2)
                continue
            if re.search(success_regex, body):
                logger.info("Upload confirmed: %s", label)
                _wait_side_tab_approval(file_sha256, label)
                return
            errs = [e for e in page.page_errors() if re.search(r"upload|file|cover|manuscript", e, re.I)]
            if errs and time.time() - started > 20:
                page.screenshot("upload_error")
                raise RuntimeError(f"Upload error ({label}): {errs[:3]}")
            m = re.search(r"(Uploading|Processing|processing)[^\n]{0,80}", body)
            cur = m.group(0) if m else ""
            if cur != last:
                logger.info("Upload status (%s): %s", label, cur or "(waiting)")
                last = cur
            time.sleep(3)
        if redirected:
            logger.info("Re-uploading %s after IP Protect approval (attempt %d)", label, attempt + 1)
            continue
        page.screenshot("upload_timeout")
        raise TimeoutError(f"Upload did not confirm /{success_regex}/ within {timeout}s ({label})")
    raise RuntimeError(f"Upload of {label} did not complete after {max_attempts} attempts")


def _click_radio_value(page: CDPPage, name: str, value: str) -> bool:
    return bool(page.eval(
        f"""(() => {{ const r = Array.from(document.querySelectorAll('input[type=radio][name={json.dumps(name)}]')).find(r => r.value === {json.dumps(value)});
            if (!r) return false; if (!r.checked) {{ r.click(); }} return r.checked; }})()""",
        await_promise=False,
    ))


ACCESSIBILITY_LABELS = {
    "unknown": "I don't know if my informative images",
    "not_readable": "None of the informative images",
    "partially_readable": "Some informative images",
    "readable": "All informative images",
}
CONFIRM_BOX_JS = ("Array.from(document.querySelectorAll('label'))"
                  ".filter(l => /By clicking this, I confirm that my answers are accurate/.test(l.innerText||''))"
                  ".map(l => l.querySelector('[role=checkbox]')).filter(Boolean)")


def _set_accessibility(page: CDPPage, value: str) -> None:
    prefix = ACCESSIBILITY_LABELS[value]
    radio_js = (f"(() => {{ const l = Array.from(document.querySelectorAll('label'))"
                f".find(l => (l.innerText||'').trim().startsWith({json.dumps(prefix)})); return l ? l.querySelector('input[type=radio]') : null; }})()")
    if not page.eval(f"!!({radio_js})", await_promise=False):
        raise RuntimeError(f"Accessibility option not found: {prefix!r}")
    for attempt in range(3):
        if page.eval(f"({radio_js}).checked", await_promise=False):
            return
        if attempt == 0:
            page.click(radio_js, settle=1.0)
        else:
            page.eval(f"({radio_js}).click()", await_promise=False)
            time.sleep(1.0)
    raise RuntimeError(f"Could not select accessibility option {prefix!r}")


def _confirm_answers_accurate(page: CDPPage) -> int:
    """Tick every visible 'I confirm that my answers are accurate' checkbox; returns how many are ticked."""
    n = page.eval(f"({CONFIRM_BOX_JS}).length", await_promise=False) or 0
    for i in range(n):
        box = f"({CONFIRM_BOX_JS})[{i}]"
        for attempt in range(3):
            if page.eval(f"{box}.getAttribute('aria-checked') === 'true'", await_promise=False):
                break
            if attempt == 0:
                page.click(box, settle=1.0)
            else:
                page.eval(f"{box}.click()", await_promise=False)
                time.sleep(1.0)
        else:
            raise RuntimeError("Could not tick the 'answers are accurate' confirmation box")
    if n:
        logger.info("Ticked %d 'answers are accurate' confirmation box(es)", n)
    return n


def _set_ai_block(page: CDPPage, select_id: str, value: str, tool: str) -> None:
    res = page.set_native_select(f"document.querySelector('#{select_id}')", value)
    if not str(res).startswith("OK"):
        raise RuntimeError(f"AI select {select_id}: {res}")
    time.sleep(1.2)
    if value != "NONE" and tool:
        tool_input = (
            f"""(() => {{ const q = document.querySelector('.ditto-questionnaire'); const s = document.querySelector('#{select_id}');
                const sels = Array.from(q.querySelectorAll('select')); const next = sels[sels.indexOf(s) + 1];
                return Array.from(q.querySelectorAll('input[type=text]')).filter(i => i.getBoundingClientRect().width > 0)
                  .find(i => (s.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING) && (!next || (i.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING))) || null; }})()"""
        )
        page.wait_for(f"!!({tool_input})", timeout=15)
        page.type_into(tool_input, tool)


def step_content(page: CDPPage, listing: Any) -> None:
    """Upload manuscript + cover, set DRM / AI declaration / accessibility, continue to Pricing."""
    book = listing.title[:40]
    # Never continue past an IP Protect prompt the user hasn't answered yet (e.g. when resuming a run)
    for path, kind in ((listing.epub_path, "manuscript (EPUB)"), (listing.cover_path, "cover (JPG)")):
        _wait_side_tab_approval(hashlib.sha256(Path(path).read_bytes()).hexdigest(), f"{book} — {kind}", grace=0)
    url = f"{KDP_BASE}/title-setup/kindle/{listing.kdp_title_id}/content"
    if not page.url.startswith(url):
        page.navigate(url, settle=3.0)
    page.wait_for("document.querySelector('#data-assets-interior-file-upload-AjaxInput') && document.querySelector('#data-cover-choice')", timeout=90)
    time.sleep(3.0)
    # Require *this* file: a draft may still hold an older manuscript (e.g. Book 2's uncompressed EPUB)
    manuscript_ok = rf"Manuscript \"{re.escape(Path(listing.epub_path).name)}\" uploaded successfully"
    cover_ok = r"Cover uploaded successfully"
    for pass_no in range(1, 4):
        body = page.body_text()
        if re.search(manuscript_ok, body) and re.search(cover_ok, body):
            break
        # "Do you have a file to upload now?" (only shown for unpublished titles)
        _click_radio_value(page, "data[is_assetless_preorder_choice]-radio", "false")
        time.sleep(1.0)
        if not re.search(manuscript_ok, body):
            logger.info("Uploading manuscript %s (pass %d)", Path(listing.epub_path).name, pass_no)
            _upload(page, "#data-assets-interior-file-upload-AjaxInput", listing.epub_path, manuscript_ok,
                    label=f"{book} — manuscript (EPUB)", timeout=900)
            body = page.body_text()
        if not re.search(cover_ok, body):
            page.eval(
                """(() => { const a = document.querySelector('#data-cover-choice-accordion'); if (!a) return 'NO_ACCORDION';
                    const row = Array.from(a.children).find(c => /Upload a cover you already have/.test(c.innerText||''));
                    const h = row && (row.querySelector('.a-accordion-row') || row.querySelector('a')); if (h) h.click(); return h ? 'OK' : 'NO_ROW'; })()""",
                await_promise=False,
            )
            page.wait_for("document.querySelector('#data-cover-choice').value === 'upload'", timeout=15)
            time.sleep(1.0)
            logger.info("Uploading cover %s (pass %d)", Path(listing.cover_path).name, pass_no)
            _upload(page, "#data-assets-cover-file-upload-AjaxInput", listing.cover_path, cover_ok,
                    label=f"{book} — cover (JPG)", timeout=300)
        time.sleep(2.0)
    else:
        page.screenshot(f"{listing.slug}_uploads_missing")
        raise RuntimeError("Manuscript and cover are not both showing as uploaded after 3 passes")
    logger.info("Manuscript and cover uploaded")

    # DRM
    if not _click_radio_value(page, "data[is_drm]-radio", "true" if listing.drm else "false"):
        raise RuntimeError("Could not set DRM radio")

    # AI-generated content declaration
    yes_js = "Array.from(document.querySelectorAll('.ditto-questionnaire [role=radio]')).find(r => (r.innerText||'').trim() === 'Yes')"
    if page.eval(f"({yes_js}) && ({yes_js}).getAttribute('aria-checked') !== 'true'", await_promise=False):
        page.click(f"({yes_js}).querySelector('a') || ({yes_js})", settle=1.5)
        if page.eval(f"({yes_js}).getAttribute('aria-checked') !== 'true'", await_promise=False):
            page.eval(f"(({yes_js}).querySelector('a') || ({yes_js})).click()", await_promise=False)
            time.sleep(1.5)
    page.wait_for("document.querySelector('#generative-ai-questionnaire-text') && document.querySelector('#generative-ai-questionnaire-text').getBoundingClientRect().width > 0", timeout=20)
    _set_ai_block(page, "generative-ai-questionnaire-text", listing.ai_text, listing.ai_text_tool)
    _set_ai_block(page, "generative-ai-questionnaire-images", listing.ai_images, listing.ai_images_tool)
    _set_ai_block(page, "generative-ai-questionnaire-translations", listing.ai_translations, listing.ai_translations_tool)

    # Accessibility: informative images alt text (radios share a stale value attr → select by label text)
    _set_accessibility(page, listing.accessibility_images)
    # "It looks like you've uploaded a new manuscript or book cover — I confirm that my answers are accurate"
    _confirm_answers_accurate(page)

    state = page.eval(
        f"""({{manuscript: document.body.innerText.includes('Manuscript "' + {json.dumps(Path(listing.epub_path).name)} + '" uploaded successfully'),
            cover: /Cover uploaded successfully/.test(document.body.innerText),
            drm: (Array.from(document.querySelectorAll('input[name="data[is_drm]-radio"]')).find(r => r.checked)||{{}}).value,
            aiYes: (Array.from(document.querySelectorAll('.ditto-questionnaire [role=radio]')).find(r => (r.innerText||'').trim() === 'Yes')||{{getAttribute: () => null}}).getAttribute('aria-checked'),
            aiText: document.querySelector('#generative-ai-questionnaire-text').value,
            aiImages: document.querySelector('#generative-ai-questionnaire-images').value,
            aiTrans: document.querySelector('#generative-ai-questionnaire-translations').value,
            access: ((Array.from(document.querySelectorAll('input[name="data[accessibility][image_reading]"]')).find(r => r.checked)||{{closest: () => null}}).closest('label')||{{innerText: ''}}).innerText.trim().slice(0, 40),
            confirmBoxes: ({CONFIRM_BOX_JS}).map(b => b.getAttribute('aria-checked'))}})""",
        await_promise=False,
    )
    logger.info("Content state: %s", state)
    ok = (state["manuscript"] and state["cover"] and state["drm"] == ("true" if listing.drm else "false")
          and state["aiYes"] == "true" and state["aiText"] == listing.ai_text and state["aiImages"] == listing.ai_images
          and state["aiTrans"] == listing.ai_translations
          and state["access"].startswith(ACCESSIBILITY_LABELS[listing.accessibility_images])
          and all(c == "true" for c in state["confirmBoxes"]))
    if not ok:
        page.screenshot(f"{listing.slug}_content_incomplete")
        raise RuntimeError(f"Content incomplete: {state}")
    page.screenshot(f"{listing.slug}_content_filled")

    page.click(_btn_js("Save and Continue"), settle=3.0)
    page.wait_saving(timeout=120)
    for _ in range(2):
        try:
            page.wait_for("location.href.includes('/pricing')", timeout=60)
            break
        except TimeoutError:
            dlg = page.dialog_text()
            if dlg:
                logger.warning("Dialog after Save and Continue: %s", re.sub(r"\s+", " ", dlg)[:300])
                page.screenshot(f"{listing.slug}_content_dialog")
                btn = _btn_js("Continue", MODAL_JS, starts_with=True)
                if page.eval(f"!!({btn})", await_promise=False):
                    page.click(btn, settle=3.0)
                    continue
            errs = page.page_errors()
            page.screenshot(f"{listing.slug}_content_error")
            raise RuntimeError(f"Content page did not advance to Pricing. Errors: {errs} Dialog: {dlg[:200]}")
    _save_state(listing, status="content")
    logger.info("Content complete for %s", listing.slug)


US_PRICE_INPUT = "document.querySelector('input[name=\"data[digital][channels][amazon][US][price_vat_inclusive]\"]')"
LIMIT_RE = re.compile(r"limit|exceed|too many|maximum number", re.I)


def step_pricing(page: CDPPage, listing: Any, submit: bool = False) -> None:
    """KDP Select / territories / royalty / price (mirrors Book 1), then Save as Draft or Publish."""
    url = f"{KDP_BASE}/title-setup/kindle/{listing.kdp_title_id}/pricing"
    if not page.url.startswith(url):
        page.navigate(url, settle=3.0)
    page.wait_for(f"document.querySelector('#data-is-select') && {US_PRICE_INPUT}", timeout=90)
    time.sleep(2.0)

    # KDP: $0.99 at 35% royalty is only allowed when the converted file is <= 3 MB
    size_m = re.search(r"file size after conversion is ([\d.]+)\s*MB", page.body_text())
    converted_mb = float(size_m.group(1)) if size_m else None
    logger.info("KDP file size after conversion: %s MB", converted_mb)
    if (converted_mb is not None and listing.royalty_rate == "35_PERCENT"
            and float(listing.price_usd) < 1.99 and converted_mb > 3.0):
        page.screenshot(f"{listing.slug}_file_too_big")
        raise RuntimeError(f"Converted file is {converted_mb} MB; ${listing.price_usd} at 35% needs <= 3 MB "
                           f"(re-run kdp_epub_optimize.py write --only {listing.slug} --target <smaller>)")

    # KDP Select enrollment (Book 1 is enrolled)
    want_select = bool(listing.kdp_select)
    if page.eval("document.querySelector('#data-is-select').checked", await_promise=False) != want_select:
        page.click("document.querySelector('#data-is-select')", settle=2.0)
        if page.eval("document.querySelector('#data-is-select').checked", await_promise=False) != want_select:
            page.eval("document.querySelector('#data-is-select').click()", await_promise=False)
            time.sleep(1.5)
        dlg = page.dialog_text()
        if dlg.strip():
            page.screenshot(f"{listing.slug}_select_dialog")
            raise RuntimeError("Unexpected dialog after KDP Select toggle: " + re.sub(r"\s+", " ", dlg)[:300])

    # Territories: all (worldwide rights)
    if listing.territories == "all":
        ww = page.eval("(document.querySelector('input[name=\"data[digital][worldwide_rights]\"]')||{}).value", await_promise=False)
        if ww != "true":
            page.eval(
                """(() => { const a = document.querySelector('#data-digital-worldwide-rights-accordion');
                    const row = a && Array.from(a.children).find(c => /^All territories/.test((c.innerText||'').trim()));
                    const h = row && row.querySelector('.a-accordion-row'); if (h) h.click(); return !!h; })()""",
                await_promise=False,
            )
            time.sleep(1.5)

    # Royalty plan + list price
    if not _click_radio_value(page, "data[digital][royalty_rate]-radio", listing.royalty_rate):
        raise RuntimeError(f"Could not select royalty plan {listing.royalty_rate}")
    time.sleep(1.0)
    page.type_into(US_PRICE_INPUT, listing.price_usd)
    page.eval(f"(() => {{ const el = {US_PRICE_INPUT}; el.dispatchEvent(new Event('blur', {{bubbles: true}})); el.dispatchEvent(new Event('focusout', {{bubbles: true}})); el.blur(); }})()", await_promise=False)
    time.sleep(3.0)

    state = page.eval(
        f"""({{select: document.querySelector('#data-is-select').checked,
            selectHidden: (document.querySelector('input[name="data[is_select]"]')||{{}}).value,
            worldwide: (document.querySelector('input[name="data[digital][worldwide_rights]"]')||{{}}).value,
            royalty: (Array.from(document.querySelectorAll('input[name="data[digital][royalty_rate]-radio"]')).find(r => r.checked)||{{}}).value,
            us: ({US_PRICE_INPUT}).value,
            uk: (document.querySelector('input[name="data[digital][channels][amazon][UK][price_vat_inclusive]"]')||{{}}).value,
            jp: (document.querySelector('input[name="data[digital][channels][amazon][JP][price_vat_inclusive]"]')||{{}}).value,
            royaltyText: ((document.body.innerText.match(/Amazon\\.com\\s*\\n\\s*\\$[\\s\\S]{{0,160}}/)||[''])[0]).replace(/\\s+/g, ' ').slice(0, 160)}})""",
        await_promise=False,
    )
    logger.info("Pricing state: %s", state)
    ok = (state["select"] == want_select and state["worldwide"] == "true" and state["royalty"] == listing.royalty_rate
          and state["us"] == listing.price_usd and bool(state["uk"]) and bool(state["jp"]))
    if not ok:
        page.screenshot(f"{listing.slug}_pricing_incomplete")
        raise RuntimeError(f"Pricing incomplete: {state}")
    errs = page.page_errors()
    if errs:
        page.screenshot(f"{listing.slug}_pricing_errors")
        raise RuntimeError(f"Pricing page shows errors: {errs}")
    page.screenshot(f"{listing.slug}_pricing_filled")

    if not submit:
        page.click(_btn_js("Save as Draft"), settle=3.0)
        page.wait_saving(timeout=120)
        errs = page.page_errors()
        if errs:
            raise RuntimeError(f"Save as Draft errors: {errs}")
        _save_state(listing, status="pricing")
        logger.info("Pricing saved as draft for %s (not published)", listing.slug)
        return

    logger.info("Publishing %s ...", listing.slug)
    page.click(_btn_js("Publish Your Kindle eBook"), settle=4.0)
    deadline = time.time() + 120
    while time.time() < deadline:
        url_now = _safe_url(page)
        try:
            body = page.body_text()
        except Exception:  # noqa: BLE001
            time.sleep(2)
            continue
        if "/pricing" not in url_now or re.search(r"being published|Congratulations|submitted for publishing|In review", body, re.I):
            page.screenshot(f"{listing.slug}_published")
            _save_state(listing, status="published")
            logger.info("Published %s → %s", listing.slug, url_now[:100])
            return
        errs = page.page_errors()
        if errs:
            page.screenshot(f"{listing.slug}_publish_error")
            if any(LIMIT_RE.search(e) for e in errs):
                raise RuntimeError(f"KDP_LIMIT: {errs}")
            raise RuntimeError(f"Publish errors: {errs}")
        dlg = page.dialog_text()
        if dlg.strip() and not re.search(r"Saving|Publishing", dlg):
            page.screenshot(f"{listing.slug}_publish_dialog")
            prefix = "KDP_LIMIT: " if re.search(r"limit reached|submission limit|creation limit", dlg, re.I) else ""
            raise RuntimeError(f"{prefix}Unexpected dialog after Publish: " + re.sub(r"\s+", " ", dlg)[:400])
        time.sleep(2)
    page.screenshot(f"{listing.slug}_publish_timeout")
    raise TimeoutError("No confirmation after clicking Publish")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _logged_in(url: str) -> bool:
    return "kdp.amazon.com" in url and "signin" not in url and "/ap/" not in url


def cmd_status(_: argparse.Namespace) -> int:
    page = connect_kdp_tab()
    try:
        page.call("Page.bringToFront")
        if "bookshelf" not in page.url and _logged_in(page.url):
            page.navigate(BOOKSHELF_URL, settle=3.0)
        url = page.url
        logged_in = _logged_in(url)
        page.screenshot("status")
        print(json.dumps({"url": url, "logged_in": logged_in}, indent=2))
        if logged_in:
            print("---- BOOKSHELF TEXT (first 4000 chars) ----")
            print(page.body_text()[:4000])
        return 0
    finally:
        page.close()


def cmd_wait_login(args: argparse.Namespace) -> int:
    """Bring the KDP window to front and wait until the user has signed in."""
    page = connect_kdp_tab()
    try:
        page.call("Page.bringToFront")
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to activate'],
            capture_output=True, check=False,
        )
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline:
            url = page.url
            if _logged_in(url):
                if "bookshelf" not in url:
                    page.navigate(BOOKSHELF_URL, settle=3.0)
                page.wait_for("document.body && document.body.innerText.length > 200", timeout=30)
                page.screenshot("bookshelf")
                print(json.dumps({"url": page.url, "logged_in": True}, indent=2))
                print(page.body_text()[:4000])
                return 0
            time.sleep(3)
        print(json.dumps({"url": page.url, "logged_in": False, "error": "timeout waiting for login"}, indent=2))
        return 2
    finally:
        page.close()


def cmd_shot(args: argparse.Namespace) -> int:
    page = connect_kdp_tab()
    try:
        print(page.screenshot(args.name))
        print(page.url)
        return 0
    finally:
        page.close()


def cmd_eval(args: argparse.Namespace) -> int:
    page = connect_kdp_tab()
    try:
        val = page.eval(args.js)
        print(json.dumps(val, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        page.close()


def cmd_publish(args: argparse.Namespace) -> int:
    from kdp_listings import load_listing

    listing = load_listing(args.slug)
    if args.title_id:
        _save_state(listing, kdp_title_id=args.title_id)
    page = connect_kdp_tab()
    try:
        page.call("Page.bringToFront")
        order = ["details", "content", "pricing"]
        done = order.index(listing.status) + 1 if listing.status in order else 0
        if listing.status == "published":
            print(f"{listing.slug} already published ({listing.asin or listing.kdp_title_id})")
            return 0
        if args.from_step:
            done = min(done, order.index(args.from_step))
        stop = order.index(args.until) + 1
        steps = {"details": step_details, "content": globals().get("step_content"), "pricing": globals().get("step_pricing")}
        for name in order[done:stop]:
            fn = steps[name]
            if fn is None:
                print(f"Step {name} not implemented yet")
                return 2
            logger.info("=== %s: %s ===", listing.slug, name)
            if name == "pricing":
                fn(page, listing, submit=args.submit)
            else:
                fn(page, listing)
        print(json.dumps({"slug": listing.slug, "status": listing.status, "kdp_title_id": listing.kdp_title_id, "asin": listing.asin, "notes": listing.notes}, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        shot = page.screenshot(f"{listing.slug}_FAILED")
        logger.error("FAILED %s at status=%s: %s (screenshot %s)", listing.slug, listing.status, exc, shot)
        _save_state(listing, notes=listing.notes + [f"error: {str(exc)[:300]}"])
        return 1
    finally:
        page.close()


def cmd_batch(args: argparse.Namespace) -> int:
    """Run the title-setup flow for every planned listing, in series order, stopping safely on trouble."""
    from kdp_listings import BOOK_PLAN, load_listing

    slugs = [p["slug"] for p in BOOK_PLAN]
    if args.only:
        wanted = set(args.only.split(","))
        slugs = [s for s in slugs if s in wanted or any(s.startswith(w) for w in wanted)]
    report: list[dict[str, Any]] = []
    consecutive_failures = 0
    creation_blocked = False  # KDP: max 2 new titles per format per week (resets Sunday 00:00 UTC)
    for slug in slugs:
        listing = load_listing(slug)
        if listing.status == "published":
            report.append({"slug": slug, "status": "published", "kdp_title_id": listing.kdp_title_id})
            continue
        if creation_blocked and not listing.kdp_title_id:
            report.append({"slug": slug, "rc": None, "status": listing.status, "kdp_title_id": None,
                           "error": "skipped: weekly title-creation limit reached (resets Sunday 00:00 UTC)"})
            continue
        sub_args = argparse.Namespace(slug=slug, until=args.until, submit=args.submit, title_id=None, from_step=None)
        logger.info("######## %s (status=%s) → until %s%s ########", slug, listing.status, args.until, " + PUBLISH" if args.submit else "")
        rc = cmd_publish(sub_args)
        listing = load_listing(slug)
        last_note = listing.notes[-1] if listing.notes else ""
        report.append({"slug": slug, "rc": rc, "status": listing.status, "kdp_title_id": listing.kdp_title_id,
                       "error": last_note if rc else ""})
        (KDP_DIR / "batch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if rc == 0:
            consecutive_failures = 0
            continue
        if "KDP_LIMIT" in last_note and re.search(r"creation limit", last_note, re.I):
            logger.error("Weekly title-creation limit reached; continuing only with listings that already have a KDP draft")
            creation_blocked = True
            continue
        consecutive_failures += 1
        if "KDP_LIMIT" in last_note or LIMIT_RE.search(last_note):
            logger.error("Stopping batch: KDP limit reached (%s)", last_note[:200])
            break
        if "IP Protect approval not received" in last_note:
            logger.error("Stopping batch: IP Protect prompt was not approved in time")
            break
        if consecutive_failures >= 2:
            logger.error("Stopping batch after 2 consecutive failures")
            break
        try:
            page = connect_kdp_tab()
            signed_in = _logged_in(_safe_url(page)) or "Sign out" in page.body_text()
            page.close()
        except Exception:  # noqa: BLE001
            signed_in = False
        if not signed_in:
            logger.error("Stopping batch: KDP session appears signed out")
            break
    (KDP_DIR / "batch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(r.get("rc", 0) == 0 for r in report) else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("launch")
    sub.add_parser("status")
    p_wait = sub.add_parser("wait-login")
    p_wait.add_argument("--minutes", type=float, default=10.0)
    p_shot = sub.add_parser("shot")
    p_shot.add_argument("--name", default="shot")
    p_eval = sub.add_parser("eval")
    p_eval.add_argument("js")
    p_pub = sub.add_parser("publish", help="Run the KDP title-setup flow for one listing slug")
    p_pub.add_argument("slug")
    p_pub.add_argument("--until", choices=["details", "content", "pricing"], default="pricing")
    p_pub.add_argument("--submit", action="store_true", help="Click 'Publish Your Kindle eBook' at the end (otherwise Save as Draft)")
    p_pub.add_argument("--title-id", default=None, help="Resume an existing KDP draft id")
    p_pub.add_argument("--from-step", choices=["details", "content", "pricing"], default=None,
                       help="Re-run starting from this step even if it was completed before")
    p_batch = sub.add_parser("batch", help="Run the flow for all listings in series order")
    p_batch.add_argument("--until", choices=["details", "content", "pricing"], default="details")
    p_batch.add_argument("--submit", action="store_true", help="Publish at the end of the pricing step")
    p_batch.add_argument("--only", default="", help="Comma-separated slugs or slug prefixes (e.g. 'lily-robo-,mmb-01')")
    args = ap.parse_args(argv)
    if args.cmd == "launch":
        launch_chrome()
        return 0
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "wait-login":
        return cmd_wait_login(args)
    if args.cmd == "shot":
        return cmd_shot(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    if args.cmd == "publish":
        return cmd_publish(args)
    if args.cmd == "batch":
        return cmd_batch(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
