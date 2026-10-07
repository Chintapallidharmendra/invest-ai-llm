#!/bin/sh
# Install approved model weights into /data/models (Story 1.4; ADR-034).
#
#   sudo deploy/models/stage-models.sh <transfer-dir> [model-name ...] [--replace]
#
# <transfer-dir> holds one directory per model, named as in manifest.yaml
# (e.g. <transfer-dir>/Qwen3-14B-AWQ/config.json). Without model names, every model
# in the manifest found in <transfer-dir> is installed.
#
# Every listed file must be present with the exact size and SHA-256 from
# deploy/models/manifest.yaml. ALL selected models are verified before anything is
# installed; any missing file or mismatch aborts with nothing changed. Copies are
# verified again in a staging directory, then moved into place atomically.
#
# Environment: MODELS_DIR (default /data/models), MANIFEST (default manifest.yaml
# next to this script). Requires python3 on the host (stdlib only).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
MANIFEST="${MANIFEST:-$HERE/manifest.yaml}"
MODELS_DIR="${MODELS_DIR:-/data/models}"

if [ "$#" -lt 1 ]; then
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi

exec python3 -I - "$MANIFEST" "$MODELS_DIR" "$@" <<'PY'
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path


def fail(message):
    print(f"stage-models: REFUSED: {message}", file=sys.stderr)
    sys.exit(1)


def parse_manifest(path):
    """Parse the fixed manifest layout (stdlib only; no PyYAML on the host)."""
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
    for m in models:
        for f in m["files"]:
            if set(f) != {"path", "size", "sha256"} or len(f["sha256"]) != 64:
                fail(f"malformed manifest entry for {m['name']}: {f}")
            if f["path"].startswith("/") or ".." in Path(f["path"]).parts:
                fail(f"unsafe path in manifest: {f['path']}")
            f["size"] = int(f["size"])
        if not m["files"]:
            fail(f"manifest lists no files for {m['name']}")
    return models


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(model, root):
    """Return a list of problems for model files under root (empty = verified)."""
    problems = []
    for f in model["files"]:
        path = root / f["path"]
        if not path.is_file() or path.is_symlink():
            problems.append(f"missing {f['path']}")
            continue
        size = path.stat().st_size
        if size != f["size"]:
            problems.append(f"size mismatch {f['path']} ({size} != {f['size']})")
            continue
        if sha256_of(path) != f["sha256"]:
            problems.append(f"sha256 mismatch {f['path']}")
    return problems


def main():
    manifest_path, models_dir, transfer, *rest = sys.argv[1:]
    replace = "--replace" in rest
    names = [a for a in rest if a != "--replace"]
    models_dir, transfer = Path(models_dir), Path(transfer)

    if str(models_dir.resolve()).startswith("/mnt"):
        fail("MODELS_DIR is on /mnt (ephemeral Azure temp disk)")
    if not transfer.is_dir():
        fail(f"transfer dir not found: {transfer}")

    manifest = {m["name"]: m for m in parse_manifest(manifest_path)}
    unknown = [n for n in names if n not in manifest]
    if unknown:
        fail(f"not in manifest (not approved): {', '.join(unknown)}")
    selected = [manifest[n] for n in names] if names else [
        m for m in manifest.values() if (transfer / m["name"]).is_dir()
    ]
    if not selected:
        fail(f"no approved model directories found in {transfer}")

    # 1. Verify every selected model before touching MODELS_DIR.
    for m in selected:
        print(f"verifying {m['name']} @ {m.get('revision', '?')} ({len(m['files'])} files)")
        problems = verify(m, transfer / m["name"])
        if problems:
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            fail(f"{m['name']} failed verification; nothing installed")

    # 2. Copy to a staging dir, re-verify the copy, then rename into place.
    models_dir.mkdir(parents=True, exist_ok=True)
    for m in selected:
        target = models_dir / m["name"]
        if target.exists():
            if not verify(m, target):
                print(f"{m['name']}: already installed and verified, skipping")
                continue
            if not replace:
                fail(f"{target} exists and differs; rerun with --replace")
        staging = models_dir / f".staging-{m['name']}-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            for f in m["files"]:
                dest = staging / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(transfer / m["name"] / f["path"], dest)
                dest.chmod(0o444)
            problems = verify(m, staging)
            if problems:
                fail(f"{m['name']}: copy failed verification ({problems[0]})")
            (staging / ".manifest-verified").write_text(
                f"{m['name']} {m.get('revision', '?')} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            )
            for d in [staging, *[p for p in staging.rglob("*") if p.is_dir()]]:
                d.chmod(0o755)
            if target.exists():
                old = models_dir / f".old-{m['name']}-{os.getpid()}"
                target.rename(old)
                staging.rename(target)
                shutil.rmtree(old)
            else:
                staging.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        print(f"{m['name']}: installed to {target}")

    print("stage-models: OK")


main()
PY
