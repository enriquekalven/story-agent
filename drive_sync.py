"""Unified, idempotent Google Drive synchronization utilities for Story-Agent."""

import hashlib
import io
import logging
import mimetypes
import os
import re
import time
from typing import Any, Callable, Dict, Optional, TypeVar

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

import config

logger = logging.getLogger(__name__)

T = TypeVar("T")
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def _escape_drive_q_value(value: str) -> str:
    """Escapes backslashes and single quotes for Google Drive API v3 `q` strings."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_retryable_drive_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
        return True
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        return status in (408, 429, 500, 502, 503, 504)
    msg = str(exc).lower()
    return "timed out" in msg or "connection reset" in msg or "ssl" in msg


def _execute_with_retry(op: Callable[[], T], retries: int = 3, base_delay: float = 1.0) -> T:
    """Executes a Drive API operation with exponential backoff on transient network/5xx errors."""
    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            return op()
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= retries or not _is_retryable_drive_error(exc):
                raise
            delay = base_delay * (2**attempt)
            logger.warning(f"Transient Drive error ({exc}); retrying in {delay:.1f}s ({attempt + 1}/{retries})...")
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


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

    escaped_name = _escape_drive_q_value(folder_name)
    query = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        escaped_parent = _escape_drive_q_value(parent_id)
        query += f" and '{escaped_parent}' in parents"

    try:
        results = _execute_with_retry(lambda: drive_service.files().list(q=query, fields="files(id)").execute())
        if isinstance(results, dict):
            files = results.get("files", [])
            if isinstance(files, list) and files and isinstance(files[0], dict):
                existing_id = files[0].get("id")
                if existing_id:
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

    folder = _execute_with_retry(lambda: drive_service.files().create(body=file_metadata, fields="id").execute())
    return folder.get("id")


def list_folder_index(drive_service, folder_id: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Lists all non-trashed items in a Drive folder once, mapping filename -> metadata dict.

    Returns None when drive_service is unconfigured or mocked without a dict response.
    """
    if not drive_service or not folder_id:
        return None
    try:
        escaped_parent = _escape_drive_q_value(folder_id)
        query = f"'{escaped_parent}' in parents and trashed = false"
        index: Dict[str, Dict[str, Any]] = {}
        page_token: Optional[str] = None
        while True:
            resp = _execute_with_retry(
                lambda tok=page_token: (
                    drive_service.files()
                    .list(
                        q=query,
                        pageSize=200,
                        pageToken=tok,
                        fields="nextPageToken, files(id, name, md5Checksum, mimeType)",
                    )
                    .execute()
                )
            )
            if not isinstance(resp, dict):
                return None
            files = resp.get("files", [])
            if not isinstance(files, list):
                return None
            for item in files:
                if isinstance(item, dict) and item.get("name") and item.get("id"):
                    index.setdefault(item["name"], item)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return index
    except Exception:
        return None


def find_existing_file_id(drive_service, filename: str, folder_id: str) -> Optional[str]:
    """Finds an existing file by name inside a Drive folder if list() is configured."""
    if not drive_service or not folder_id:
        return None
    try:
        escaped_name = _escape_drive_q_value(filename)
        escaped_folder = _escape_drive_q_value(folder_id)
        query = f"name = '{escaped_name}' and '{escaped_folder}' in parents and trashed = false"
        results = _execute_with_retry(lambda: drive_service.files().list(q=query, fields="files(id)").execute())
        if not isinstance(results, dict):
            return None
        files = results.get("files", [])
        if isinstance(files, list) and files and isinstance(files[0], dict):
            return files[0].get("id")
    except Exception:
        pass
    return None


def _compute_file_md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upsert_bytes_to_drive(
    drive_service,
    data_bytes: bytes,
    filename: str,
    folder_id: str,
    mimetype: str,
    target_mime_type: Optional[str] = None,
    fields: str = "id",
    overwrite_existing: bool = True,
    folder_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Uploads or updates in-memory bytes in a Google Drive folder without creating duplicates."""
    existing_entry = folder_index.get(filename) if folder_index is not None else None
    existing_id = (
        existing_entry.get("id")
        if existing_entry
        else (find_existing_file_id(drive_service, filename, folder_id) if folder_index is None else None)
    )

    if existing_id:
        if not overwrite_existing:
            logger.info(f"Skipping existing file '{filename}' (ID: {existing_id}) in Google Drive (--no-overwrite).")
            return existing_id
        if (
            not target_mime_type
            and existing_entry
            and existing_entry.get("md5Checksum")
            and existing_entry.get("md5Checksum") == hashlib.md5(data_bytes).hexdigest()
        ):
            logger.debug(f"Unchanged '{filename}' (MD5 match); skipping re-upload.")
            return existing_id
        logger.info(f"Updating existing file '{filename}' (ID: {existing_id}) in Google Drive...")
        updated = _execute_with_retry(
            lambda: (
                drive_service.files()
                .update(
                    fileId=existing_id,
                    media_body=MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True),
                    fields=fields,
                )
                .execute()
            )
        )
        return updated.get("id", existing_id)

    logger.info(f"Uploading '{filename}' to Google Drive...")
    file_metadata: dict[str, object] = {
        "name": filename,
        "parents": [folder_id],
    }
    if target_mime_type:
        file_metadata["mimeType"] = target_mime_type

    created = _execute_with_retry(
        lambda: (
            drive_service.files()
            .create(
                body=file_metadata,
                media_body=MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True),
                fields=fields,
            )
            .execute()
        )
    )
    new_id = created.get("id")
    if folder_index is not None and new_id:
        folder_index[filename] = {
            "id": new_id,
            "name": filename,
            "md5Checksum": None if target_mime_type else hashlib.md5(data_bytes).hexdigest(),
        }
    return new_id


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
    folder_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Uploads or updates a local file in a Google Drive folder."""
    filename = os.path.basename(local_file_path)
    content_type = infer_mime_type(local_file_path)
    existing_entry = folder_index.get(filename) if folder_index is not None else None
    existing_id = (
        existing_entry.get("id")
        if existing_entry
        else (find_existing_file_id(drive_service, filename, folder_id) if folder_index is None else None)
    )

    if existing_id:
        if not overwrite_existing:
            logger.info(f"Skipping existing '{filename}' in folder (ID: {existing_id}) (--no-overwrite).")
            return existing_id
        remote_md5 = existing_entry.get("md5Checksum") if existing_entry else None
        if remote_md5 and remote_md5 == _compute_file_md5(local_file_path):
            logger.debug(f"Unchanged '{filename}' (MD5 match); skipping re-upload.")
            return existing_id
        logger.info(f"Updating existing '{filename}' in folder (ID: {existing_id})...")
        updated = _execute_with_retry(
            lambda: (
                drive_service.files()
                .update(
                    fileId=existing_id,
                    media_body=MediaFileUpload(local_file_path, mimetype=content_type, resumable=True),
                    fields="id",
                )
                .execute()
            )
        )
        return updated.get("id", existing_id)

    logger.info(f"Uploading '{filename}' ({content_type})...")
    file_metadata = {"name": filename, "parents": [folder_id]}
    uploaded = _execute_with_retry(
        lambda: (
            drive_service.files()
            .create(
                body=file_metadata,
                media_body=MediaFileUpload(local_file_path, mimetype=content_type, resumable=True),
                fields="id",
            )
            .execute()
        )
    )
    new_id = uploaded.get("id")
    logger.info(f"Uploaded '{filename}' (ID: {new_id})")
    if folder_index is not None and new_id:
        folder_index[filename] = {
            "id": new_id,
            "name": filename,
            "md5Checksum": _compute_file_md5(local_file_path),
        }
    return new_id


def _sync_folder_recursive(
    drive,
    local_folder: str,
    drive_folder_id: str,
    overwrite_existing: bool,
    book_folder_id: str,
    book_title: Optional[str],
    book_index: Optional[Dict[str, Dict[str, Any]]],
) -> None:
    folder_index = book_index if drive_folder_id == book_folder_id else list_folder_index(drive, drive_folder_id)
    for fname in sorted(os.listdir(local_folder)):
        if fname.startswith(".") or fname == "__pycache__":
            continue
        full_path = os.path.join(local_folder, fname)
        if os.path.isfile(full_path):
            upsert_local_file(
                drive,
                full_path,
                drive_folder_id,
                overwrite_existing=overwrite_existing,
                folder_index=folder_index,
            )
            if fname.endswith(".pptx") and os.path.basename(local_folder) == "google_vids":
                try:
                    m = re.search(r"_Google_Vids_([A-Z]+)\.pptx$", fname)
                    if m and book_title:
                        slides_name = f"{book_title} ({m.group(1)}) — Google Vids Import Deck"
                        target_folder = book_folder_id
                        target_index = book_index
                    else:
                        slides_name = os.path.splitext(fname)[0]
                        target_folder = drive_folder_id
                        target_index = folder_index
                    with open(full_path, "rb") as pf:
                        pptx_bytes = pf.read()
                    upsert_bytes_to_drive(
                        drive,
                        data_bytes=pptx_bytes,
                        filename=slides_name,
                        folder_id=target_folder,
                        mimetype=PPTX_MIME,
                        target_mime_type=SLIDES_MIME,
                        overwrite_existing=overwrite_existing,
                        folder_index=target_index,
                    )
                except Exception as e:
                    logger.warning(f"Could not convert '{fname}' to Google Slides: {e}")
        elif os.path.isdir(full_path):
            sub_id = get_or_create_folder(drive, fname, parent_id=drive_folder_id)
            if sub_id:
                _sync_folder_recursive(
                    drive=drive,
                    local_folder=full_path,
                    drive_folder_id=sub_id,
                    overwrite_existing=overwrite_existing,
                    book_folder_id=book_folder_id,
                    book_title=book_title,
                    book_index=book_index,
                )


def sync_directory_to_drive(
    local_dir: str,
    root_folder_name: str = config.DRIVE_ROOT_FOLDER_NAME,
    drive_service=None,
    overwrite_existing: bool = True,
) -> Optional[str]:
    """Synchronizes all files (recursively) in a local output directory to Google Drive."""
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

    book_title: Optional[str] = None
    story_path = os.path.join(local_dir, "story.json")
    if os.path.isfile(story_path):
        try:
            import json

            with open(story_path, "r", encoding="utf-8") as sf:
                book_title = json.load(sf).get("title")
        except Exception:
            book_title = None

    book_index = list_folder_index(drive, book_folder_id)
    logger.info(f"Syncing '{local_dir}' recursively to Google Drive folder '{subfolder_name}'...")
    _sync_folder_recursive(
        drive=drive,
        local_folder=local_dir,
        drive_folder_id=book_folder_id,
        overwrite_existing=overwrite_existing,
        book_folder_id=book_folder_id,
        book_title=book_title,
        book_index=book_index,
    )

    drive_url = f"https://drive.google.com/drive/folders/{book_folder_id}"
    logger.info("=" * 60)
    logger.info("ALL ASSETS SYNCED TO GOOGLE DRIVE!")
    logger.info(f"Folder URL: {drive_url}")
    logger.info("=" * 60)
    return drive_url
