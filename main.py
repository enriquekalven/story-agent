import argparse
import random
import logging
import google.auth
from googleapiclient.discovery import build
from storybook import StorybookGenerator

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TOPICS = [
    "long running agents (agents that work day and night to help people)",
    "agent sandbox (a safe digital playground where robots learn to play)",
    "agent coding (how baby robots write instructions to build games)",
    "memory (how AI agents remember their friends and what they did)",
    "tools (how robots use helpful tools like calculators and magnifying glasses)",
    "skills (superpowers that AI agents learn, like drawing and translating languages)"
]

AGE_GROUPS = ["0-3", "4-6", "7-9"]

def main():
    parser = argparse.ArgumentParser(description="Generate kids friendly storybooks about AI agents.")
    parser.add_argument("--topic", type=str, default="automatic", help="Topic of the storybook")
    parser.add_argument("--age-group", type=str, default="automatic", help="Age group (0-3, 4-6, 7-9)")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit number of pages generated")
    parser.add_argument("--language", type=str, default="all", help="Language of the storybook (English, Spanish, Chinese Simplified, or all)")
    
    args = parser.parse_args()
    
    # Selection of topic
    if args.topic == "automatic":
        topic = random.choice(TOPICS)
    else:
        topic = args.topic
        
    # Selection of age group
    if args.age_group == "automatic":
        age_group = random.choice(AGE_GROUPS)
    else:
        age_group = args.age_group
        
    logger.info(f"Starting Storybook Generator workflow...")
    logger.info(f"Selected Topic: {topic}")
    logger.info(f"Selected Age Group: {age_group}")
    
    try:
        # Authenticate with Google Drive
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        drive_service = build("drive", "v3", credentials=credentials)
        
        # Initialize and run generator
        generator = StorybookGenerator(drive_service)
        
        languages = ["English", "Spanish", "Chinese Simplified", "French", "Portuguese"] if args.language == "all" else [args.language]
        for lang in languages:
            logger.info(f"Running workflow for language: {lang}")
            generator.run_workflow(topic, age_group, max_pages=args.max_pages, language=lang)
        
    except Exception as e:
        logger.error(f"Workflow failed: {e}")

if __name__ == "__main__":
    main()
