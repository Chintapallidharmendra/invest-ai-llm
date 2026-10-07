# syntax=docker/dockerfile:1
# Caddy with the built SPA baked in. Build context: repository root.
#   docker build -f deploy/caddy/Dockerfile -t invest-ai-llm/caddy .
# Build off-host (npm registry needed), then ship with `docker save`/`docker load`.

ARG NODE_IMAGE=node:22-slim
ARG CADDY_IMAGE=caddy:2

FROM ${NODE_IMAGE} AS spa
ARG PNPM_VERSION=12.9.1
RUN npm install --global "pnpm@${PNPM_VERSION}"
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# Until the SPA shell exists (Story 1.10), ship a placeholder page.
RUN if [ -f index.html ]; then \
        pnpm build; \
    else \
        mkdir -p dist \
        && printf '%s\n' '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>invest-ai-llm</title></head><body><p>invest-ai-llm: frontend not built yet.</p></body></html>' > dist/index.html; \
    fi

FROM ${CADDY_IMAGE}
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=spa /frontend/dist /srv
