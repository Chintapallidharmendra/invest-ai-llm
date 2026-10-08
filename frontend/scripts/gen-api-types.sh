#!/bin/sh
# Generate src/api/schema.d.ts from the backend OpenAPI document (ADR-002).
# The output is generated: never edit it by hand, re-run this instead.
#
#   sh frontend/scripts/gen-api-types.sh               # export from backend/ via uv
#   sh frontend/scripts/gen-api-types.sh openapi.json  # use an exported file
#
# Env: UV (default `uv`) runs `python -m app.api.export_openapi` in backend/.
set -eu

FRONTEND_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO_DIR=$(dirname -- "$FRONTEND_DIR")
OUT="$FRONTEND_DIR/src/api/schema.d.ts"
OPENAPI_TS="$FRONTEND_DIR/node_modules/.bin/openapi-typescript"
UV=${UV:-uv}

if [ ! -x "$OPENAPI_TS" ]; then
  echo "gen-api-types: $OPENAPI_TS not found; install frontend dependencies first." >&2
  exit 1
fi

if [ $# -ge 1 ]; then
  SPEC=$1
  [ -f "$SPEC" ] || { echo "gen-api-types: no such file: $SPEC" >&2; exit 1; }
else
  TMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
  SPEC="$TMP_DIR/openapi.json"
  (cd "$REPO_DIR/backend" && $UV run python -m app.api.export_openapi) > "$SPEC"
fi

"$OPENAPI_TS" "$SPEC" --output "$OUT"
echo "gen-api-types: wrote ${OUT#"$REPO_DIR"/}"
