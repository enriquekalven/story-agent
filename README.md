# Lily & Robo / Mei, Ming & Baobao Storybook Generator 🤖👧🐼📖

Welcome to **Story Agent**, an autonomous, AI-driven children's book generation, 10-language localization, read-aloud audio, Google Vids video, and Amazon KDP publishing orchestrator.

This repository leverages the Google GenAI SDK (`gemini-2.5-flash` / `gemini-2.5-flash-image` / Gemini & Cloud Chirp 3 HD TTS) to compose, illustrate with character-sheet consistency + Vision QA, translate across 10 languages, synthesize read-aloud narration, export 16:9 Google Vids decks & avatar videos, and package print-ready PDFs + KDP fixed-layout EPUB3 editions for children aged 4 to 6.

## 🚀 Key Capabilities

* **26-Book Multi-Series Catalog (`topics.yaml`):**
  * **Curious Little Minds (STEM Series — Books 1–14):** Lily & Robo explore AI agent concepts through cozy real-world metaphors.
  * **Chinese Proverb Tales (`genre: chinese_heritage` — Books 15–26):** Mei 美美, Ming 明明, and red-panda spirit guide Baobao 宝宝 bring classical *chengyu* idioms to life in warm watercolor + *shuimo* ink-wash style.
* **10-Language Localization & Packaging:** Parallel translation and localized fixed-layout PDF/EPUB3/16:9 PPTX generation across English (`en`), Spanish (`es`), Simplified Chinese (`zh`), Cantonese (`yue`), Portuguese (`pt`), Japanese (`ja`), Korean (`ko`), French (`fr`), Italian (`it`), and German (`de`).
* **Multilingual Bedtime Read-Aloud Audio (`audio_generator.py`):** Synthesizes calming EN, ES, and ZH MP3 narration mixed with 20% ducked ambient background music via FFmpeg, plus per-page Cloud Chirp 3 HD / Gemini TTS WAVs.
* **Google Vids 10-Language Pipeline (`vids_*.py`):** Exports 16:9 stage decks with localized voiceover speaker notes, clones & retargets Google Vids projects in background Chrome tabs without stealing focus, and supports both Vids AI Avatar UI rendering and quota-free local TTS + timed MP4 avatar replacement (`--avatar-mode mp4`).
* **Amazon KDP Publishing Suite (`kdp_*.py`):** Prepares standalone KDP cover JPEGs, cached listing descriptions, delivery-cost-optimized EPUBs (`kdp_epub_optimize.py`), and Playwright KDP draft uploads (`kdp_publisher.py`).
* **Indexed & Resilient Google Drive Sync (`drive_sync.py`):** Recursive folder sync with single-list folder indexing, MD5 checksum skip for unchanged files, query-escaped lookups, and exponential backoff on transient Drive errors.

## 📁 Repository Structure

| File | Purpose |
|---|---|
| `publish_pipeline.py` | Primary CLI publishing pipeline (`--topic`, `--next`, `--all`, `--genre`, `--qa`, `--export-vids`, `--no-drive`) |
| `storybook.py` | Story generation, validation, Vision QA, parallel 10-language translation, and typography rendering |
| `epub_packager.py` | Standalone PDF compilation, fixed-layout Kindle EPUB3 packaging, and structural EPUB validation |
| `audio_generator.py` | EN/ES/ZH read-aloud script builders, macOS / Cloud TTS / Gemini TTS synthesis, and FFmpeg ducking mixer |
| `vids_exporter.py` | Parallel 10-language 16:9 PPTX stage deck, localized PDF, and localized EPUB exporter |
| `vids_book_pipeline.py` | Resumable background-tab Google Vids cloner, scene retargeter, and AI/MP4 avatar orchestrator |
| `vids_book_flow.py` | Background-tab JXA bridge, scene navigation, slide/script replacement, and timed MP4 muxing |
| `vids_zh_avatars.py` | Quota-free 10-language TTS + looped avatar MP4 builder and scene video replacer |
| `vids_avatar_layout.py` | Reference layout verifier & fixer for Google Vids avatar size, crop, and stage placement |
| `vids_drive_sync.py` | Syncs 10-language `.pptx` decks, Google Slides conversions, and Vids URLs into book manifests |
| `drive_sync.py` | Google Drive API v3 client with folder indexing, MD5 skip, retry backoff, and recursive sync |
| `kdp_listings.py` | KDP metadata, cover extraction, and cached listing description generator (`kdp_manifest.json`) |
| `kdp_epub_optimize.py` | Binary-searched JPEG recompression to fit fixed-layout EPUBs within KDP delivery-fee budgets |
| `kdp_publisher.py` | Playwright automation to populate and upload KDP eBook drafts |
| `config.py` / `prompts.py` | Character & topic registries (`characters.yaml`, `topics.yaml`) and AlphaEvolve visual prompt library |
| `tests/` | Hermetic unit, genre, Cantonese, Vids pipeline, Drive sync, and EPUB validation test suites (`pytest`) |

## 🛠️ Getting Started

### 1. Prerequisites
Ensure you have Python 3.12+, [`uv`](https://docs.astral.sh/uv/), FFmpeg (`brew install ffmpeg`), and optionally Google Cloud SDK (`gcloud`) for Vertex AI / Cloud TTS / Drive integration.

### 2. Install Dependencies, Lint & Run Tests
```bash
uv sync --frozen
uv run ruff check .
uv run pytest -v
```

### 3. Running the Fast Publishing Pipeline
```bash
# Publish a single book by slug or title
uv run python publish_pipeline.py --topic "agent sandbox" --workers 4 --qa

# Quick 2-page local smoke test without Drive upload
uv run python publish_pipeline.py --topic "agent sandbox" --max-pages 2 --no-drive

# Publish the next pending book (all genres or Chinese Heritage only)
uv run python publish_pipeline.py --next --workers 4 --qa
uv run python publish_pipeline.py --next --genre chinese_heritage --workers 4 --qa

# Export 10-language Google Vids decks + localized PDFs/EPUBs
uv run python vids_exporter.py --book-dir "output/Year of the Horse" --no-drive

# Build quota-free timed MP4 avatars for any language (zh, yue, en, es, pt, ja, ko, fr, it, de)
uv run python vids_zh_avatars.py --book-dir "output/Year of the Horse" --lang yue --steps tts mp4

# Prepare and optimize KDP eBook packages
uv run python kdp_publisher.py prepare
uv run python kdp_epub_optimize.py write --target 2.6
```

## 🧠 Evolved with AlphaEvolve
The prompt architecture in [`prompts.py`](prompts.py) was evolved and mathematically optimized by AlphaEvolve to climb from a `7.45` baseline to an **`8.91`** rating on Narrative Virality, Sensory Engagement, and Strict Visual Asset Consistency.

## 📜 License
*Designed by Enrique Chan.*
