"""Print-ready 300 DPI PDF and fixed-layout EPUB 3.0 packaging and validation."""

import html
import io
import logging
import os
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from PIL import Image

import config
import prompts

logger = logging.getLogger(__name__)

EPUB_LANGUAGE_CODES: dict[str, str] = {
    "English": "en",
    "Spanish": "es",
    "Chinese Simplified": "zh",
    "Cantonese": "zh-HK",
    "French": "fr",
    "Portuguese": "pt",
    "Japanese": "ja",
    "Korean": "ko",
    "German": "de",
    "Italian": "it",
}


def compile_pdf_bytes(
    pil_images: list[Image.Image],
    dpi: float = config.PDF_DPI,
    upscale_for_print: bool = False,
) -> bytes:
    """Compiles PIL images into a print-ready PDF with proper DPI metadata (default 300 DPI)."""
    if not pil_images:
        return b""
    processed = pil_images
    if upscale_for_print:
        target_px = int(config.PDF_TRIM_INCHES * dpi)
        processed = [
            img.resize((target_px, target_px), Image.Resampling.LANCZOS)
            if img.size != (target_px, target_px)
            else img
            for img in pil_images
        ]
    pdf_buffer = io.BytesIO()
    processed[0].save(
        pdf_buffer,
        format="PDF",
        save_all=True,
        append_images=processed[1:],
        resolution=dpi,
    )
    return pdf_buffer.getvalue()


def create_epub(
    title: str,
    pages_data: list[tuple[str, bytes]],
    language: str,
    author: str = prompts.SERIES_AUTHOR,
) -> bytes:
    """Creates a valid EPUB 3.0 fixed-layout eBook with nav document, dcterms:modified, and XML escaping."""
    logger.info(f"Creating KDP-compatible fixed-layout EPUB 3 for '{title}'...")
    epub_buf = io.BytesIO()
    book_id = str(uuid.uuid4())
    modified_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lang_code = EPUB_LANGUAGE_CODES.get(language, "en")
    escaped_title = html.escape(title or "Curious Little Minds Storybook")
    escaped_author = html.escape(author)

    with zipfile.ZipFile(epub_buf, "w", zipfile.ZIP_DEFLATED) as epub:
        # 1. mimetype MUST be the first entry and uncompressed (ZIP_STORED)
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. META-INF/container.xml
        container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="http://www.oasis-open.org/names/tc/opendocument/xmlns/container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        epub.writestr("META-INF/container.xml", container_xml)

        manifest_items = [
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine_items = []
        nav_links = []

        for idx, (filename, img_bytes) in enumerate(pages_data):
            page_num = idx + 1
            img_href = f"images/{html.escape(filename)}"
            page_href = f"page_{page_num:02d}.xhtml"

            epub.writestr(f"OEBPS/images/{filename}", img_bytes)

            page_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang_code}">
<head>
  <title>{escaped_title} - Page {page_num}</title>
  <meta name="viewport" content="width=1024,height=1024"/>
  <style>
    body {{ margin: 0; padding: 0; background-color: #FFFFFF; }}
    img {{ width: 1024px; height: 1024px; display: block; }}
  </style>
</head>
<body>
  <div>
    <img src="{img_href}" alt="Page {page_num}"/>
  </div>
</body>
</html>"""
            epub.writestr(f"OEBPS/{page_href}", page_xhtml)

            img_props = ' properties="cover-image"' if page_num == 1 else ""
            manifest_items.append(
                f'    <item id="page_{page_num:02d}" href="{page_href}" media-type="application/xhtml+xml"/>'
            )
            manifest_items.append(
                f'    <item id="img_{page_num:02d}" href="{img_href}" media-type="image/jpeg"{img_props}/>'
            )
            spine_items.append(f'    <itemref idref="page_{page_num:02d}"/>')
            nav_links.append(f'        <li><a href="{page_href}">Page {page_num}</a></li>')

        nav_links_str = "\n".join(nav_links)
        nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang_code}">
<head>
  <title>{escaped_title} - Navigation</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{escaped_title}</h1>
    <ol>
{nav_links_str}
    </ol>
  </nav>
</body>
</html>"""
        epub.writestr("OEBPS/nav.xhtml", nav_xhtml)

        manifest_str = "\n".join(manifest_items)
        spine_str = "\n".join(spine_items)

        content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0" xml:lang="{lang_code}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{escaped_title}</dc:title>
    <dc:creator>{escaped_author}</dc:creator>
    <dc:language>{lang_code}</dc:language>
    <meta property="dcterms:modified">{modified_utc}</meta>
    <meta property="rendition:layout">pre-paginated</meta>
    <meta property="rendition:orientation">auto</meta>
    <meta property="rendition:spread">none</meta>
  </metadata>
  <manifest>
{manifest_str}
  </manifest>
  <spine>
{spine_str}
  </spine>
</package>"""
        epub.writestr("OEBPS/content.opf", content_opf)

    return epub_buf.getvalue()


def validate_epub_bytes(epub_bytes: bytes, epub_path: Optional[str] = None) -> dict[str, Any]:
    """Validates EPUB 3 package invariants and optionally runs `epubcheck` CLI if available."""
    errors: list[str] = []
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        names = zf.namelist()
        if not names or names[0] != "mimetype":
            errors.append("First entry in EPUB zip must be 'mimetype'.")
        else:
            info = zf.getinfo("mimetype")
            if info.compress_type != zipfile.ZIP_STORED:
                errors.append("'mimetype' entry must be uncompressed (ZIP_STORED).")
            if zf.read("mimetype") != b"application/epub+zip":
                errors.append("Invalid mimetype payload.")

        for required in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
            if required not in names:
                errors.append(f"Missing required EPUB 3 file: {required}")

        for name in names:
            if name.endswith((".xml", ".opf", ".xhtml")):
                try:
                    ET.fromstring(zf.read(name))
                except ET.ParseError as e:
                    errors.append(f"Malformed XML in {name}: {e}")

        if "OEBPS/content.opf" in names:
            opf_text = zf.read("OEBPS/content.opf").decode("utf-8", errors="replace")
            if 'property="dcterms:modified"' not in opf_text:
                errors.append('Missing required <meta property="dcterms:modified"> in content.opf.')
            if 'properties="nav"' not in opf_text:
                errors.append('Missing required nav item with properties="nav" in content.opf.')
            if 'properties="cover-image"' not in opf_text:
                errors.append("Missing cover-image item in content.opf.")

    epubcheck_bin = shutil.which("epubcheck")
    if epubcheck_bin and epub_path and os.path.exists(epub_path):
        res = subprocess.run([epubcheck_bin, epub_path], capture_output=True, text=True)
        if res.returncode != 0:
            errors.append(f"epubcheck failed: {res.stderr or res.stdout}")

    return {"valid": len(errors) == 0, "errors": errors}
