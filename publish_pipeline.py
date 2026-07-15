import os
import logging
from datetime import datetime
import google.auth
from googleapiclient.discovery import build
from storybook import StorybookGenerator
import prompts

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

LANGUAGES = ["English", "Spanish", "Portuguese", "Japanese", "Korean", "French", "Italian", "German"]

SERIES_CHARACTER = prompts.SERIES_CHARACTER

SERIES_STYLE = prompts.SERIES_STYLE

def main():
    # 1. Authenticate with Google Drive using ADC
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Instantiate generator
    book_gen = StorybookGenerator(drive_service)
    
    # 2. Get/Create root folder for today's date (format: YYYY-MM-DD)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Search for an existing folder with today's date name to avoid duplicates
    query = f"name = '{today_str}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        today_folder_id = files[0]["id"]
        logger.info(f"Reusing existing root folder for today: {today_str} (ID: {today_folder_id})")
    else:
        logger.info(f"Creating root folder for today: {today_str}...")
        today_folder_id = book_gen.create_drive_folder(today_str)
    
    topics_config = [
        {
            "topic_name": "long running agents (agents that work day and night to help people)",
            "folder_name": "Long Running Agents",
            "age_group": "4-6"
        },
        {
            "topic_name": "agent sandbox (a safe digital playground where robots learn to play)",
            "folder_name": "Agent Sandbox",
            "age_group": "4-6"
        },
        {
            "topic_name": "memory bank (a magical treasure chest where robots keep their favorite memories and facts)",
            "folder_name": "Agent Memory Bank",
            "age_group": "4-6"
        },
        {
            "topic_name": "skills (superpowers that AI agents learn, like drawing and translating languages)",
            "folder_name": "Agent Skills",
            "age_group": "4-6"
        },
        {
            "topic_name": "computer use (how AI agents learn to click, type, and navigate a computer to help people with tasks)",
            "folder_name": "Agent Computer Use",
            "age_group": "4-6"
        },
        {
            "topic_name": "observability (how AI agents keep track of their actions, like writing logs or keeping diary notes so people can see what they did)",
            "folder_name": "Agent Observability",
            "age_group": "4-6"
        },
        {
            "topic_name": "evaluation (how AI agents are tested and checked to see how well they do their jobs, like a report card for robots)",
            "folder_name": "Agent Evaluation",
            "age_group": "4-6"
        },
        {
            "topic_name": "vibe coding (how people can build apps and games simply by talking to computer helpers, using their ideas and vibes instead of writing complicated code)",
            "folder_name": "Agent Vibe Coding",
            "age_group": "4-6"
        }
    ]
    
    for config in topics_config:
        topic = config["topic_name"]
        folder_name = config["folder_name"]
        age_group = config["age_group"]
        
        logger.info(f"===========================================================")
        logger.info(f"Starting pipeline for Topic: '{topic}'")
        logger.info(f"===========================================================")
        
        # Create subfolder for this topic inside the date folder
        topic_folder_id = book_gen.create_drive_folder(folder_name, parent_id=today_folder_id)
        
        try:
            # Run the multilingual publishing workflow (PDF, EPUB, Description Google Doc)
            book_gen.run_multilingual_workflow(
                topic=topic,
                age_group=age_group,
                languages=LANGUAGES,
                parent_id=topic_folder_id,
                override_char=SERIES_CHARACTER,
                override_style=SERIES_STYLE
            )
        except Exception as e:
            logger.error(f"Error running multilingual workflow for topic '{topic}': {e}")
                
    logger.info("===========================================================")
    logger.info(f"All pipelines completed successfully for date {today_str}!")
    logger.info(f"Root Folder: https://drive.google.com/drive/folders/{today_folder_id}")
    logger.info("===========================================================")

if __name__ == "__main__":
    main()
