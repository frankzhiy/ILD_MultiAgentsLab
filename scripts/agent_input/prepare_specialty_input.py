#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.schemas.semantic_graphing.clinical_proposition import (  # noqa: E402
    DocumentClinicalPropositions,
)
from src.schemas.semantic_graphing.document import DocumentClassification  # noqa: E402
from src.schemas.semantic_graphing.graph_unit import (  # noqa: E402
    DocumentGraphUnits,
    MdtSpecialty,
)
from src.schemas.semantic_graphing.local_graph import (  # noqa: E402
    DocumentLocalGraphs,
    LocalGraphBuildStatus,
)
from src.schemas.semantic_graphing.primary_frame import (  # noqa: E402
    DocumentPrimaryFrames,
)
from src.schemas.semantic_graphing.proposition_validation import (  # noqa: E402
    DocumentPropositionValidation,
)
from src.schemas.specialty_agent_input import (  # noqa: E402
    EvidenceRole,
    SpecialtyCaseInput,
    SpecialtyCaseSummary,
    SpecialtySegmentInput,
    SpecialtyUnitInput,
)


_FILE_MODELS = {
    "discourse_segments": DocumentClassification,
    "graph_units": DocumentGraphUnits,
    "primary_frames": DocumentPrimaryFrames,
    "clinical_propositions": DocumentClinicalPropositions,
    "proposition_validation": DocumentPropositionValidation,
    "local_graphs": DocumentLocalGraphs,
}

_ALLOWED_USES = {
    EvidenceRole.OWNED: [
        "diagnostic_support",
        "clinical_interpretation",
        "specialist_question",
    ],
    EvidenceRole.SHARED_CONTEXT: [
        "diagnostic_support",
        "contextual_support",
        "clinical_interpretation",
        "specialist_question",
    ],
    EvidenceRole.REFERENCE_ONLY: [
        "case_orientation",
        "related_evidence",
        "specialist_question",
    ],
}


def build_specialty_case_input(
    run_dir: str | Path,
    target_specialty: MdtSpecialty | str,
    *,
    case_id: str | None = None,
) -> SpecialtyCaseInput:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")
    specialty = MdtSpecialty(target_specialty)
    if specialty == MdtSpecialty.SHARED_CONTEXT:
        raise ValueError("shared_context is broadcast context, not a target specialty")
    case_id = case_id or _discover_case_id(run_dir)
    documents = _load_documents(run_dir, case_id)

    classification: DocumentClassification = documents["discourse_segments"]
    graph_units: DocumentGraphUnits = documents["graph_units"]
    primary_frames: DocumentPrimaryFrames = documents["primary_frames"]
    propositions: DocumentClinicalPropositions = documents["clinical_propositions"]
    validations: DocumentPropositionValidation = documents["proposition_validation"]
    local_graphs: DocumentLocalGraphs = documents["local_graphs"]

    graph_segments = _index_unique(graph_units.segments, "segment_id", "graph-unit segments")
    frame_segments = _index_unique(primary_frames.segments, "segment_id", "primary-frame segments")
    proposition_segments = _index_unique(
        propositions.segments,
        "segment_id",
        "clinical-proposition segments",
    )
    validation_segments = _index_unique(
        validations.segments,
        "segment_id",
        "proposition-validation segments",
    )
    local_graph_segments = _index_unique(
        local_graphs.segments,
        "segment_id",
        "local-graph segments",
    )
    expected_segment_ids = [segment.segment_id for segment in classification.segments]
    _require_unique(expected_segment_ids, "discourse segment IDs")
    for label, indexed in (
        ("graph units", graph_segments),
        ("primary frames", frame_segments),
        ("clinical propositions", proposition_segments),
        ("proposition validation", validation_segments),
        ("local graphs", local_graph_segments),
    ):
        _require_same_ids(expected_segment_ids, indexed, label)

    merged_segments: list[SpecialtySegmentInput] = []
    role_counts: Counter[EvidenceRole] = Counter()
    locator_counts: Counter[str] = Counter()
    for segment_index, segment in enumerate(classification.segments, start=1):
        segment_id = segment.segment_id
        graph_segment = graph_segments[segment_id]
        frame_by_unit = _index_unique(
            frame_segments[segment_id].units,
            "graph_unit_id",
            f"{segment_id} primary frames",
        )
        proposition_by_unit = _index_unique(
            proposition_segments[segment_id].units,
            "graph_unit_id",
            f"{segment_id} clinical propositions",
        )
        validation_by_unit = _index_unique(
            validation_segments[segment_id].units,
            "graph_unit_id",
            f"{segment_id} proposition validation",
        )
        local_graph_by_unit = _index_unique(
            local_graph_segments[segment_id].units,
            "graph_unit_id",
            f"{segment_id} local graphs",
        )
        ordered_unit_ids = [unit.graph_unit_id for unit in graph_segment.graph_units]
        _require_unique(ordered_unit_ids, f"{segment_id} graph-unit IDs")
        for label, indexed in (
            ("primary frames", frame_by_unit),
            ("clinical propositions", proposition_by_unit),
            ("proposition validation", validation_by_unit),
            ("local graphs", local_graph_by_unit),
        ):
            _require_same_ids(ordered_unit_ids, indexed, f"{segment_id} {label}")

        merged_units = []
        for unit_index, unit in enumerate(graph_segment.graph_units, start=1):
            if unit.segment_id != segment_id:
                raise ValueError(
                    f"Graph unit {unit.graph_unit_id} belongs to {unit.segment_id}, "
                    f"not parent segment {segment_id}"
                )
            unit_id = unit.graph_unit_id
            primary_frame = frame_by_unit[unit_id]
            if (
                unit.primary_frame is not None
                and unit.primary_frame != primary_frame.primary_frame
            ):
                raise ValueError(
                    f"Graph unit {unit_id} embeds primary_frame={unit.primary_frame}, "
                    f"but primary_frames output contains {primary_frame.primary_frame}"
                )
            local_graph = local_graph_by_unit[unit_id]
            if local_graph.segment_id != segment_id:
                raise ValueError(
                    f"Local graph {unit_id} belongs to {local_graph.segment_id}, "
                    f"not parent segment {segment_id}"
                )
            role = _evidence_role(unit.mdt_specialty, specialty)
            validation = validation_by_unit[unit_id]
            locator_status = (
                "available"
                if validation.is_graph_ready
                and local_graph.build_status == LocalGraphBuildStatus.BUILT
                else "degraded"
            )
            role_counts[role] += 1
            locator_counts[locator_status] += 1
            merged_units.append(
                SpecialtyUnitInput(
                    segment_index=segment_index,
                    unit_index=unit_index,
                    evidence_role=role,
                    may_support_diagnostic_claim=(
                        "diagnostic_support" in _ALLOWED_USES[role]
                    ),
                    allowed_uses=_ALLOWED_USES[role],
                    locator_status=locator_status,
                    graph_unit=unit,
                    primary_frame=primary_frame,
                    clinical_propositions=proposition_by_unit[unit_id],
                    proposition_validation=validation,
                    local_graph=local_graph,
                )
            )
        merged_segments.append(
            SpecialtySegmentInput(
                segment_index=segment_index,
                segment=segment,
                units=merged_units,
            )
        )

    unit_count = sum(len(segment.units) for segment in merged_segments)
    return SpecialtyCaseInput(
        case_id=case_id,
        target_specialty=specialty,
        source_run_dir=str(run_dir.resolve()),
        segments=merged_segments,
        summary=SpecialtyCaseSummary(
            segment_count=len(merged_segments),
            unit_count=unit_count,
            owned_unit_count=role_counts[EvidenceRole.OWNED],
            shared_context_unit_count=role_counts[EvidenceRole.SHARED_CONTEXT],
            reference_only_unit_count=role_counts[EvidenceRole.REFERENCE_ONLY],
            available_locator_count=locator_counts["available"],
            degraded_locator_count=locator_counts["degraded"],
        ),
    )


def _discover_case_id(run_dir: Path) -> str:
    suffix = "_discourse_segments.json"
    case_ids = sorted(path.name[: -len(suffix)] for path in run_dir.glob(f"*{suffix}"))
    if len(case_ids) != 1:
        raise ValueError(
            f"Expected exactly one case in {run_dir}; found {case_ids}. Use --case-id."
        )
    return case_ids[0]


def _load_documents(run_dir: Path, case_id: str) -> dict[str, Any]:
    documents = {}
    for suffix, model in _FILE_MODELS.items():
        path = run_dir / f"{case_id}_{suffix}.json"
        if not path.exists():
            raise FileNotFoundError(f"Required semantic output not found: {path}")
        documents[suffix] = model.model_validate_json(path.read_text(encoding="utf-8"))
    return documents


def _index_unique(items: list[Any], attribute: str, label: str) -> dict[str, Any]:
    indexed = {}
    for item in items:
        item_id = str(getattr(item, attribute))
        if item_id in indexed:
            raise ValueError(f"{label} contain duplicate {attribute} {item_id}")
        indexed[item_id] = item
    return indexed


def _require_unique(ids: list[str], label: str) -> None:
    duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"{label} contain duplicates: {duplicates}")


def _require_same_ids(expected_ids: list[str], indexed: dict[str, Any], label: str) -> None:
    expected = set(expected_ids)
    actual = set(indexed)
    if expected != actual:
        raise ValueError(
            f"{label} do not align; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _evidence_role(
    specialties: list[MdtSpecialty],
    target: MdtSpecialty,
) -> EvidenceRole:
    if MdtSpecialty.SHARED_CONTEXT in specialties:
        return EvidenceRole.SHARED_CONTEXT
    if target in specialties:
        return EvidenceRole.OWNED
    return EvidenceRole.REFERENCE_ONLY


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
