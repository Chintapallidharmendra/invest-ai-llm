"""Verify the generated corpus against manifest.yaml (Story 1.7).

    uv run --project ../../backend python check_corpus.py [--build build] [--manifest manifest.yaml]

Fails (exit 1) if a file in build/ is not in the manifest (or the reverse), a hash
differs, or any planted item, figure, total or difference is not where the manifest
says it is.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from generate.build import DEFAULT_MANIFEST, DEFAULT_OUT, load
from generate.verify import verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    manifest = load(args.manifest)
    problems = verify(args.build, manifest)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(f"check_corpus: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    kinds = Counter(i.kind for i in manifest.items)
    print(
        f"check_corpus: OK - {len(manifest.documents)} documents, "
        f"{kinds['identifier']} identifiers, {kinds['benign']} benign, "
        f"{kinds['injection']} injections, {kinds['canary']} canaries, "
        f"{len(manifest.term_sheet_diff)} term-sheet differences, "
        f"{len(manifest.workbook_totals)} workbook totals"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
