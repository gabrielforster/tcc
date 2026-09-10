.PHONY: setup up down extract ingest dictionary eda features pipeline test lint fmt

setup:            ## install dependencies (uv creates .venv with Python 3.12)
	uv sync --all-extras

up:               ## start Postgres (pgvector) and Redis
	docker compose up -d

down:
	docker compose down

extract:          ## extract the raw dataset from the configured source
	uv run collection extract

ingest:           ## anonymize + validate + write to data/interim
	uv run collection ingest

dictionary:       ## regenerate docs/data-dictionary.md from the domain schema
	uv run collection dictionary

eda:              ## descriptive statistics and charts under docs/eda/
	uv run collection eda

features:         ## feature engineering + chronological 70/15/15 split
	uv run collection features

pipeline:         ## schedule deliverables 3, 4 and 5, end to end
	uv run collection pipeline

test:
	uv run pytest

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests
