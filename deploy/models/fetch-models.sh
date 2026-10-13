#!/bin/sh
# Download approved model weights into a transfer directory (connected machine only).
#
#   deploy/models/fetch-models.sh <transfer-dir> [model-name ...]
#
# Fetches every file listed in manifest.yaml from the model's pinned Hugging Face
# revision into <transfer-dir>/<model-name>/, the layout stage-models.sh expects on the
# host. Each file is checked against the manifest size and SHA-256; a mismatch aborts.
# Files already present with the right hash are skipped, so re-runs resume.
# Without model names, fetches the models the stack runs by default: the chat and guard
# models in deploy/vllm/*.env and the two Docling models.
#
# Environment: MANIFEST (default manifest.yaml next to this script), HF_TOKEN (optional).
# Requires python3 (stdlib only).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
MANIFEST="${MANIFEST:-$HERE/manifest.yaml}"

if [ "$#" -lt 1 ]; then
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi

exec python3 -I - "$MANIFEST" "$@" <<'PY'
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT = [
    "Qwen3-14B-AWQ",
    "Qwen3Guard-Gen-0.6B",
    "docling-project--docling-layout-heron",
    "docling-project--docling-models",
]


def fail(message):
    print(f"fetch-models: FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def parse_manifest(path):
    """Same fixed layout as stage-models.sh parses (stdlib only)."""
    models, model, entry = [], None, None
    for raw in Path(path).read_text().splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 2 and text.startswith("- name:"):
            model = {"name": text.split(":", 1)[1].strip(), "files": []}
            models.append(model)
        elif indent == 4 and model is not None and ":" in text and not text.startswith("files"):
            key, value = text.split(":", 1)
            model[key.strip()] = value.strip()
        elif indent == 6 and text.startswith("- path:"):
            entry = {"path": text.split(":", 1)[1].strip()}
            model["files"].append(entry)
        elif indent == 8 and entry is not None:
            key, value = text.split(":", 1)
            entry[key.strip()] = value.strip()
    return {m["name"]: m for m in models}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ok(path, f):
    return path.is_file() and path.stat().st_size == int(f["size"]) and sha256(path) == f["sha256"]


def download(url, dest):
    request = urllib.request.Request(url)
    if os.environ.get("HF_TOKEN"):
        request.add_header("Authorization", f"Bearer {os.environ['HF_TOKEN']}")
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(request) as response, open(part, "wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    part.replace(dest)


def main():
    manifest_path, transfer, *names = sys.argv[1:]
    manifest = parse_manifest(manifest_path)
    names = names or DEFAULT
    unknown = [n for n in names if n not in manifest]
    if unknown:
        fail(f"not in manifest: {', '.join(unknown)}")
    for name in names:
        m = manifest[name]
        base = f"{m['source'].rstrip('/')}/resolve/{m['revision']}"
        print(f"{name} @ {m['revision']} ({len(m['files'])} files)")
        for f in m["files"]:
            dest = Path(transfer) / name / f["path"]
            if ok(dest, f):
                print(f"  ok      {f['path']}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            print(f"  fetch   {f['path']} ({int(f['size']) / 1e9:.2f} GB)", flush=True)
            download(f"{base}/{f['path']}", dest)
            if not ok(dest, f):
                dest.unlink()
                fail(f"{name}/{f['path']}: size or SHA-256 does not match the manifest")
    print("fetch-models: OK")


main()
PY
