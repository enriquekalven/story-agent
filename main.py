import argparse
import logging
import random
import sys

import config
import drive_sync
from storybook import StorybookGenerator

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TOPICS = [t["topic_name"] for t in config.load_topics()]
AGE_GROUPS = ["0-3", "4-6", "7-9"]


def main():
    parser = argparse.ArgumentParser(description="Generate kids friendly storybooks about AI agents.")
    parser.add_argument("--topic", type=str, default="automatic", help="Topic of the storybook")
    parser.add_argument("--age-group", type=str, default="automatic", help="Age group (0-3, 4-6, 7-9)")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit number of pages generated")
    parser.add_argument(
        "--language",
        type=str,
        default="all",
        help="Language of the storybook (English, Spanish, Chinese Simplified, or all)",
    )
    parser.add_argument("--no-drive", action="store_true", help="Run without Google Drive upload")

    args = parser.parse_args()

    topic = random.choice(TOPICS) if args.topic == "automatic" else args.topic
    age_group = random.choice(AGE_GROUPS) if args.age_group == "automatic" else args.age_group

    logger.info("Starting Storybook Generator workflow...")
    logger.info(f"Selected Topic: {topic}")
    logger.info(f"Selected Age Group: {age_group}")

    try:
        drive_service = drive_sync.init_drive_service(no_drive=args.no_drive)
        generator = StorybookGenerator(drive_service)

        languages = (
            ["English", "Spanish", "Chinese Simplified", "French", "Portuguese"]
            if args.language == "all"
            else [args.language]
        )
        for lang in languages:
            logger.info(f"Running workflow for language: {lang}")
            generator.run_workflow(topic, age_group, max_pages=args.max_pages, language=lang)
    except Exception as e:
        logger.error(f"Workflow failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
