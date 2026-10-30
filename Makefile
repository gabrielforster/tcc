.PHONY: setup up down seed extract ingest dictionary eda features train score explain serve demo rag answer outreach voice contacts migrate pipeline test lint fmt

setup:            ## install dependencies (uv creates .venv with Python 3.12)
	uv sync --all-extras

up:               ## start Postgres (pgvector), Redis and MinIO
	docker compose up -d

down:
	docker compose down

seed:             ## create the development tenant, load knowledge/ into it, ingest, activate
	uv run collection tenant seed

extract:          ## store the raw dataset in object storage (COLLECTION_TENANT=<slug>)
	uv run collection extract

ingest:           ## extract + anonymize + validate; needs COLLECTION_TENANT=<slug>
	uv run collection ingest

dictionary:       ## regenerate docs/data-dictionary.md from the domain schema
	uv run collection dictionary

eda:              ## descriptive statistics and charts under docs/eda/ (COLLECTION_TENANT=<slug>)
	uv run collection eda

features:         ## feature engineering + chronological 70/15/15 split (COLLECTION_TENANT=<slug>)
	uv run collection features

train:            ## train candidates, publish the champion as a platform model version (COLLECTION_TENANT=<slug>)
	uv run collection train

score:            ## score the tenant's customers against the latest platform model (COLLECTION_TENANT=<slug>)
	uv run collection score

explain:          ## permutation importance and SHAP for the champion (COLLECTION_TENANT=<slug>)
	uv run collection explain

serve:            ## run the scoring API on http://127.0.0.1:8000
	uv run collection serve

demo:             ## route a mix of events through the orchestrator
	uv run collection demo

rag:              ## build the knowledge index and evaluate retrieval
	uv run collection rag

answer:           ## evaluate the responders; ARGS="--compare" also runs the LLM arm
	uv run collection answer $(ARGS)

outreach:         ## evaluate the outbound composers; ARGS="--compare" also runs the LLM arm
	uv run collection outreach $(ARGS)

voice:            ## sweep what recognition error costs; ARGS="--audio FILE" transcribes one clip
	uv run collection voice $(ARGS)

contacts:         ## report what the outbound path decides (serve --seed-demo is the live demo)
	uv run collection contacts

migrate:          ## apply database migrations (alembic upgrade head)
	uv run collection db upgrade

pipeline:         ## the whole data pipeline, end to end (set COLLECTION_TENANT)
	uv run collection pipeline

test:
	uv run pytest

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests
