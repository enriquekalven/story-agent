.PHONY: help sync lint format test smoke publish next all sync-drive kdp-prepare kdp-optimize vids-sync

help:
	@echo "Story-Agent Automation Targets:"
	@echo "  make sync          - Install locked dependencies with uv"
	@echo "  make lint          - Run Ruff linter"
	@echo "  make format        - Auto-format code with Ruff"
	@echo "  make test          - Run isolated unit & EPUB validation tests"
	@echo "  make smoke         - Run a 2-page local smoke test (--max-pages 2 --no-drive)"
	@echo "  make next          - Publish the next pending book from topics.yaml"
	@echo "  make all           - Publish all pending books in topics.yaml in parallel page mode"
	@echo "  make sync-drive    - Sync output/ directory to Google Drive"
	@echo "  make vids-sync     - Sync Google Vids decks and project links to Google Drive"
	@echo "  make kdp-prepare   - Build KDP covers, metadata, and kdp_manifest.json"
	@echo "  make kdp-optimize  - Optimize KDP EPUBs under target delivery size"

sync:
	uv sync --frozen

lint:
	uv run ruff check .

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest -v

smoke:
	uv run python publish_pipeline.py --max-pages 2 --no-drive --workers 2

next:
	uv run python publish_pipeline.py --next --workers 4 --qa

all:
	uv run python publish_pipeline.py --all --workers 4 --qa

sync-drive:
	uv run python push_to_drive.py

vids-sync:
	uv run python vids_drive_sync.py --all

kdp-prepare:
	uv run python kdp_publisher.py prepare

kdp-optimize:
	uv run python kdp_epub_optimize.py write
