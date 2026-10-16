FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORY_AGENT_TTS_PROVIDER=gemini \
    STORY_AGENT_VISUAL_QA=true

# Install FFmpeg for MP3 audio mixing and Noto CJK fonts for multilingual cover/page typography
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ENTRYPOINT ["uv", "run", "python", "publish_pipeline.py"]
CMD ["--next", "--workers", "4", "--qa"]
