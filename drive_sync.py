"""Unified, idempotent Google Drive synchronization utilities for Story-Agent."""

import io
import logging
import mimetypes
import os
from typing import Optional

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

import config

logger = logging.getLogger(__name__)


def init_drive_service(no_drive: bool = False):
    """Safely initializes Google Drive API v3 service using ADC."""
    if no_drive:
        logger.info("Running in local-only mode (--no-drive specified). Skipping Google Drive authentication.")
        return None

    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive"])
        drive_service = build("drive", "v3", credentials=credentials)
        drive_service.files().list(pageSize=1, fields="files(id)").execute()
        logger.info("Google Drive service initialized successfully via ADC.")
        return drive_service
    except Exception as e:
        logger.warning(
            f"Google Drive ADC authentication unavailable ({e}). "
            "Continuing in local-only mode; all files will be saved in the output directory."
        )
        return None


def get_or_create_folder(
    drive_service,
    folder_name: str,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    """Gets an existing Drive folder by name (and optional parent) or creates a new one."""
    if not drive_service:
        return None

    escaped_name = folder_name.replace("'", "\\'")
    query = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    try:
        results = drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            existing_id = files[0]["id"]
            logger.info(f"Reusing existing folder '{folder_name}' (ID: {existing_id})")
            return existing_id
    except Exception as e:
        logger.warning(f"Error checking folder existence for '{folder_name}': {e}")

    logger.info(f"Creating folder '{folder_name}' in Google Drive...")
    file_metadata: dict[str, object] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        file_metadata["parents"] = [parent_id]

    folder = drive_service.files().create(body=file_metadata, fields="id").execute()
    return folder.get("id")


def find_existing_file_id(drive_service, filename: str, folder_id: str) -> Optional[str]:
    """Finds an existing file by name inside a Drive folder if list() is configured."""
    if not drive_service or not folder_id:
        return None
    try:
        escaped_name = filename.replace("'", "\\'")
        query = f"name = '{escaped_name}' and '{folder_id}' in parents and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id)").execute()
        if not isinstance(results, dict):
            return None
        files = results.get("files", [])
        if isinstance(files, list) and files and isinstance(files[0], dict):
            return files[0].get("id")
    except Exception:
        pass
    return None


def upsert_bytes_to_drive(
    drive_service,
    data_bytes: bytes,
    filename: str,
    folder_id: str,
    mimetype: str,
    target_mime_type: Optional[str] = None,
    fields: str = "id",
    overwrite_existing: bool = True,
) -> str:
    """Uploads or updates in-memory bytes in a Google Drive folder without creating duplicates."""
    media = MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True)
    existing_id = find_existing_file_id(drive_service, filename, folder_id) if overwrite_existing else None

    if existing_id:
        logger.info(f"Updating existing file '{filename}' (ID: {existing_id}) in Google Drive...")
        updated = (
            drive_service.files()
            .update(fileId=existing_id, media_body=media, fields=fields)
            .execute()
        )
        return updated.get("id", existing_id)

    logger.info(f"Uploading '{filename}' to Google Drive...")
    file_metadata: dict[str, object] = {
        "name": filename,
        "parents": [folder_id],
    }
    if target_mime_type:
        file_metadata["mimeType"] = target_mime_type

    created = (
        drive_service.files()
        .create(body=file_metadata, media_body=media, fields=fields)
        .execute()
    )
    return created.get("id")


def infer_mime_type(local_file_path: str) -> str:
    """Infers MIME type for storybook artifacts."""
    filename = os.path.basename(local_file_path)
    content_type, _ = mimetypes.guess_type(local_file_path)
    if content_type:
        return content_type
    if filename.endswith(".epub"):
        return "application/epub+zip"
    if filename.endswith(".mp3"):
        return "audio/mpeg"
    if filename.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def upsert_local_file(
    drive_service,
    local_file_path: str,
    folder_id: str,
    overwrite_existing: bool = True,
) -> str:
    """Uploads or updates a local file in a Google Drive folder."""
    filename = os.path.basename(local_file_path)
    content_type = infer_mime_type(local_file_path)
    existing_id = find_existing_file_id(drive_service, filename, folder_id) if overwrite_existing else None
    media = MediaFileUpload(local_file_path, mimetype=content_type, resumable=True)

    if existing_id:
        logger.info(f"Updating existing '{filename}' in folder (ID: {existing_id})...")
        updated = (
            drive_service.files()
            .update(fileId=existing_id, media_body=media, fields="id")
            .execute()
        )
        return updated.get("id", existing_id)

    logger.info(f"Uploading '{filename}' ({content_type})...")
    file_metadata = {"name": filename, "parents": [folder_id]}
    uploaded = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    logger.info(f"Uploaded '{filename}' (ID: {uploaded.get('id')})")
    return uploaded.get("id")


def sync_directory_to_drive(
    local_dir: str,
    root_folder_name: str = config.DRIVE_ROOT_FOLDER_NAME,
    drive_service=None,
    overwrite_existing: bool = True,
) -> Optional[str]:
    """Synchronizes all files in a local output directory to Google Drive."""
    if not os.path.isdir(local_dir):
        logger.error(f"Directory not found: {local_dir}")
        return None

    drive = drive_service or init_drive_service(no_drive=False)
    if not drive:
        logger.error(
            "Cannot connect to Google Drive. Please run:\n"
            "  gcloud auth login --enable-gdrive-access --update-adc\n"
            "in your terminal to grant Google Drive permissions."
        )
        return None

    root_id = get_or_create_folder(drive, root_folder_name)
    subfolder_name = os.path.basename(os.path.normpath(local_dir))
    book_folder_id = get_or_create_folder(drive, subfolder_name, parent_id=root_id)
    if not book_folder_id:
        return None

    files = sorted(os.listdir(local_dir))
    logger.info(f"Found {len(files)} entries in '{local_dir}' to sync to Google Drive...")
    for fname in files:
        full_path = os.path.join(local_dir, fname)
        if os.path.isfile(full_path):
            upsert_local_file(drive, full_path, book_folder_id, overwrite_existing=overwrite_existing)

    drive_url = f"https://drive.google.com/drive/folders/{book_folder_id}"
    logger.info("=" * 60)
    logger.info("ALL ASSETS SYNCED TO GOOGLE DRIVE!")
    logger.info(f"Folder URL: {drive_url}")
    logger.info("=" * 60)
    return drive_url
