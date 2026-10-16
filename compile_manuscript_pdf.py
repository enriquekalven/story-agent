import argparse
import io
import logging
import os

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image

import config
import drive_sync
from storybook import StorybookGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def compile_pdf(
    folder_id: str | None = None,
    pdf_filename: str = "The Little Helpers That Never Sleep - Print Manuscript.pdf",
):
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive"])
    service = build("drive", "v3", credentials=credentials)

    target_folder_id = folder_id or os.environ.get(
        "STORY_AGENT_MANUSCRIPT_FOLDER_ID", config.DRIVE_PORTFOLIO_FOLDER_ID
    )

    query = f"'{target_folder_id}' in parents and mimeType = 'image/jpeg' and trashed = false"
    logger.info("Retrieving list of images in the folder...")
    results = service.files().list(q=query, pageSize=100, fields="files(id, name)").execute()

    files = results.get("files", [])
    page_files = sorted(
        [f for f in files if f["name"].startswith("page_") and f["name"].endswith(".jpg")],
        key=lambda x: x["name"],
    )

    if not page_files:
        logger.error("No page_XX.jpg files found in the folder!")
        return None

    logger.info(f"Found {len(page_files)} pages. Downloading and converting...")

    pil_images = []
    for f in page_files:
        name = f["name"]
        file_id = f["id"]
        logger.info(f"Downloading {name}...")

        request = service.files().get_media(fileId=file_id)
        img_buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(img_buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        img_buffer.seek(0)
        img = Image.open(img_buffer).convert("RGB")
        pil_images.append(img)

    if not pil_images:
        logger.error("No images downloaded successfully.")
        return None

    logger.info("Compiling pages into a single 300 DPI PDF manuscript...")
    pdf_bytes = StorybookGenerator.compile_pdf_bytes(pil_images, dpi=config.PDF_DPI)

    logger.info(f"Uploading compiled PDF '{pdf_filename}'...")
    uploaded_id = drive_sync.upsert_bytes_to_drive(
        service,
        pdf_bytes,
        pdf_filename,
        target_folder_id,
        mimetype="application/pdf",
        fields="id",
    )
    logger.info(f"Successfully compiled and uploaded print manuscript PDF! File ID: {uploaded_id}")
    return uploaded_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile Drive page JPEGs into a 300 DPI print PDF.")
    parser.add_argument("--folder-id", default=None, help="Google Drive folder ID containing page_XX.jpg")
    parser.add_argument(
        "--output-name",
        default="The Little Helpers That Never Sleep - Print Manuscript.pdf",
        help="Output PDF filename in Google Drive",
    )
    cli_args = parser.parse_args()
    compile_pdf(folder_id=cli_args.folder_id, pdf_filename=cli_args.output_name)
