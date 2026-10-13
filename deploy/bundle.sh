#!/bin/sh
# Build the offline bundle for the zero-egress GPU host (runbook §3; ADR-034).
#
#   deploy/bundle.sh [out-dir]                 # default: ./bundle-<APP_VERSION>
#
# Run on a connected machine with Docker (Compose >= 2.20, buildx). Produces:
#   images-app-<v>.tar.gz    backend, caddy, parser, pgvector, valkey
#   images-vllm-<v>.tar.gz   vllm/vllm-openai (large, ~12 GB)
#   models/                  approved weights (deploy/models/fetch-models.sh)
#   src-<v>.tar.gz           the repository at HEAD (compose files, runbook, scripts)
#   SHA256SUMS, INSTALL.txt
# Images are built and pulled for PLATFORM (the host is x86_64); on Apple Silicon the
# builds run under emulation and take a while.
#
# Environment: APP_VERSION (default <date>-<git sha>), PLATFORM (linux/amd64),
# VLLM_VERSION (v0.31.0-cu129), MODELS (manifest names, default set in fetch-models.sh),
# SKIP_VLLM=1, SKIP_MODELS=1.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PLATFORM="${PLATFORM:-linux/amd64}"
VLLM_VERSION="${VLLM_VERSION:-v0.31.0-cu129}"
APP_VERSION="${APP_VERSION:-$(date +%Y.%m.%d)-$(git rev-parse --short HEAD)}"
OUT="${1:-$ROOT/bundle-$APP_VERSION}"

APP_IMAGES="invest-ai-llm/backend:$APP_VERSION invest-ai-llm/caddy:$APP_VERSION invest-ai-llm/parser:$APP_VERSION"
BASE_IMAGES="pgvector/pgvector:pg18 valkey/valkey:8"
VLLM_IMAGE="vllm/vllm-openai:$VLLM_VERSION"

step() { printf '\n==> %s\n' "$*"; }

if [ -n "$(git status --porcelain)" ]; then
    echo "bundle: working tree has uncommitted changes; images include them, src tarball does not." >&2
fi
mkdir -p "$OUT"

step "build app images ($APP_VERSION, $PLATFORM)"
docker buildx build --platform "$PLATFORM" --load -t "invest-ai-llm/backend:$APP_VERSION" backend
docker buildx build --platform "$PLATFORM" --load -t "invest-ai-llm/parser:$APP_VERSION" parser
docker buildx build --platform "$PLATFORM" --load -t "invest-ai-llm/caddy:$APP_VERSION" \
    -f deploy/caddy/Dockerfile .

step "pull third-party images ($PLATFORM)"
pulled="$BASE_IMAGES"
[ "${SKIP_VLLM:-}" = 1 ] || pulled="$pulled $VLLM_IMAGE"
for image in $pulled; do
    docker pull --platform "$PLATFORM" "$image"
done

step "check image platforms"
for image in $APP_IMAGES $pulled; do
    got=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
    [ "$got" = "$PLATFORM" ] || { echo "bundle: $image is $got, expected $PLATFORM" >&2; exit 1; }
    echo "  $got  $image"
done

step "save images"
# shellcheck disable=SC2086
docker save --platform "$PLATFORM" $APP_IMAGES $BASE_IMAGES | gzip -1 > "$OUT/images-app-$APP_VERSION.tar.gz"
if [ "${SKIP_VLLM:-}" != 1 ]; then
    docker save --platform "$PLATFORM" "$VLLM_IMAGE" | gzip -1 > "$OUT/images-vllm-$APP_VERSION.tar.gz"
fi

# The pull above replaced the local pgvector/valkey tags with the host platform; put the
# native ones back so local development keeps running without emulation.
native="linux/$(docker version --format '{{.Server.Arch}}')"
if [ "$native" != "$PLATFORM" ]; then
    step "restore native $native base images"
    for image in $BASE_IMAGES; do docker pull --platform "$native" "$image"; done
fi

if [ "${SKIP_MODELS:-}" != 1 ]; then
    step "fetch model weights"
    # shellcheck disable=SC2086
    deploy/models/fetch-models.sh "$OUT/models" ${MODELS:-}
fi

step "pack repository"
git archive --format=tar.gz --prefix=invest-ai-llm/ -o "$OUT/src-$APP_VERSION.tar.gz" HEAD

cat > "$OUT/INSTALL.txt" <<EOF
invest-ai-llm offline bundle $APP_VERSION ($PLATFORM)

Host prerequisites (cannot be shipped as images): Docker Engine with Compose >= 2.20,
NVIDIA driver, NVIDIA Container Toolkit, python3. Check:
  docker compose version; nvidia-smi; docker info | grep -i nvidia; python3 -V

On the host, as root, from the copied bundle directory:
  sha256sum -c SHA256SUMS                       # every line must print OK
  docker load -i images-app-$APP_VERSION.tar.gz
  docker load -i images-vllm-$APP_VERSION.tar.gz
  tar -xzf src-$APP_VERSION.tar.gz -C /opt      # -> /opt/invest-ai-llm
  /opt/invest-ai-llm/deploy/models/stage-models.sh ./models
Then follow /opt/invest-ai-llm/deploy/runbook.md (secrets §2, config §4 with
APP_VERSION=$APP_VERSION and VLLM_VERSION=$VLLM_VERSION in deploy/.env, start §5).
EOF

step "checksums"
(cd "$OUT" && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | sort \
    | while read -r f; do shasum -a 256 "$f"; done > SHA256SUMS)
du -sh "$OUT"
echo "bundle: OK -> $OUT"
