"""Estimate (or write) KDP-optimized English EPUBs: re-encode page images so the EPUB stays under a size target.

KDP only allows the $0.99 list price at 35% royalty when the converted file is <= 3 MB. The source EPUBs
are 4.2-6.2 MB because each of the 20 pages is a ~250 KB 1024px JPEG, so the JPEGs are re-encoded at the
highest quality that fits (same pixels, same layout). The source EPUB is always the original one in
output/<folder>/, never a previously optimized file, so re-running never compresses twice.

Usage:
    python kdp_epub_optimize.py estimate [--only lily-robo-,mmb-01] [--target 2.6]   # no files written
    python kdp_epub_optimize.py write    [--only lily-robo-,mmb-01] [--target 2.6]   # kdp/epubs/<slug>.epub
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

from PIL import Image

from kdp_listings import KDP_EPUBS_DIR, LISTINGS_DIR, OUTPUT_DIR, _find_epub

TARGET_MB = 2.6  # EPUB size; KDP's "file size after conversion" must stay <= 3 MB for the $0.99 price
QUALITIES = [85, 82, 80, 78, 75, 72, 70]


def _encode_pil(im: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
    return buf.getvalue()


def _reencode(data: bytes, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as src_im:
        rgb = src_im.convert("RGB")
    return _encode_pil(rgb, quality)


def _load_epub_entries(src: Path) -> tuple[bytes, list[tuple[zipfile.ZipInfo, Image.Image | bytes]]]:
    """Read source EPUB once (closing handle) and decode JPEG pages into RGB PIL images in memory."""
    entries: list[tuple[zipfile.ZipInfo, Image.Image | bytes]] = []
    with zipfile.ZipFile(src, "r") as zin:
        mimetype_bytes = zin.read("mimetype")
        for info in zin.infolist():
            if info.filename == "mimetype":
                continue
            data = zin.read(info)
            if info.filename.lower().endswith((".jpg", ".jpeg")):
                with Image.open(io.BytesIO(data)) as src_im:
                    entries.append((info, src_im.convert("RGB")))
            else:
                entries.append((info, data))
    return mimetype_bytes, entries


def _build_from_entries(
    mimetype_bytes: bytes,
    entries: list[tuple[zipfile.ZipInfo, Image.Image | bytes]],
    quality: int,
) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        # EPUB spec: 'mimetype' must be the first entry, stored uncompressed
        zout.writestr(zipfile.ZipInfo("mimetype"), mimetype_bytes, compress_type=zipfile.ZIP_STORED)
        for info, payload in entries:
            out_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out_info.compress_type = zipfile.ZIP_DEFLATED
            out_info.external_attr = info.external_attr
            raw = _encode_pil(payload, quality) if isinstance(payload, Image.Image) else payload
            zout.writestr(out_info, raw, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def _close_entry_images(entries: list[tuple[zipfile.ZipInfo, Image.Image | bytes]]) -> None:
    for _, payload in entries:
        if isinstance(payload, Image.Image):
            payload.close()


def _build(src: Path, quality: int) -> bytes:
    mimetype_bytes, entries = _load_epub_entries(src)
    try:
        return _build_from_entries(mimetype_bytes, entries, quality)
    finally:
        _close_entry_images(entries)


def plan_for(src: Path, target_mb: float) -> tuple[int, bytes]:
    """Find highest JPEG quality in QUALITIES whose resulting EPUB fits <= target_mb using binary search."""
    mimetype_bytes, entries = _load_epub_entries(src)
    try:
        cache: dict[int, bytes] = {}

        def build_at(idx: int) -> bytes:
            if idx not in cache:
                cache[idx] = _build_from_entries(mimetype_bytes, entries, QUALITIES[idx])
            return cache[idx]

        # Fast path: highest quality already fits
        first = build_at(0)
        if len(first) / 1e6 <= target_mb:
            return QUALITIES[0], first

        lo, hi = 1, len(QUALITIES) - 1
        best_idx: int | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            blob = build_at(mid)
            if len(blob) / 1e6 <= target_mb:
                best_idx = mid
                hi = mid - 1  # try higher quality (smaller index)
            else:
                lo = mid + 1  # need stronger compression (larger index)

        if best_idx is not None:
            return QUALITIES[best_idx], cache[best_idx]
        last_idx = len(QUALITIES) - 1
        return QUALITIES[last_idx], build_at(last_idx)
    finally:
        _close_entry_images(entries)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["estimate", "write"], nargs="?", default="estimate")
    ap.add_argument("--only", default="", help="Comma-separated slug prefixes")
    ap.add_argument("--target", type=float, default=TARGET_MB, help="Max EPUB size in MB (default %(default)s)")
    args = ap.parse_args(argv)
    prefixes = [p for p in args.only.split(",") if p]
    rows = []
    for p in sorted(LISTINGS_DIR.glob("*.json")):
        listing = json.loads(p.read_text(encoding="utf-8"))
        if prefixes and not any(listing["slug"].startswith(x) for x in prefixes):
            continue
        src = _find_epub(OUTPUT_DIR / listing["folder"])
        q, blob = plan_for(src, args.target)
        row = {"slug": listing["slug"], "original_mb": round(src.stat().st_size / 1e6, 2),
               "quality": q, "optimized_mb": round(len(blob) / 1e6, 2), "fits": len(blob) / 1e6 <= args.target}
        if args.mode == "write":
            KDP_EPUBS_DIR.mkdir(parents=True, exist_ok=True)
            dst = KDP_EPUBS_DIR / f"{listing['slug']}.epub"
            dst.write_bytes(blob)
            row["written"] = str(dst)
        rows.append(row)
        print(json.dumps(row))
    if not rows:
        print("No listings matched")
        return 1
    worst = max(rows, key=lambda r: r["optimized_mb"])
    print(f"Lowest quality used: {min(r['quality'] for r in rows)}; largest EPUB: {worst['optimized_mb']} MB ({worst['slug']})")
    return 0 if all(r["fits"] for r in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
