#!/usr/bin/env python3
"""Build or incrementally reuse the project-local guideline vector index."""

from argparse import ArgumentParser
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.guidelines.index import DEFAULT_MODEL, build_index  # noqa: E402


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--guideline-dir", default=str(ROOT / "data/guidelines"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = build_index(args.guideline_dir, model_name=args.model, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
