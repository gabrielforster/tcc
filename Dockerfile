# syntax=docker/dockerfile:1
#
# One image, two commands: `collection serve` (the API, :8000) and `collection call serve`
# (the voice call, :8100). One rather than two because the dependency set is nearly the same
# -- the call server adds Whisper and Piper, the API adds nothing the call lacks -- and two
# images would be two builds, two pushes and two tags to keep in step for a few hundred MB.
#
#   docker build -t tcc --build-arg APP_VERSION=$(git rev-parse --short HEAD) .
#
# Three stages: `build` resolves the virtualenv from uv.lock, `weights` downloads the models
# into /opt/models, and `runtime` copies both onto a slim base with no compiler, no uv and no
# build cache.

ARG PYTHON_IMAGE=python:3.12-slim-trixie

# uv as a pinned image rather than `pip install uv`: the binary is static, and copying it
# out of its own image keeps the version in this file, next to the lockfile it reads.
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv


FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

# Bytecode compiled once here instead of on every cold start; copy rather than hardlink
# because the cache mount is a different filesystem; the base image's interpreter, never a
# downloaded one, so the venv's `python` symlink still resolves in the runtime stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_FROZEN=1 \
    UV_NO_DEV=1

WORKDIR /app

# Everything the two servers and the one-shot `migrate` job import. `ml` is not optional:
# the champion is an XGBoost or LightGBM pipeline, and unpickling it needs the library.
# Left out, because nothing the image runs imports them: nvidia-nccl is xgboost's multi-GPU
# collective (216 MB), loaded only when training on several GPUs; shap and the numba JIT
# under it (about 250 MB) serve `collection explain`, a pipeline step run from a checkout.
ARG UV_SYNC_ARGS="--extra api --extra call --extra voice --extra llm --extra rag --extra ml \
    --extra otel \
    --no-install-package nvidia-nccl-cu13 \
    --no-install-package shap --no-install-package numba --no-install-package llvmlite"

# Dependencies first, without the project: this layer is invalidated by uv.lock, not by
# every source edit, so a code change rebuilds in seconds.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project ${UV_SYNC_ARGS}

# Then the project itself, editable. Editable on purpose: `config.ROOT` is three levels up
# from config.py, and an editable install keeps that /app -- where knowledge/, migrations/
# and data/ are -- instead of a site-packages directory with none of them.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync ${UV_SYNC_ARGS}


FROM build AS weights
# Baked in rather than fetched on first start: a container that downloads a model before it
# can answer has a startup time set by Hugging Face, and a deploy that needs the internet to
# come up healthy has one more way to fail. The image is bigger by about 0.7 GB instead.
#
# Downloaded into cache mounts and then copied out: a lockfile change rebuilds the venv and
# invalidates this stage, and without the cache that would fetch the same gigabyte again.
#
# The embedder is the one `settings` names, so the image and the code cannot disagree about
# it; Whisper `small` is the call server's default checkpoint. Only the agent's voice: the
# second one (`cadu`) is the customer in `make call-bench`, run from a checkout.
RUN --mount=type=cache,target=/cache/huggingface \
    --mount=type=cache,target=/cache/voices \
    HF_HOME=/cache/huggingface /app/.venv/bin/python -c "\
from faster_whisper import download_model; \
from sentence_transformers import SentenceTransformer; \
from collection.config import settings; \
SentenceTransformer(settings.embedding_model); \
download_model('small')" \
    && /app/.venv/bin/python -m piper.download_voices \
        --download-dir /cache/voices pt_BR-faber-medium \
    && mkdir -p /opt/models/voices \
    && cp -a /cache/huggingface /opt/models/huggingface \
    && cp -a /cache/voices/pt_BR-faber-medium.onnx /cache/voices/pt_BR-faber-medium.onnx.json \
        /opt/models/voices/


FROM ${PYTHON_IMAGE} AS runtime

# ffmpeg decodes the browser's Opus and probes clip lengths; libgomp is the OpenMP runtime
# LightGBM and XGBoost link against, which the slim base does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# A fixed UID so a bind-mounted directory can be chowned to it from the host.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app
COPY --from=weights /opt/models /opt/models
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY knowledge ./knowledge
COPY src ./src
# The only place the app writes on disk: call recordings, under data/calls. Everything the
# pipeline produces goes to object storage.
RUN mkdir -p data/calls && chown -R app:app data

# HF_HUB_OFFLINE: the weights are already here, so a cache miss is a bug to surface, not a
# download to start quietly at request time. COLLECTION_HOST: loopback in a container is the
# container's own, so the servers bind every interface and the network decides reach.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models/huggingface \
    HF_HUB_OFFLINE=1 \
    DIR_VOICES=/opt/models/voices \
    COLLECTION_HOST=0.0.0.0

USER app
EXPOSE 8000 8100

# Last, so a new commit reuses every layer above. `/health` reports it.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

CMD ["collection", "serve"]
