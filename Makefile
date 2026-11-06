.PHONY: setup up down ollama seed extract ingest dictionary eda features train score explain serve demo rag answer outreach voice voices call call-bench contacts experiment concurrency sweep purge queue migrate pipeline test coverage lint fmt cronograma article

setup:            ## install dependencies (uv creates .venv with Python 3.12)
	uv sync --all-extras

up:               ## start Postgres (pgvector), Redis, MinIO and LiveKit (the voice call)
	docker compose up -d

down:
	docker compose down

OLLAMA_MODEL ?= qwen2.5:3b
.PHONY: ollama
ollama:           ## start the local model server and pull OLLAMA_MODEL (LLM_PROVIDER=openai)
	docker compose --profile llm up -d ollama
	docker compose exec ollama ollama pull $(OLLAMA_MODEL)

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

serve:            ## run the scoring API on http://127.0.0.1:8000; ARGS="--seed-demo" is the live demo
	uv run collection serve $(ARGS)

demo:             ## route a mix of events through the orchestrator
	uv run collection demo

rag:              ## evaluate retrieval; ARGS="--retriever pgvector" measures the serving path
	uv run collection rag $(ARGS)

answer:           ## evaluate the responders; ARGS="--compare" also runs the LLM arm
	uv run collection answer $(ARGS)

outreach:         ## evaluate the outbound composers; ARGS="--compare" also runs the LLM arm
	uv run collection outreach $(ARGS)

voice:            ## sweep what recognition error costs; ARGS="--audio FILE" transcribes one clip
	uv run collection voice $(ARGS)

voices:           ## download the two Piper pt-BR voices the call uses into data/voices
	uv run python -m piper.download_voices --download-dir data/voices pt_BR-faber-medium pt_BR-cadu-medium

call:             ## the voice call demo on http://127.0.0.1:8100/call/demo (needs `make voices`, LiveKit up)
	docker compose up -d livekit
	uv run collection call serve $(ARGS)

call-bench:       ## the turn-time budget on synthesised speech; ARGS="--live" goes through LiveKit
	uv run collection call bench $(ARGS)

contacts:         ## report what the outbound path decides (serve --seed-demo is the live demo)
	uv run collection contacts

experiment:       ## simulate the system against the manual and semi-automated baselines
	uv run collection experiment $(ARGS)

concurrency:      ## hold >= 100 chat conversations at once and time every request
	uv run collection concurrency $(ARGS)

sweep:            ## close the threads nothing is tracking (COLLECTION_TENANT=<slug>)
	uv run collection conversations sweep $(ARGS)

queue:            ## list the escalated threads waiting for a person (COLLECTION_TENANT=<slug>)
	uv run collection conversations queue

purge:            ## erase transcripts past their retention window (COLLECTION_TENANT=<slug>)
	uv run collection conversations purge $(ARGS)

migrate:          ## apply database migrations (alembic upgrade head)
	uv run collection db upgrade

pipeline:         ## the whole data pipeline, end to end (set COLLECTION_TENANT)
	uv run collection pipeline

test:
	uv run pytest

coverage:         ## the suite under coverage, then the critical-function report (target: 80%)
	uv run pytest --cov --cov-report=term --cov-report=json
	uv run python scripts/critical_coverage.py

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests

cronograma:       ## rebuild cronograma.pdf from cronograma.md (pandoc in Docker; no local TeX needed)
	docker run --rm --entrypoint sh -v "$(CURDIR)":/data pandoc/extra:latest -c '\
	  apk add -q font-dejavu >/dev/null && fc-cache -f >/dev/null && \
	  pandoc cronograma.md -o cronograma.pdf --pdf-engine=xelatex \
	    -V mainfont="DejaVu Serif" -V sansfont="DejaVu Sans" -V monofont="DejaVu Sans Mono" \
	    -V fontsize=9pt -V geometry:landscape -V geometry:margin=1.8cm && \
	  chown $(shell id -u):$(shell id -g) cronograma.pdf'

article:          ## build article/main.pdf (XeLaTeX + BibTeX in Docker; no local TeX needed)
	docker build -q -t tcc-article-tex -f article/Dockerfile article >/dev/null
	docker run --rm --user $(shell id -u):$(shell id -g) -e HOME=/tmp --entrypoint sh \
	  -v "$(CURDIR)/article":/data -w /data tcc-article-tex -c '\
	  run() { "$$@" >/dev/null || { grep -A3 "^!" main.log; exit 1; }; } && \
	  run xelatex -interaction=nonstopmode -halt-on-error main.tex && run bibtex main && \
	  run xelatex -interaction=nonstopmode -halt-on-error main.tex && \
	  run xelatex -interaction=nonstopmode -halt-on-error main.tex && \
	  rm -f main.aux main.bbl main.blg main.out main.log'
	@echo "article/main.pdf"
