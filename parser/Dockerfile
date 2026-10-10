# syntax=docker/dockerfile:1
# Parser image (Story 9.2; ADR-027). Build off-host (internet needed for wheels), then
# ship with `docker save`/`docker load` like the backend image. Nothing is downloaded at
# runtime: Docling's models are mounted read-only at /models/docling.

ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
# Exactly the locked versions (--frozen): the lock is the approved dependency set.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime
# OpenCV (pulled in by RapidOCR/Docling) needs these shared libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 parser \
    && useradd --system --uid 10001 --gid parser --home-dir /nonexistent --shell /usr/sbin/nologin parser
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY --chown=root:root app ./app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DOCLING_ARTIFACTS_PATH=/models/docling \
    # Libraries that cache to $HOME write to the tmpfs instead (the root fs is read-only).
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app"]
