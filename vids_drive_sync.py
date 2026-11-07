"""Syncs a book's Google Vids package to Drive and records it in the book manifest (no re-rendering).

- Converts each `<title>_Google_Vids_<LANG>.pptx` into a native Google Slides import deck (upsert by name, so
  existing deck ids/links are kept) and mirrors the .pptx + script .md into the `google_vids` subfolder.
- Uploads localized PDFs / EPUBs.
- Moves the book's Vids projects (from `google_vids/vids_projects.json`) into the book's Drive folder.
- Writes `manifest.json["google_vids"]` (decks, scripts, localized files, Slides + Vids URLs) and `drive_folder_id`.

Usage: python vids_drive_sync.py --book-dir "output/Long Running Agents" [--folder-id ID]
"""

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional

import config
import drive_sync

LANGS = ["en", "es", "zh", "yue", "pt", "ja", "ko", "fr", "it", "de"]
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def _read_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def sync_book(book_dir: str, folder_id: str = "", vids_only: bool = False, langs: Optional[List[str]] = None) -> Dict[str, Any]:
    svc = drive_sync.init_drive_service(no_drive=False)
    if not svc:
        raise RuntimeError("Drive ADC unavailable - run `gcloud auth application-default login` with the drive scope.")

    story = _read_json_file(os.path.join(book_dir, "story.json"))
    title = story["title"]
    safe_title = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip()
    vids_dir = os.path.join(book_dir, "google_vids")
    manifest_path = os.path.join(book_dir, "manifest.json")
    manifest = _read_json_file(manifest_path)
    gv = manifest.get("google_vids", {})

    if not folder_id:
        root = drive_sync.get_or_create_folder(svc, config.DRIVE_ROOT_FOLDER_NAME)
        folder_id = drive_sync.get_or_create_folder(svc, os.path.basename(os.path.normpath(book_dir)), parent_id=root)
    vids_sub = drive_sync.get_or_create_folder(svc, "google_vids", parent_id=folder_id)

    book_index = None if vids_only else drive_sync.list_folder_index(svc, folder_id)
    vids_index = None if vids_only else drive_sync.list_folder_index(svc, vids_sub)

    decks, scripts, pdfs, epubs = {}, {}, {}, {}
    slides_urls = dict(gv.get("drive_slides_urls") or {})
    selected_langs = [lang_code for lang_code in LANGS if not langs or lang_code in langs]
    for lang in selected_langs:
        up = lang.upper()
        pptx = os.path.join(vids_dir, f"{safe_title}_Google_Vids_{up}.pptx")
        md = os.path.join(vids_dir, f"{safe_title}_Google_Vids_Script_{up}.md")
        if not os.path.exists(pptx):
            continue
        decks[lang], scripts[lang] = pptx, md
        pdf = os.path.join(book_dir, f"{safe_title}_{up}.pdf")
        epub = os.path.join(book_dir, f"{safe_title}_{up} (eBook).epub")
        if lang != "en" and os.path.exists(pdf):
            pdfs[lang] = pdf
        if lang != "en" and os.path.exists(epub):
            epubs[lang] = epub
        if vids_only:
            continue
        with open(pptx, "rb") as f:
            data = f.read()
        deck_id = drive_sync.upsert_bytes_to_drive(
            svc,
            data,
            f"{title} ({up}) — Google Vids Import Deck",
            folder_id,
            PPTX_MIME,
            target_mime_type=SLIDES_MIME,
            folder_index=book_index,
        )
        slides_urls[lang] = f"https://docs.google.com/presentation/d/{deck_id}/edit"
        drive_sync.upsert_local_file(svc, pptx, vids_sub, folder_index=vids_index)
        if os.path.exists(md):
            drive_sync.upsert_local_file(svc, md, vids_sub, folder_index=vids_index)
        for path in (pdfs.get(lang), epubs.get(lang)):
            if path:
                drive_sync.upsert_local_file(svc, path, folder_id, folder_index=book_index)
        print(f"[{up}] Slides deck: {slides_urls[lang]}", flush=True)

    # Move Vids projects into the book folder and collect share links
    projects_path = os.path.join(vids_dir, "vids_projects.json")
    projects = _read_json_file(projects_path)
    vids_urls = {}
    for lang, info in projects.items():
        if langs and lang not in langs:
            continue
        try:
            f = svc.files().get(fileId=info["id"], fields="id,parents,webViewLink,resourceKey", supportsAllDrives=True).execute()
            parents = f.get("parents", [])
            if folder_id not in parents:
                svc.files().update(
                    fileId=info["id"], addParents=folder_id, removeParents=",".join(parents), fields="id,parents", supportsAllDrives=True
                ).execute()
            rk = f.get("resourceKey")
            vids_urls[lang] = f"https://docs.google.com/videos/d/{info['id']}/edit" + (f"?resourcekey={rk}" if rk else "")
        except Exception as exc:
            print(f"[{lang.upper()}] Vids Drive move skipped ({exc}); keeping direct URL", flush=True)
            vids_urls[lang] = info.get("url") or f"https://docs.google.com/videos/d/{info['id']}/edit"
        print(f"[{lang.upper()}] Vids: {vids_urls[lang]}", flush=True)
    if vids_urls:
        # Re-read right before writing: pipeline workers may have added projects meanwhile; only touch `url`.
        cur = _read_json_file(projects_path)
        for lang, url in vids_urls.items():
            if lang in cur:
                cur[lang]["url"] = url
        with open(projects_path, "w", encoding="utf-8") as fp:
            json.dump(cur, fp, ensure_ascii=False, indent=2)

    gv.update({
        "title": title,
        **{
            f"title_{lang_code}": story.get(f"title_{lang_code}")
            for lang_code in LANGS
            if lang_code != "en" and story.get(f"title_{lang_code}")
        },
        "book_dir": book_dir,
        "vids_dir": vids_dir,
        # merge so a per-language sync (--langs) never drops the other languages' entries
        "decks": {**(gv.get("decks") or {}), **decks},
        "scripts": {**(gv.get("scripts") or {}), **scripts},
        "localized_pdfs": {**(gv.get("localized_pdfs") or {}), **pdfs},
        "localized_epubs": {**(gv.get("localized_epubs") or {}), **epubs},
        "drive_slides_urls": slides_urls,
        "drive_vids_urls": {**(gv.get("drive_vids_urls") or {}), **vids_urls},
    })
    manifest["google_vids"] = gv
    manifest["drive_folder_id"] = folder_id
    with open(manifest_path, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
    print(f"Manifest updated: {manifest_path} (folder {folder_id})", flush=True)
    return gv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--book-dir", required=True)
    ap.add_argument("--folder-id", default="")
    ap.add_argument("--vids-only", action="store_true", help="Only move Vids projects + refresh links/manifest (no uploads).")
    ap.add_argument("--langs", nargs="*", default=None, help="Only sync these languages (default: all).")
    args = ap.parse_args()
    sync_book(args.book_dir, args.folder_id, vids_only=args.vids_only, langs=args.langs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
