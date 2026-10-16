import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import audio_generator
import config
import drive_sync
import prompts
from storybook import StorybookGenerator

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("publish_pipeline")

TOPICS_CONFIG = config.load_topics()


def init_drive_service(no_drive: bool = False):
    """Safely initializes Google Drive API service using ADC, with fallback for local runs."""
    return drive_sync.init_drive_service(no_drive=no_drive)


def get_or_create_root_drive_folder(drive_service, folder_name: str) -> Optional[str]:
    """Gets or creates the root Drive folder for the publication batch."""
    return drive_sync.get_or_create_folder(drive_service, folder_name)


def run_pipeline(
    topics: List[Dict[str, str]],
    max_pages: Optional[int] = None,
    no_audio: bool = False,
    no_drive: bool = False,
    output_base_dir: str = "output",
    bgm_path: str = "background_music.mp3",
    chinese_voice: str = audio_generator.DEFAULT_CHINESE_VOICE,
    resume: bool = True,
    max_workers: int = config.MAX_CONCURRENT_PAGES,
    enable_visual_qa: bool = config.ENABLE_VISUAL_QA,
    upscale_pdf: bool = False,
) -> List[dict]:
    """Executes the fast publishing pipeline for specified topics."""
    drive_service = init_drive_service(no_drive=no_drive)
    book_gen = StorybookGenerator(drive_service=drive_service)

    root_folder_id = get_or_create_root_drive_folder(
        drive_service, config.DRIVE_ROOT_FOLDER_NAME
    )

    results = []

    for idx, topic_cfg in enumerate(topics, 1):
        topic = topic_cfg["topic_name"]
        folder_name = topic_cfg.get("folder_name", topic[:30])
        age_group = topic_cfg.get("age_group", "4-6")

        logger.info("=" * 60)
        logger.info(f"[{idx}/{len(topics)}] Publishing: '{topic}'")
        logger.info("=" * 60)

        topic_folder_id = None
        if drive_service and root_folder_id:
            try:
                topic_folder_id = book_gen.create_drive_folder(
                    folder_name, parent_id=root_folder_id
                )
            except Exception as e:
                logger.warning(f"Could not create Drive topic subfolder: {e}")

        topic_output_dir = os.path.join(output_base_dir, folder_name)

        try:
            res = book_gen.run_fast_publishing_workflow(
                topic=topic,
                age_group=age_group,
                max_pages=max_pages,
                language="English",
                parent_id=topic_folder_id,
                override_char=prompts.SERIES_CHARACTER,
                override_style=prompts.SERIES_STYLE,
                generate_audio=(not no_audio),
                output_dir=topic_output_dir,
                bgm_path=bgm_path,
                chinese_voice=chinese_voice,
                resume=resume,
                max_workers=max_workers,
                enable_visual_qa=enable_visual_qa,
                upscale_pdf=upscale_pdf,
            )
            results.append(res)
            if res.get("status") == "success":
                logger.info(f"Successfully published: '{res['title']}'")
                if max_pages is None:
                    config.update_topic_status(
                        folder_name,
                        status="published",
                        last_published_at=datetime.now(timezone.utc).isoformat(),
                    )
            else:
                logger.error(
                    f"Partial failure publishing '{topic}': failed pages={res.get('pages_failed')}"
                )
        except Exception as e:
            logger.error(f"Error publishing topic '{topic}': {e}", exc_info=True)
            results.append(
                {
                    "status": "error",
                    "topic": topic,
                    "folder_name": folder_name,
                    "error": str(e),
                }
            )

    # Write batch manifest summary
    try:
        os.makedirs(output_base_dir, exist_ok=True)
        batch_manifest_path = os.path.join(output_base_dir, "batch_manifest.json")
        with open(batch_manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "topics_requested": len(topics),
                    "topics_succeeded": sum(1 for r in results if r.get("status") == "success"),
                    "results": results,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        logger.warning(f"Failed to write batch_manifest.json: {e}")

    return results


def parse_args(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        description="Fast, automated publishing pipeline for Lily & Robo Adventures with Chinese Audio."
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Specify a single topic to publish (e.g. 'agent sandbox').",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Publish all 8 pre-configured agent topics in sequence.",
    )
    parser.add_argument(
        "--next",
        action="store_true",
        help="Publish the next pending (unpublished) topic from topics.yaml.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit the number of pages (e.g. 2 for lightning-fast smoke testing).",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Skip Chinese MP3 audio generation.",
    )
    parser.add_argument(
        "--no-drive",
        action="store_true",
        help="Force local-only mode, skipping Google Drive uploads.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Base directory for generated assets (default: ./output).",
    )
    parser.add_argument(
        "--bgm",
        type=str,
        default="background_music.mp3",
        help="Path to background music file for mixing (default: background_music.mp3).",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=audio_generator.DEFAULT_CHINESE_VOICE,
        help=f"Voice name for Chinese narration (default: {audio_generator.DEFAULT_CHINESE_VOICE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=config.MAX_CONCURRENT_PAGES,
        help=f"Concurrent workers for page illustration generation (default: {config.MAX_CONCURRENT_PAGES}).",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume from cached story.json and raw page images if available (default: --resume).",
    )
    parser.add_argument(
        "--qa",
        action=argparse.BooleanOptionalAction,
        default=config.ENABLE_VISUAL_QA,
        help="Enable Gemini Vision per-page QA gate to catch baked-in text or off-model characters.",
    )
    parser.add_argument(
        "--upscale-pdf",
        action="store_true",
        help="Upscale pages to 2550x2550 (8.5x8.5 inches at 300 DPI) when compiling the print PDF.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    all_topics = config.load_topics()

    if args.topic:
        matched = [
            t
            for t in all_topics
            if args.topic.lower() in t.get("topic_name", "").lower()
            or args.topic.lower() == t.get("folder_name", "").lower()
            or args.topic.lower() == t.get("id", "").lower()
        ]
        if matched:
            topics = [matched[0]]
        else:
            topics = [
                {
                    "topic_name": args.topic,
                    "folder_name": args.topic.split("(")[0].strip().title(),
                    "age_group": "4-6",
                }
            ]
    elif args.next:
        next_topic = config.get_next_pending_topic()
        if not next_topic:
            logger.info("All topics in topics.yaml are already marked 'published'! Nothing to do.")
            return 0
        topics = [next_topic]
        logger.info("Selected next pending topic from topics.yaml: '%s'", next_topic["topic_name"])
    elif args.all:
        topics = all_topics
    else:
        topics = [all_topics[0]]
        logger.info(
            "No --topic, --next, or --all specified; defaulting to topic: '%s'",
            topics[0]["topic_name"],
        )

    results = run_pipeline(
        topics=topics,
        max_pages=args.max_pages,
        no_audio=args.no_audio,
        no_drive=args.no_drive,
        output_base_dir=args.output_dir,
        bgm_path=args.bgm,
        chinese_voice=args.voice,
        resume=args.resume,
        max_workers=args.workers,
        enable_visual_qa=args.qa,
        upscale_pdf=args.upscale_pdf,
    )

    logger.info("=" * 60)
    logger.info("PUBLISHING BATCH COMPLETE")
    logger.info("=" * 60)
    has_failure = len(results) < len(topics)
    for r in results:
        status = r.get("status", "success")
        if status != "success":
            has_failure = True
        logger.info(f"Book: {r.get('title', r.get('topic'))} [status={status}]")
        if r.get("pages_count") is not None:
            logger.info(f"  Pages: {r.get('pages_succeeded', r.get('pages_count'))}/{r.get('pages_count')}")
        if r.get("output_dir"):
            logger.info(f"  Local Assets: {r.get('output_dir')}")
        if r.get("pdf_path"):
            logger.info(f"  PDF: {r.get('pdf_path')}")
        if r.get("epub_path"):
            logger.info(f"  EPUB: {r.get('epub_path')}")
        if r.get("audio_path"):
            logger.info(f"  Chinese Audio: {r.get('audio_path')}")
        if r.get("manifest_path"):
            logger.info(f"  Manifest: {r.get('manifest_path')}")
        if r.get("drive_folder_id"):
            logger.info(f"  Google Drive: https://drive.google.com/drive/folders/{r.get('drive_folder_id')}")

    if has_failure:
        logger.error("One or more topics failed during publishing batch.")
        sys.exit(1)
    return 0


if __name__ == "__main__":
    main()
