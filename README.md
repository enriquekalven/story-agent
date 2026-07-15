# Lily & Robo Storybook Generator 🤖👧📖

Welcome to the **Lily & Robo Storybook Generator**, a fully autonomous, AI-driven children's book generation and multi-lingual publishing orchestrator. 

This repository leverages the Google GenAI SDK (Gemini-2.5-Flash) and AlphaEvolve algorithms to automatically compose, illustrate, translate, and compile fixed-layout Kindle EPUB and print-ready PDF editions of stories for children aged 4 to 6.

## 🚀 Features

* **Evolved Narrative Engine:** Composes high-fidelity, emotionally resonant, and original stories weaving STEM-focused learning highlights using Gemini-2.5-Flash.
* **Unified Artwork Ecosystem:** Automatically constructs sanitized, consistent visual prompts for recurring characters (**Lily** and **Robo**) alongside a Premium whimsical watercolor artistic style.
* **Multilingual Translation Engine:** Outputs and typesets local editions dynamically across English, Spanish, Portuguese, French, Japanese, Korean, Italian, and German.
* **Turnkey Fixed-Layout EPUB & PDF Compilers:** Renders typography overlays, background block transparency for readability, and embeds beautiful print-ready files directly to your target directory.

## 📁 Repository Structure

| File | Purpose |
|---|---|
| `publish_pipeline.py` | Core end-to-end publishing pipeline |
| `storybook.py` | Story generation, validation agent, and image overlay logic |
| `prompts.py` | Evolved baseline character and illustration style library |
| `compile_manuscript_pdf.py` | PDF manuscript compiling |
| `refresh_tokens.py` | Google Cloud Artifact Registry and gcloud token updater |
| `pyproject.toml` | `uv` package and environment configuration |

## 🛠️ Getting Started

### 1. Prerequisites
Ensure you have Python 3.11+, Google Cloud SDK (`gcloud`), and `uv` installed. You will need access to a Google Cloud Project with the Vertex AI APIs enabled.

### 2. Install Dependencies
```bash
uv sync
```

### 3. Authenticate with Google Cloud
The generator relies on Application Default Credentials (ADC) and `gcloud`:
```bash
gcloud auth application-default login
```

### 4. Running the Pipeline
Invoke the master pipeline to generate and publish books:
```bash
uv run python publish_pipeline.py
```

## 🧠 Evolved with AlphaEvolve
The prompt architecture in [**prompts.py**](prompts.py) was evolved and mathematically optimized by AlphaEvolve to climb from a `7.45` baseline to an **`8.91`** rating on Narrative Virality, Sensory Engagement, and Strict Visual Asset Consistency.

## 📜 License
*Designed by Enrique Chan.*
