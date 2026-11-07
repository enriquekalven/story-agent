"""Centralized configuration and topic backlog management for Story-Agent."""

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent

# Google Cloud & Vertex AI configuration
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-maui")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GOOGLE_GENAI_USE_VERTEXAI = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "True")

# Model selection
TEXT_MODEL = os.environ.get("STORY_AGENT_TEXT_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = os.environ.get("STORY_AGENT_IMAGE_MODEL", "gemini-2.5-flash-image")
VISION_QA_MODEL = os.environ.get("STORY_AGENT_VISION_QA_MODEL", "gemini-2.5-flash")
TTS_MODEL = os.environ.get("STORY_AGENT_TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_PROVIDER = os.environ.get("STORY_AGENT_TTS_PROVIDER", "auto")  # auto | gemini | say
GEMINI_TTS_VOICE = os.environ.get("STORY_AGENT_GEMINI_TTS_VOICE", "Kore")

# Concurrency, retry & QA settings
MAX_CONCURRENT_PAGES = int(os.environ.get("STORY_AGENT_MAX_WORKERS", "4"))
MAX_API_RETRIES = int(os.environ.get("STORY_AGENT_MAX_RETRIES", "3"))
MAX_QA_RETRIES = int(os.environ.get("STORY_AGENT_MAX_QA_RETRIES", "2"))
ENABLE_VISUAL_QA = os.environ.get("STORY_AGENT_VISUAL_QA", "true").lower() in ("1", "true", "yes")

# Publishing & print settings
PDF_DPI = float(os.environ.get("STORY_AGENT_PDF_DPI", "300.0"))
PDF_TRIM_INCHES = float(os.environ.get("STORY_AGENT_PDF_TRIM_INCHES", "8.5"))
DRIVE_ROOT_FOLDER_NAME = os.environ.get(
    "STORY_AGENT_DRIVE_ROOT_FOLDER",
    "Story Book",
)
DRIVE_PORTFOLIO_FOLDER_ID = os.environ.get(
    "STORY_AGENT_PORTFOLIO_FOLDER_ID",
    "1b4s2-ce13jl52-a3sK6-w7t7cB4wG-xP",
)

# Asset paths & Character Consistency Requirement
TOPICS_YAML_PATH = Path(os.environ.get("STORY_AGENT_TOPICS_FILE", PROJECT_ROOT / "topics.yaml"))
CHARACTERS_YAML_PATH = Path(
    os.environ.get("STORY_AGENT_CHARACTERS_FILE", PROJECT_ROOT / "characters.yaml")
)
REFERENCE_SHEET_PATH = Path(
    os.environ.get("STORY_AGENT_REFERENCE_SHEET", PROJECT_ROOT / "assets" / "character_reference_sheet.png")
)
HERITAGE_REFERENCE_SHEET_PATH = Path(
    os.environ.get(
        "STORY_AGENT_HERITAGE_REFERENCE_SHEET",
        PROJECT_ROOT / "assets" / "characters" / "chinese_heritage_reference_sheet.png",
    )
)
REQUIRE_CHARACTER_REFERENCE = os.environ.get(
    "STORY_AGENT_REQUIRE_CHARACTER_REF", "true"
).lower() in ("1", "true", "yes")
BUNDLED_FONT_DIR = PROJECT_ROOT / "assets" / "fonts"

DEFAULT_TOPICS: list[dict[str, Any]] = [
    {
        "id": "long-running-agents",
        "topic_name": "long running agents (agents that work day and night to help people)",
        "folder_name": "Long Running Agents",
        "age_group": "4-6",
        "status": "published",
    },
    {
        "id": "agent-sandbox",
        "topic_name": "agent sandbox (a safe digital playground where robots learn to play)",
        "folder_name": "Agent Sandbox",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "memory-bank",
        "topic_name": "memory bank (a magical treasure chest where robots keep their favorite memories and facts)",
        "folder_name": "Agent Memory Bank",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "skills",
        "topic_name": "skills (superpowers that AI agents learn, like drawing and translating languages)",
        "folder_name": "Agent Skills",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "computer-use",
        "topic_name": "computer use (how AI agents learn to click, type, and navigate a computer to help people with tasks)",
        "folder_name": "Agent Computer Use",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "observability",
        "topic_name": "observability (how AI agents keep track of their actions, like writing logs or keeping diary notes so people can see what they did)",
        "folder_name": "Agent Observability",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "evaluation",
        "topic_name": "evaluation (how AI agents are tested and checked to see how well they do their jobs, like a report card for robots)",
        "folder_name": "Agent Evaluation",
        "age_group": "4-6",
        "status": "pending",
    },
    {
        "id": "vibe-coding",
        "topic_name": "vibe coding (how people can build apps and games simply by talking to computer helpers, using their ideas and vibes instead of writing complicated code)",
        "folder_name": "Agent Vibe Coding",
        "age_group": "4-6",
        "status": "pending",
    },
]


def load_topics(yaml_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Loads topic configurations from topics.yaml, falling back to DEFAULT_TOPICS."""
    path = Path(yaml_path) if yaml_path else TOPICS_YAML_PATH
    if yaml is not None and path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            topics = data.get("topics", data if isinstance(data, list) else [])
            if isinstance(topics, list) and topics:
                return topics
        except Exception:
            pass
    return [dict(t) for t in DEFAULT_TOPICS]


def get_next_pending_topic(yaml_path: Path | str | None = None, genre: str | None = None) -> dict[str, Any] | None:
    """Returns the first topic with status != 'published' from topics.yaml (optionally filtered by genre)."""
    for topic in load_topics(yaml_path):
        if genre and str(topic.get("genre", "stem")).lower() != genre.lower():
            continue
        if topic.get("status", "pending") != "published":
            return topic
    return None


def update_topic_status(
    folder_name_or_topic: str,
    status: str,
    yaml_path: Path | str | None = None,
    last_published_at: str | None = None,
) -> bool:
    """Updates a topic's status in topics.yaml if the file and PyYAML are available."""
    path = Path(yaml_path) if yaml_path else TOPICS_YAML_PATH
    if yaml is None or not path.exists():
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        topics = data.get("topics", [])
        updated = False
        target = folder_name_or_topic.lower().strip()
        for item in topics:
            if (
                item.get("folder_name", "").lower() == target
                or item.get("topic_name", "").lower() == target
                or item.get("id", "").lower() == target
            ):
                item["status"] = status
                if last_published_at:
                    item["last_published_at"] = last_published_at
                updated = True
        if updated:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        return updated
    except Exception:
        return False


def load_characters(yaml_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Loads the canonical character registry from characters.yaml."""
    path = Path(yaml_path) if yaml_path else CHARACTERS_YAML_PATH
    if yaml is not None and path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            chars = data.get("characters", data if isinstance(data, list) else [])
            if isinstance(chars, list) and chars:
                return chars
        except Exception:
            pass
    return []


def is_heritage_character_list(
    character_ids: list[str] | None = None,
    genre: str | None = None,
) -> bool:
    """Returns True if the genre or character list belongs to the Chinese Heritage & Proverbs cast."""
    if genre and genre.lower().strip() in ("chinese_heritage", "heritage", "proverbs", "chinese_proverbs"):
        return True
    requested = {c.lower().strip() for c in (character_ids or []) if c}
    if requested and requested.intersection({"mei", "ming", "baobao", "美美", "明明", "宝宝"}):
        if not requested.intersection({"lily", "robo"}):
            return True
    return False


def get_active_characters(
    character_ids: list[str] | None = None,
    yaml_path: Path | str | None = None,
    genre: str | None = None,
) -> list[dict[str, Any]]:
    """Returns character definitions for the requested IDs (or genre cast for chinese_heritage)."""
    all_chars = load_characters(yaml_path)
    if not all_chars:
        return []
    requested = {c.lower().strip() for c in (character_ids or []) if c}
    heritage_mode = is_heritage_character_list(character_ids, genre=genre)
    active: list[dict[str, Any]] = []
    for ch in all_chars:
        cid = str(ch.get("id", "")).lower().strip()
        cname = str(ch.get("name", "")).lower().strip()
        cgenre = str(ch.get("genre", "stem")).lower().strip()
        if heritage_mode:
            if requested:
                if cid in requested or cname in requested:
                    active.append(ch)
            elif cgenre == "chinese_heritage":
                active.append(ch)
        else:
            if ch.get("default_active", False) or cid in requested or cname in requested:
                active.append(ch)
    return active


def get_character_reference_paths(
    character_ids: list[str] | None = None,
    yaml_path: Path | str | None = None,
    genre: str | None = None,
) -> list[Path]:
    """Resolves existing reference image paths for the active genre and character cast."""
    paths: list[Path] = []
    heritage_mode = is_heritage_character_list(character_ids, genre=genre)
    if heritage_mode:
        if HERITAGE_REFERENCE_SHEET_PATH.exists():
            paths.append(HERITAGE_REFERENCE_SHEET_PATH)
        for ch in get_active_characters(character_ids, yaml_path, genre="chinese_heritage"):
            ref_rel = ch.get("reference_image")
            if not ref_rel:
                continue
            p = Path(ref_rel) if Path(ref_rel).is_absolute() else PROJECT_ROOT / ref_rel
            if p.exists() and p not in paths and not HERITAGE_REFERENCE_SHEET_PATH.exists():
                paths.append(p)
        return paths

    if REFERENCE_SHEET_PATH.exists():
        paths.append(REFERENCE_SHEET_PATH)
    for ch in get_active_characters(character_ids, yaml_path):
        ref_rel = ch.get("reference_image")
        if not ref_rel:
            continue
        p = Path(ref_rel) if Path(ref_rel).is_absolute() else PROJECT_ROOT / ref_rel
        # Only add guest character reference sheets in addition to master sheet,
        # or add individual core crops if master sheet is absent.
        if p.exists() and p not in paths:
            if not ch.get("default_active", False) or not REFERENCE_SHEET_PATH.exists():
                paths.append(p)
    return paths


def register_character(
    char_id: str,
    name: str,
    visual_description: str,
    strict_invariants: str,
    role: str = "recurring_friend",
    reference_image: str | None = None,
    negative_traits: str = "",
    name_es: str | None = None,
    name_zh: str | None = None,
    default_active: bool = False,
    yaml_path: Path | str | None = None,
) -> dict[str, Any]:
    """Registers or updates a character in characters.yaml for future books."""
    path = Path(yaml_path) if yaml_path else CHARACTERS_YAML_PATH
    entry = {
        "id": char_id.lower().strip(),
        "name": name.strip(),
        "name_es": (name_es or name).strip(),
        "name_zh": (name_zh or name).strip(),
        "role": role,
        "default_active": default_active,
        "reference_image": reference_image or f"assets/characters/{char_id.lower().strip()}_ref.png",
        "visual_description": visual_description.strip(),
        "strict_invariants": strict_invariants.strip(),
        "negative_traits": negative_traits.strip(),
    }
    if yaml is not None:
        data: dict[str, Any] = {
            "master_reference_sheet": "assets/character_reference_sheet.png",
            "require_reference_sheet": True,
            "characters": [],
        }
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception:
                pass
        chars = list(data.get("characters", []))
        replaced = False
        for idx, existing in enumerate(chars):
            if str(existing.get("id", "")).lower().strip() == entry["id"]:
                chars[idx] = entry
                replaced = True
                break
        if not replaced:
            chars.append(entry)
        data["characters"] = chars
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return entry

