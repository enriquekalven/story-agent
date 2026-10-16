"""CLI utility to synchronize a local storybook output directory to Google Drive."""

import argparse
import logging
import sys
from typing import Optional

import config
import drive_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("push_to_drive")


def get_drive_service():
    """Initializes Google Drive service from Application Default Credentials."""
    return drive_sync.init_drive_service(no_drive=False)


def get_or_create_folder(drive_service, folder_name: str, parent_id: Optional[str] = None) -> str:
    """Gets existing folder or creates a new one in Google Drive."""
    return drive_sync.get_or_create_folder(drive_service, folder_name, parent_id=parent_id) or ""


def upload_file(drive_service, local_file_path: str, folder_id: str, overwrite: bool = True):
    """Uploads or updates a local file in a Google Drive folder."""
    return drive_sync.upsert_local_file(
        drive_service,
        local_file_path,
        folder_id,
        overwrite_existing=overwrite,
    )


def push_directory(
    local_dir: str,
    root_folder_name: str = config.DRIVE_ROOT_FOLDER_NAME,
    overwrite: bool = True,
):
    """Pushes an entire local output directory to Google Drive."""
    url = drive_sync.sync_directory_to_drive(
        local_dir=local_dir,
        root_folder_name=root_folder_name,
        overwrite_existing=overwrite,
    )
    return url or False


def main():
    parser = argparse.ArgumentParser(description="Sync local storybook output folder to Google Drive.")
    parser.add_argument(
        "target_dir",
        nargs="?",
        default="output/Long Running Agents",
        help="Local output directory to sync (default: 'output/Long Running Agents').",
    )
    parser.add_argument(
        "--root-folder",
        default=config.DRIVE_ROOT_FOLDER_NAME,
        help=f"Root Google Drive folder name (default: '{config.DRIVE_ROOT_FOLDER_NAME}').",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Skip existing files instead of updating them in place.",
    )
    args = parser.parse_args()

    result = push_directory(
        args.target_dir,
        root_folder_name=args.root_folder,
        overwrite=(not args.no_overwrite),
    )
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
