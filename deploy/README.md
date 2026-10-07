# deploy

Single-host, zero-egress Docker Compose deployment (ADR-034): Caddy (HTTPS on **8043**),
API, worker, parser, PostgreSQL 18 + pgvector, Valkey, `vllm-chat` and `vllm-guard` on the A10.

- `compose.yml` arrives in Story 1.3; `make up` / `make down` use it.
- `secrets/` holds runtime secrets on the host only. Everything in it except its README is
  git-ignored. Never commit secrets (gitleaks runs in pre-commit and CI).
- Backups live under `/data/backups` on the host, never in this repo (ADR-035).
