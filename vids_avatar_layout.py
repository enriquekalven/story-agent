"""Copy the approved Google Vids AI-avatar layout from a reference project to other projects, scene by scene.

What gets copied per scene (matched by book page): avatar scale, symmetric left/right crop, mask shape
(e.g. Rounded Rectangle) and X/Y position.

Why it works this way: Google Vids has no API for element geometry and its crop has no numeric field. So we
drive the editor in a *background* Chrome tab through `vids_book_flow.exec_js` (never activating Chrome and
never touching any window's active tab):

1. Select the scene's avatar video.
2. Format options -> Height (aspect locked) to match the reference scale.
3. Crop mode -> drag the left/right crop handles until the visible width matches (iterative, ~1 px precision).
4. Mask video -> reference shape.
5. Format options -> X / Y (top-left of the visible box, 1920x1080 space).
6. Verify against the reference by re-reading geometry from the filmstrip thumbnail clip paths.

Geometry source: each filmstrip thumbnail renders the avatar with three clip paths - the crop rect, the full
(uncropped) video rect and the mask path - in punch units (page = 365760 x 205740). We convert those to the
1920x1080 pixel space used by Format options.

Usage:
    python vids_avatar_layout.py --reference <EN_DOC_ID> --targets <DOC_ID> [<DOC_ID> ...] [--pages 1-20]
    python vids_avatar_layout.py --reference <EN_DOC_ID> --dump          # print the reference layout only

Each target must already be open in a background Chrome tab (see `vids_book_flow.open_in_background_tab`).
"""

import argparse
import json
import time
from typing import Any, Callable, Dict, List, Optional

import vids_book_flow as vbf

UNITS_W, UNITS_H = 365760.0, 205740.0
FMT_W, FMT_H = 1920.0, 1080.0
NUM_IDX = {"width": 0, "height": 1, "x": 5, "y": 6}  # order of input[type=number] in Format options

# ------------------------------------------------------------------------------------------------
# Browser snippets
# ------------------------------------------------------------------------------------------------

GEOMETRY_JS = r"""(() => {
  const bb = (el) => { try { const b = el.getBBox(); return [b.x, b.y, b.width, b.height]; } catch (e) { return null; } };
  const rows = [];
  [...document.querySelectorAll("g.punch-filmstrip-thumbnail")].forEach((t) => {
    const slideG = [...t.querySelectorAll("g[id^='filmstrip-slide-']")].find(g => !g.id.endsWith("-bg"));
    if (!slideG) return;
    const m = slideG.id.match(/^filmstrip-slide-(\d+)-(.+)$/);
    const groups = [...t.querySelectorAll("g[id^='filmstrip-slide-']")].map(g => g.id.replace(/^filmstrip-slide-\d+-/, ""));
    const clips = [...t.querySelectorAll("[clip-path]")].map(e => {
      const ref = (e.getAttribute("clip-path") || "").match(/#([^)]+)/);
      const cp = ref ? document.getElementById(ref[1]) : null;
      const shape = cp ? cp.firstElementChild : null;
      const d = shape ? (shape.getAttribute("d") || "") : "";
      return {ref: ref ? ref[1] : null, bbox: shape ? bb(shape) : null, nC: (d.match(/C/g) || []).length, nL: (d.match(/L/g) || []).length};
    }).filter(c => c.ref && !/^goog_/.test(c.ref));
    rows.push({scene: m ? parseInt(m[1], 10) + 1 : null, slideId: m ? m[2] : null, groups, clips});
  });
  return JSON.stringify(rows);
})()"""

_HELPERS = r"""
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0.5 && r.height > 0.5 && getComputedStyle(el).visibility !== 'hidden'; };
  const click = (el, x, y) => { const r = el.getBoundingClientRect(); const cx = x === undefined ? r.left + r.width / 2 : x; const cy = y === undefined ? r.top + r.height / 2 : y; const o = {bubbles: true, cancelable: true, clientX: cx, clientY: cy, button: 0}; ['mouseover', 'mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, o))); };
  const cropBtn = () => document.getElementById('cropVideoButton');
  const exitCrop = () => { const c = cropBtn(); if (c && c.getAttribute('aria-pressed') === 'true') click(c); };
  const closeMenus = () => { document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true})); };
  const pageScale = () => { const bg = [...document.querySelectorAll("svg g[id^='editor-'][id$='-bg']")].filter(vis).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0]; return bg ? bg.getBoundingClientRect().width / 1920 : null; };
  const avatarLeaf = () => {
    const leaves = [...document.querySelectorAll("svg g[id^='editor-']")].filter(g => vis(g) && !g.id.endsWith('-bg') && g.querySelectorAll("g[id^='editor-']").length === 0);
    const vids = new Set([...document.querySelectorAll("g[id^='zindexedVideo-']")].filter(vis).map(g => g.id.replace('zindexedVideo-', '')));
    let av = leaves.find(g => vids.has(g.id.replace('editor-', '')));
    if (!av && leaves.length > 1) av = [...leaves].sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[1];
    return av || null;
  };
  const fmtRoot = () => document.querySelector('.appsElementsSideSheetRoot');
  const fmtNums = () => { const r = fmtRoot(); return r ? [...r.querySelectorAll("input[type=number]")] : []; };
  const readFmt = () => { const n = fmtNums().map(e => e.value); return {width: parseFloat(n[0]), height: parseFloat(n[1]), x: parseFloat(n[5]), y: parseFloat(n[6]), raw: n.slice(0, 7)}; };
"""

SELECT_JS = "(() => {" + _HELPERS + r"""
  exitCrop(); closeMenus();
  const av = avatarLeaf();
  if (!av) return JSON.stringify({ok: false, err: 'NO_AVATAR'});
  const r = av.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  click(document.elementFromPoint(cx, cy) || av, cx, cy);
  const more = document.getElementById('moreButton');
  if (more && vis(more) && more.getAttribute('aria-pressed') !== 'true') click(more);
  const c = cropBtn();
  return JSON.stringify({ok: !!c && !/disabled/.test(c.className), id: av.id, rect: [r.left, r.top, r.width, r.height].map(Math.round)});
})()"""

OPEN_FMT_JS = "(() => {" + _HELPERS + r"""
  if (fmtRoot() && vis(fmtRoot()) && fmtNums().length >= 7) return 'ALREADY_OPEN';
  const more = document.getElementById('moreButton');
  if (more && vis(more) && more.getAttribute('aria-pressed') !== 'true') click(more);
  const tb = [...document.querySelectorAll("[role=toolbar] [aria-label='Format options'], [role=toolbar] [data-tooltip='Format options']")].find(vis);
  if (tb) { click(tb); return 'CLICKED_TOOLBAR'; }
  const fmt = [...document.querySelectorAll("[role=menubar] [role=menuitem]")].find(e => e.innerText.trim() === 'Format');
  if (!fmt) return 'NO_FORMAT_MENU';
  click(fmt);
  const item = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => vis(e) && e.innerText.trim().startsWith('Format options'));
  if (!item) return 'MENU_OPENED';
  click(item);
  return 'OPENED';
})()"""

CLICK_FMT_ITEM_JS = "(() => {" + _HELPERS + r"""
  if (fmtRoot() && vis(fmtRoot()) && fmtNums().length >= 7) return 'ALREADY_OPEN';
  const item = [...document.querySelectorAll(".goog-menu [role=menuitem]")].find(e => vis(e) && e.innerText.trim().startsWith('Format options'));
  if (!item) { closeMenus(); return 'NO_ITEM'; }
  click(item);
  return 'OPENED';
})()"""

READ_FMT_JS = "(() => {" + _HELPERS + r"""
  return JSON.stringify(Object.assign(readFmt(), {scale: pageScale()}));
})()"""

SET_NUM_JS = "(() => {" + _HELPERS + r"""
  const inp = fmtNums()[__IDX__];
  if (!inp) return 'NO_INPUT';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  inp.focus();
  setter.call(inp, String(__VAL__));
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  ['keydown', 'keyup'].forEach(t => inp.dispatchEvent(new KeyboardEvent(t, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true})));
  inp.blur();
  inp.dispatchEvent(new FocusEvent('focusout', {bubbles: true}));
  return inp.value;
})()"""

CROP_JS = "(() => {" + _HELPERS + r"""
  // The crop edge follows the ABSOLUTE pointer release position (not edge + delta), so we release each
  // handle exactly on the target edge. Target edges keep the current visible box centered (symmetric crop).
  const TARGET_W = __TARGET_W__;   // visible width in Format px (1920x1080 space)
  const more = document.getElementById('moreButton');
  if (more && vis(more) && more.getAttribute('aria-pressed') !== 'true') click(more);
  const c = cropBtn();
  if (!c || /disabled/.test(c.className)) return JSON.stringify({err: 'CROP_UNAVAILABLE'});
  if (c.getAttribute('aria-pressed') !== 'true') click(c);
  const scale = pageScale();
  if (!scale) { exitCrop(); return JSON.stringify({err: 'NO_SCALE'}); }
  const sides = () => [...document.querySelectorAll('svg path')].filter(vis).map(p => ({p, r: p.getBoundingClientRect()}))
    .filter(o => Math.abs(o.r.width - 6) < 1.5 && Math.abs(o.r.height - 24) < 1.5).sort((a, b) => a.r.left - b.r.left);
  const opts = (x, y, up) => ({bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, screenX: x, screenY: y, button: 0, buttons: up ? 0 : 1, pointerId: 1, pointerType: 'mouse', isPrimary: true});
  const move = (t0, x, y0) => {
    for (const el of [document.elementFromPoint(x, y0) || t0, document]) {
      el.dispatchEvent(new PointerEvent('pointermove', opts(x, y0)));
      el.dispatchEvent(new MouseEvent('mousemove', opts(x, y0)));
    }
  };
  // Overshoot 24px past the target so tiny corrections clear the drag-start threshold, then settle exactly.
  const dragTo = (o, xt) => {
    const x0 = o.r.left + o.r.width / 2, y0 = o.r.top + o.r.height / 2;
    const t0 = document.elementFromPoint(x0, y0) || o.p;
    t0.dispatchEvent(new PointerEvent('pointerdown', opts(x0, y0)));
    t0.dispatchEvent(new MouseEvent('mousedown', opts(x0, y0)));
    const over = xt + (xt >= x0 ? 24 : -24);
    for (let i = 1; i <= 8; i++) move(t0, x0 + (over - x0) * i / 8, y0);
    for (let i = 1; i <= 6; i++) move(t0, over + (xt - over) * i / 6, y0);
    for (const el of [document.elementFromPoint(xt, y0) || t0, document]) {
      el.dispatchEvent(new PointerEvent('pointerup', opts(xt, y0, true)));
      el.dispatchEvent(new MouseEvent('mouseup', opts(xt, y0, true)));
    }
  };
  let s = sides();
  if (s.length < 2) { exitCrop(); return JSON.stringify({err: 'NO_HANDLES', n: s.length}); }
  const leftEdge = s[0].r.left, rightEdge = s[s.length - 1].r.right;
  const center = (leftEdge + rightEdge) / 2, half = TARGET_W / 2 * scale;
  const tl = center - half, tr = center + half;
  dragTo(s[0], tl);
  s = sides();
  if (s.length >= 2) dragTo(s[s.length - 1], tr);
  const a = sides();
  const after = a.length >= 2 ? [a[0].r.left, a[a.length - 1].r.right] : [];
  exitCrop();
  return JSON.stringify({ok: true, scale, before: [leftEdge, rightEdge], target: [tl, tr], after});
})()"""

MASK_JS = "(() => {" + _HELPERS + r"""
  const SHAPE = __SHAPE__;
  const more = document.getElementById('moreButton');
  if (more && vis(more) && more.getAttribute('aria-pressed') !== 'true') click(more);
  const btn = document.getElementById('maskVideoMenuButton');
  if (!btn || /disabled/.test(btn.className)) return 'MASK_UNAVAILABLE';
  click(btn);
  const cell = [...document.querySelectorAll('.goog-palette-cell')].find(c => vis(c) && c.getAttribute('aria-label') === SHAPE);
  if (!cell) { click(btn); closeMenus(); return 'NO_CELL'; }
  click(cell);
  return 'APPLIED';
})()"""

# ------------------------------------------------------------------------------------------------
# Geometry
# ------------------------------------------------------------------------------------------------


def _mask_name(clip: Optional[Dict[str, Any]]) -> Optional[str]:
    if not clip:
        return None
    if clip["nC"] and clip["nL"]:
        return "Rounded Rectangle"
    if clip["nC"]:
        return "Oval"
    return "Rectangle"


def read_scene_geometry(doc_id: str) -> List[Dict[str, Any]]:
    """Per-scene avatar geometry (punch units + 1920x1080 Format px) from filmstrip thumbnails."""
    raw = vbf.exec_js(doc_id, GEOMETRY_JS, hooks=False)
    rows = json.loads(raw)
    scenes = []
    for r in rows:
        clips = [c for c in r["clips"] if c.get("bbox") and c["bbox"][2] < UNITS_W - 1]
        entry: Dict[str, Any] = {"scene": r["scene"], "slide_id": r["slideId"], "avatar": None}
        if len(clips) >= 2:
            crop, full = clips[0]["bbox"], clips[1]["bbox"]
            entry["avatar"] = {
                "mask": _mask_name(clips[2] if len(clips) > 2 else None),
                "x": crop[0] / UNITS_W * FMT_W,
                "y": crop[1] / UNITS_H * FMT_H,
                "w": crop[2] / UNITS_W * FMT_W,
                "h": crop[3] / UNITS_H * FMT_H,
                "full_w": full[2] / UNITS_W * FMT_W,
                "full_h": full[3] / UNITS_H * FMT_H,
            }
        scenes.append(entry)
    return scenes


def page_to_scene(scenes: List[Dict[str, Any]]) -> Dict[int, int]:
    """Maps book page -> scene number. 21-scene docs carry a 2.5s bumper as Scene 2 (no avatar)."""
    n = len(scenes)
    if n == 21 and scenes[1]["avatar"] is None:
        return {p: (1 if p == 1 else p + 1) for p in range(1, 21)}
    return {p: p for p in range(1, n + 1)}


def reference_layout(ref_doc_id: str) -> Dict[int, Dict[str, Any]]:
    scenes = read_scene_geometry(ref_doc_id)
    mapping = page_to_scene(scenes)
    layout = {}
    for page, scene in mapping.items():
        av = scenes[scene - 1]["avatar"]
        if av:
            layout[page] = av
    return layout


# ------------------------------------------------------------------------------------------------
# Apply
# ------------------------------------------------------------------------------------------------


def _js(doc_id: str, js: str) -> str:
    return vbf.exec_js(doc_id, js, hooks=True)


def _read_fmt(doc_id: str) -> Dict[str, Any]:
    raw = _js(doc_id, READ_FMT_JS)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _set_num(doc_id: str, field: str, value: float) -> str:
    js = SET_NUM_JS.replace("__IDX__", str(NUM_IDX[field])).replace("__VAL__", json.dumps(round(value, 1)))
    res = _js(doc_id, js)
    time.sleep(0.6)
    return res


def apply_scene(doc_id: str, scene_num: int, target: Dict[str, Any], tol: float = 2.5) -> Dict[str, Any]:
    """Applies `target` (x, y, w, h in Format px + mask) to the avatar on `scene_num`."""
    log: Dict[str, Any] = {"scene": scene_num}
    nav = vbf.navigate_to_scene(doc_id, scene_num)
    log["nav"] = nav
    time.sleep(0.6)
    sel = json.loads(_js(doc_id, SELECT_JS) or "{}")
    if not sel.get("ok"):
        log["error"] = f"select failed: {sel}"
        return log
    time.sleep(0.5)
    open_res = _js(doc_id, OPEN_FMT_JS)
    if open_res == "MENU_OPENED":
        time.sleep(0.35)
        open_res = _js(doc_id, CLICK_FMT_ITEM_JS)
    log["open_fmt"] = open_res
    time.sleep(1.0)
    fmt = _read_fmt(doc_id)
    if not fmt.get("height"):
        log["error"] = f"format options unreadable: {fmt}"
        return log
    log["before"] = {k: fmt.get(k) for k in ("width", "height", "x", "y")}

    # 1) Scale (aspect locked): match visible height (no vertical crop in our layouts)
    if abs(fmt["height"] - target["h"]) > tol:
        _set_num(doc_id, "height", target["h"])
        fmt = _read_fmt(doc_id)

    # 2) Symmetric crop until visible width matches (absolute edge targeting + residual compensation)
    log["crop"] = []
    aim = target["w"]
    for _ in range(3):
        dw = fmt["width"] - target["w"]
        if abs(dw) <= tol:
            break
        res = _js(doc_id, CROP_JS.replace("__TARGET_W__", f"{aim:.2f}"))
        time.sleep(0.8)
        _js(doc_id, SELECT_JS)
        time.sleep(0.4)
        fmt = _read_fmt(doc_id)
        log["crop"].append({"aim": round(aim, 1), "res": res, "width_now": fmt.get("width")})
        # Compensate any systematic residual (pixel rounding / handle bias) on the next pass
        aim += target["w"] - fmt["width"]

    # 3) Mask shape
    if target.get("mask"):
        log["mask"] = _js(doc_id, MASK_JS.replace("__SHAPE__", json.dumps(target["mask"])))
        time.sleep(0.6)

    # 4) Position (top-left of visible box)
    _set_num(doc_id, "x", target["x"])
    _set_num(doc_id, "y", target["y"])
    fmt = {}
    for _ in range(3):
        fmt = _read_fmt(doc_id)
        if fmt.get("width"):
            break
        time.sleep(1.0)
    log["after"] = {k: fmt.get(k) for k in ("width", "height", "x", "y")}
    if not fmt.get("width"):
        log["error"] = "format options unreadable after apply (will re-verify)"
        return log
    log["delta"] = {
        "w": round(fmt["width"] - target["w"], 1),
        "h": round(fmt["height"] - target["h"], 1),
        "x": round(fmt["x"] - target["x"], 1),
        "y": round(fmt["y"] - target["y"], 1),
    }
    return log


def verify(doc_id: str, ref: Dict[int, Dict[str, Any]], pages: List[int], tol: float = 3.0) -> List[Dict[str, Any]]:
    scenes = read_scene_geometry(doc_id)
    mapping = page_to_scene(scenes)
    report = []
    for p in pages:
        if p not in ref or p not in mapping:
            continue
        av = scenes[mapping[p] - 1]["avatar"]
        t = ref[p]
        if not av:
            report.append({"page": p, "ok": False, "err": "no avatar"})
            continue
        d = {k: round(av[k] - t[k], 1) for k in ("x", "y", "w", "h")}
        ok = all(abs(v) <= tol for v in d.values()) and av["mask"] == t["mask"]
        report.append({"page": p, "scene": mapping[p], "ok": ok, "delta": d, "mask": av["mask"]})
    return report


def _parse_pages(spec: str) -> List[int]:
    pages: List[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            pages.extend(range(int(a), int(b) + 1))
        elif part.strip():
            pages.append(int(part))
    return pages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reference", default="", help="Reference Vids doc id (approved layout, open in a background tab).")
    ap.add_argument("--reference-json", default="", help="Saved reference layout (from --save-reference) instead of a live doc.")
    ap.add_argument("--save-reference", default="", help="Write the live reference layout to this JSON file.")
    ap.add_argument("--targets", nargs="*", default=[], help="Target Vids doc ids (open in background tabs).")
    ap.add_argument("--pages", default="1-20", help="Book pages to apply (default 1-20).")
    ap.add_argument("--uniform-page", type=int, default=0, help="Apply this reference page's layout to every page.")
    ap.add_argument("--dump", action="store_true", help="Print the reference layout and exit.")
    ap.add_argument("--verify-only", action="store_true", help="Only compare targets against the reference.")
    ap.add_argument("--log", default="", help="Append JSONL progress records to this file.")
    args = ap.parse_args()

    if args.reference_json:
        ref = {int(k): v for k, v in json.load(open(args.reference_json, encoding="utf-8")).items()}
    elif args.reference:
        ref = reference_layout(args.reference)
    else:
        ap.error("--reference or --reference-json is required")
    if args.save_reference:
        with open(args.save_reference, "w", encoding="utf-8") as f:
            json.dump(ref, f, indent=1)
    if args.uniform_page:
        ref = {p: dict(ref[args.uniform_page]) for p in range(1, 21)}
    if args.dump:
        print(json.dumps(ref, indent=1))
        return 0
    pages = _parse_pages(args.pages)

    def emit(rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        print(line, flush=True)
        if args.log:
            with open(args.log, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")

    for doc_id in args.targets:
        try:
            emit(fix_doc(doc_id, ref, pages, emit=emit, verify_only=args.verify_only))
        except Exception as exc:  # tab closed / became active / editor reloading
            emit({"doc": doc_id, "verified": 0, "failed": [], "error": f"{type(exc).__name__}: {exc}"})
    return 0


def fix_doc(
    doc_id: str,
    ref: Dict[int, Dict[str, Any]],
    pages: List[int],
    emit: Callable[[Dict[str, Any]], None] = lambda rec: None,
    verify_only: bool = False,
) -> Dict[str, Any]:
    """Verifies `doc_id` against `ref`, re-applies the layout on mismatching pages (up to 3 passes), re-verifies.

    Returns {"doc", "verified", "failed"}; `emit` receives one record per applied scene.
    """
    if not verify_only:
        for attempt in range(3):
            todo = [r["page"] for r in verify(doc_id, ref, pages) if not r["ok"] and r.get("err") != "no avatar"]
            if not todo:
                break
            mapping = page_to_scene(read_scene_geometry(doc_id))
            for p in todo:
                res = apply_scene(doc_id, mapping[p], ref[p])
                res.update({"doc": doc_id, "page": p, "attempt": attempt + 1})
                emit(res)
    rep = verify(doc_id, ref, pages)
    bad = [r for r in rep if not r["ok"]]
    return {"doc": doc_id, "verified": len(rep) - len(bad), "failed": bad}


if __name__ == "__main__":
    raise SystemExit(main())
