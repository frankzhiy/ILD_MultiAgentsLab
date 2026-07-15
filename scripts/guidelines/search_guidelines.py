#!/usr/bin/env python3
"""Inspect persisted guideline retrieval from the command line."""

from argparse import ArgumentParser
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.guidelines.catalog import load_catalog  # noqa: E402
from src.guidelines.retrieval import GuidelineRetriever  # noqa: E402


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    guideline_dir = ROOT / "data/guidelines"
    ids = list(load_catalog(guideline_dir / "catalog.yaml"))
    hits = GuidelineRetriever(guideline_dir).search(args.query, guideline_ids=ids, limit=args.limit)
    print(json.dumps([hit.model_dump(mode="json") for hit in hits], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
