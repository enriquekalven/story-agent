# Lily & Robo Storybook Generator 🤖👧📖

Welcome to the **Lily & Robo Storybook Generator**, a fully autonomous, AI-driven children's book generation and multi-lingual publishing orchestrator. 

This repository leverages the Google GenAI SDK (Gemini-2.5-Flash) and AlphaEvolve algorithms to automatically compose, illustrate, translate, and compile fixed-layout Kindle EPUB and print-ready PDF editions of stories for children aged 4 to 6.

## 🚀 Features

* **Fast, Streamlined Publishing Pipeline:** Generates a unified, high-quality edition in a single pass—eliminating slow multi-language generation loops for pages and PDFs and boosting generation speed by over 85%.
* **Chinese Bedtime Read-Aloud Audio:** Synthesizes natural, calming Chinese voice narration (`<Title>_Chinese_Read_Aloud.mp3`) using macOS native speech synthesis (`Tingting` / `Meijia`) mixed with ambient background music and voice ducking via FFmpeg.
* **Unified Artwork Ecosystem:** Automatically constructs sanitized, consistent visual prompts for recurring characters (**Lily** and **Robo**) alongside a whimsical watercolor artistic style.
* **Turnkey Fixed-Layout EPUB & PDF Compilers:** Compiles print-ready PDF manuscripts, KDP fixed-layout EPUB eBooks, and Amazon KDP marketing descriptions.
* **Resilient Dual Storage:** Packages all assets (images, PDF, EPUB, KDP description, and Chinese MP3 audio) into `./output/<book>/` locally, with optional automated upload to Google Drive.

## 📁 Repository Structure

| File | Purpose |
|---|---|
| `publish_pipeline.py` | Fast CLI publishing pipeline with options (`--topic`, `--max-pages`, `--no-audio`, `--no-drive`) |
| `audio_generator.py` | Chinese read-aloud script formatting, voice synthesis, and FFmpeg audio mixing |
| `storybook.py` | Story generation, validation agent, image overlay, and fast publishing workflow |
| `background_music.mp3` | Ambient soothing audio track for mixing with spoken narration |
| `prompts.py` | Evolved baseline character and illustration style library |
| `tests/` | Unit tests for storybook generation and Chinese audio synthesis (`pytest`) |
| `pyproject.toml` | `uv` package and test configuration |

## 🛠️ Getting Started

### 1. Prerequisites
Ensure you have Python 3.11+, FFmpeg (`brew install ffmpeg`), and optionally Google Cloud SDK (`gcloud`) for Drive integration.

### 2. Install Dependencies & Run Tests
```bash
pytest
```

### 3. Running the Fast Publishing Pipeline
Publish a single topic with Chinese read-aloud audio:
```bash
python3 publish_pipeline.py --topic "agent sandbox"
```

Quick test run (e.g. 2 pages):
```bash
python3 publish_pipeline.py --topic "agent sandbox" --max-pages 2
```

Publish all 8 pre-configured agent topics:
```bash
python3 publish_pipeline.py --all
```

Local-only mode without Google Drive:
```bash
python3 publish_pipeline.py --topic "agent sandbox" --no-drive
```

## 🧠 Evolved with AlphaEvolve
The prompt architecture in [**prompts.py**](prompts.py) was evolved and mathematically optimized by AlphaEvolve to climb from a `7.45` baseline to an **`8.91`** rating on Narrative Virality, Sensory Engagement, and Strict Visual Asset Consistency.

## 📜 License
*Designed by Enrique Chan.*
