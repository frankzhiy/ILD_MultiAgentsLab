#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.common.specialty_input import build_specialty_case_input
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge semantic-graph JSON outputs into one ordered specialty-agent input."
    )
    parser.add_argument("--run-dir", required=True, help="Semantic graphing run directory.")
    parser.add_argument(
        "--specialty",
        required=True,
        choices=[
            specialty.value
            for specialty in MdtSpecialty
            if specialty != MdtSpecialty.SHARED_CONTEXT
        ],
    )
    parser.add_argument(
        "--case-id", default=None, help="Case prefix when a run has multiple cases."
    )
    parser.add_argument("--output", default=None, help="Output JSON path.")
    args = parser.parse_args()

    result = build_specialty_case_input(
        args.run_dir,
        args.specialty,
        case_id=args.case_id,
    )
    output = (
        Path(args.output)
        if args.output
        else Path(args.run_dir) / (f"{result.case_id}_{result.target_specialty.value}_input.json")
    )
    output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output.resolve())
    print(
        f"segments={result.summary.segment_count} units={result.summary.unit_count} "
        f"owned={result.summary.owned_unit_count} "
        f"shared={result.summary.shared_context_unit_count} "
        f"reference={result.summary.reference_only_unit_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
