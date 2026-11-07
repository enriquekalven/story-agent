"""Legacy CLI entrypoint forwarding to StorybookGenerator's fast publishing workflow.

Prefer `publish_pipeline.py` for full backlog, genre, audio, QA, and Google Vids flags.
"""

import argparse
import logging
import random
import sys

import config
import drive_sync
from storybook import StorybookGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TOPICS = [t["topic_name"] for t in config.load_topics()]
AGE_GROUPS = ["0-3", "4-6", "7-9"]


def _resolve_topic_entry(query: str) -> dict | None:
    needle = query.strip().lower()
    for item in config.load_topics():
        if (
            needle == str(item.get("id", "")).lower()
            or needle == str(item.get("folder_name", "")).lower()
            or needle in str(item.get("topic_name", "")).lower()
        ):
            return item
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate kids-friendly storybooks (STEM & Chinese Heritage).")
    parser.add_argument("--topic", type=str, default="automatic", help="Topic or slug of the storybook")
    parser.add_argument("--age-group", type=str, default="4-6", help="Age group (0-3, 4-6, 7-9, or automatic)")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit number of pages generated")
    parser.add_argument(
        "--language",
        type=str,
        default="fast",
        help="Use 'fast' (default unified fast pipeline) or a specific language name for legacy single-language mode",
    )
    parser.add_argument("--no-drive", action="store_true", help="Run without Google Drive upload")
    parser.add_argument("--no-audio", action="store_true", help="Skip narration audio synthesis")

    args = parser.parse_args()

    topic_entry = _resolve_topic_entry(args.topic) if args.topic != "automatic" else None
    topic = (
        random.choice(TOPICS)
        if args.topic == "automatic"
        else (topic_entry["topic_name"] if topic_entry else args.topic)
    )
    age_group = random.choice(AGE_GROUPS) if args.age_group == "automatic" else args.age_group
    genre = (topic_entry or {}).get("genre", "stem")
    character_ids = (topic_entry or {}).get("characters")

    logger.info("Starting Storybook Generator workflow (topic=%s, age_group=%s, genre=%s)...", topic, age_group, genre)

    try:
        drive_service = drive_sync.init_drive_service(no_drive=args.no_drive)
        generator = StorybookGenerator(drive_service)
        if args.language in ("fast", "all"):
            generator.run_fast_publishing_workflow(
                topic=topic,
                age_group=age_group,
                max_pages=args.max_pages,
                generate_audio=not args.no_audio,
                character_ids=character_ids,
                genre=genre,
            )
        else:
            generator.run_workflow(topic, age_group, max_pages=args.max_pages, language=args.language)
    except Exception as exc:
        logger.error("Workflow failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
