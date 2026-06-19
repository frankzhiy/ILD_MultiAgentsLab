"""Deterministic quality gate for extracted clinical propositions."""

from collections import Counter, defaultdict

from src.agents.semantic_graphing.clinical_proposition_extractor import build_evidence_blocks
from src.schemas.semantic_graphing.clinical_proposition import (
    DocumentClinicalPropositions,
    EvidenceBlock,
    EvidenceReference,
    GraphUnitClinicalPropositions,
    ModifierType,
    PropositionType,
)
from src.schemas.semantic_graphing.graph_unit import DocumentGraphUnits, GraphUnit
from src.schemas.semantic_graphing.primary_frame import (
    DocumentPrimaryFrames,
    GraphUnitPrimaryFrame,
    PrimaryFrame,
)
from src.schemas.semantic_graphing.proposition_validation import (
    DocumentPropositionValidation,
    GraphUnitPropositionValidation,
    PropositionValidationIssue,
    PropositionValidationMetrics,
    PropositionValidationSummary,
    SegmentPropositionValidation,
    ValidationSeverity,
)


_LOCAL_MODIFIER_TYPES = {
    ModifierType.QUANTITY,
    ModifierType.INTENSITY,
    ModifierType.VALUE,
    ModifierType.UNIT,
    ModifierType.RANGE,
    ModifierType.TREND,
    ModifierType.COLOR,
    ModifierType.CONSISTENCY,
    ModifierType.QUALITY,
    ModifierType.ANATOMICAL_SITE,
    ModifierType.LATERALITY,
    ModifierType.DOSE,
    ModifierType.ROUTE,
    ModifierType.SCHEDULE,
    ModifierType.RESPONSE,
    ModifierType.PURPOSE,
    ModifierType.METHOD,
}

_EXPECTED_PROPOSITION_TYPES: dict[PrimaryFrame, set[PropositionType]] = {
    PrimaryFrame.SYMPTOM_EPISODE: {
        PropositionType.SYMPTOM,
        PropositionType.SIGN,
        PropositionType.OUTCOME,
    },
    PrimaryFrame.STANDALONE_EXAMINATION: {
        PropositionType.EXAMINATION,
        PropositionType.MEASUREMENT,
        PropositionType.FINDING,
        PropositionType.SIGN,
    },
    PrimaryFrame.CLINICAL_ASSESSMENT: {
        PropositionType.DIAGNOSIS_ASSERTION,
        PropositionType.PLAN,
    },
    PrimaryFrame.TREATMENT_COURSE: {
        PropositionType.TREATMENT,
        PropositionType.PROCEDURE,
        PropositionType.MEDICATION,
        PropositionType.OUTCOME,
    },
    PrimaryFrame.BACKGROUND_CONTEXT: {
        PropositionType.DEMOGRAPHIC,
        PropositionType.EXPOSURE,
        PropositionType.BACKGROUND_CONDITION,
        PropositionType.FINDING,
        PropositionType.PROCEDURE,
        PropositionType.INFORMATION_AVAILABILITY,
    },
}

_ATTRIBUTION_CUES = ("当地", "外院", "本院", "医院", "医生", "医师", "诊断条目")


class ClinicalPropositionValidator:
    """Validate document alignment, provenance, and graph-readiness."""

    def validate_document(
        self,
        graph_units: DocumentGraphUnits,
        primary_frames: DocumentPrimaryFrames,
        clinical_propositions: DocumentClinicalPropositions,
    ) -> DocumentPropositionValidation:
        graph_segments = _index_segments(graph_units.segments, "graph units")
        frame_segments = _index_segments(primary_frames.segments, "primary frames")
        proposition_segments = _index_segments(
            clinical_propositions.segments,
            "clinical propositions",
        )
        _require_same_ids(
            set(graph_segments),
            set(frame_segments),
            "graph-unit and primary-frame segment IDs",
        )
        _require_same_ids(
            set(graph_segments),
            set(proposition_segments),
            "graph-unit and proposition segment IDs",
        )

        validated_segments: list[SegmentPropositionValidation] = []
        for graph_segment in graph_units.segments:
            segment_id = graph_segment.segment_id
            frame_segment = frame_segments[segment_id]
            proposition_segment = proposition_segments[segment_id]
            frame_by_unit = _index_units(frame_segment.units, segment_id, "primary frames")
            propositions_by_unit = _index_units(
                proposition_segment.units,
                segment_id,
                "clinical propositions",
            )
            graph_unit_ids = {unit.graph_unit_id for unit in graph_segment.graph_units}
            _require_same_ids(
                graph_unit_ids,
                set(frame_by_unit),
                f"{segment_id} graph-unit and primary-frame IDs",
            )
            _require_same_ids(
                graph_unit_ids,
                set(propositions_by_unit),
                f"{segment_id} graph-unit and proposition IDs",
            )
            validated_segments.append(
                SegmentPropositionValidation(
                    segment_id=segment_id,
                    units=[
                        self.validate_unit(
                            unit,
                            frame_by_unit[unit.graph_unit_id],
                            propositions_by_unit[unit.graph_unit_id],
                        )
                        for unit in graph_segment.graph_units
                    ],
                )
            )

        issues = [
            issue
            for segment in validated_segments
            for unit in segment.units
            for issue in unit.issues
        ]
        counts = Counter(issue.severity for issue in issues)
        unit_count = sum(len(segment.units) for segment in validated_segments)
        graph_ready_unit_count = sum(
            unit.is_graph_ready for segment in validated_segments for unit in segment.units
        )
        summary = PropositionValidationSummary(
            segment_count=len(validated_segments),
            unit_count=unit_count,
            graph_ready_unit_count=graph_ready_unit_count,
            error_count=counts[ValidationSeverity.ERROR],
            warning_count=counts[ValidationSeverity.WARNING],
            info_count=counts[ValidationSeverity.INFO],
        )
        return DocumentPropositionValidation(
            is_graph_ready=summary.error_count == 0,
            summary=summary,
            segments=validated_segments,
        )

    def validate_unit(
        self,
        unit: GraphUnit,
        primary_frame: GraphUnitPrimaryFrame,
        propositions: GraphUnitClinicalPropositions,
    ) -> GraphUnitPropositionValidation:
        issues: list[PropositionValidationIssue] = []
        if primary_frame.graph_unit_id != unit.graph_unit_id:
            issues.append(
                _issue(
                    "primary_frame_unit_mismatch",
                    ValidationSeverity.ERROR,
                    "Primary-frame selection belongs to a different graph unit.",
                )
            )
        if propositions.graph_unit_id != unit.graph_unit_id:
            issues.append(
                _issue(
                    "proposition_unit_mismatch",
                    ValidationSeverity.ERROR,
                    "Clinical propositions belong to a different graph unit.",
                )
            )
        if propositions.primary_frame != primary_frame.primary_frame:
            issues.append(
                _issue(
                    "primary_frame_mismatch",
                    ValidationSeverity.ERROR,
                    "Clinical propositions and primary-frame selection disagree.",
                )
            )

        proposition_ids: set[str] = set()
        modifier_ids: set[str] = set()
        proposition_signatures: set[tuple] = set()
        shared_modifier_evidence: dict[tuple[tuple[str, ...], str, str], list[str]] = defaultdict(list)
        referenced_evidence_ids: set[str] = set()

        if propositions.evidence_blocks != build_evidence_blocks(unit):
            issues.append(
                _issue(
                    "evidence_blocks_mismatch",
                    ValidationSeverity.ERROR,
                    "Evidence blocks do not match deterministic graph-unit evidence blocks.",
                )
            )
        evidence_by_id = {block.evidence_id: block for block in propositions.evidence_blocks}

        if not propositions.propositions:
            issues.append(
                _issue(
                    "no_propositions",
                    ValidationSeverity.ERROR,
                    "Graph unit has no clinical propositions.",
                )
            )

        for proposition in propositions.propositions:
            proposition_id = proposition.proposition_id
            if proposition_id in proposition_ids:
                issues.append(
                    _issue(
                        "duplicate_proposition_id",
                        ValidationSeverity.ERROR,
                        f"Duplicate proposition_id {proposition_id}.",
                        proposition_id=proposition_id,
                    )
                )
            proposition_ids.add(proposition_id)
            _validate_evidence_reference(
                proposition.evidence,
                propositions.evidence_blocks,
                issues,
                owner_code="proposition",
                proposition_id=proposition_id,
            )
            referenced_evidence_ids.update(proposition.evidence.evidence_ids)
            signature = (
                proposition.proposition_type,
                proposition.concept_text,
                proposition.status,
                tuple(proposition.evidence.evidence_ids),
                proposition.evidence.quote,
            )
            if signature in proposition_signatures:
                issues.append(
                    _issue(
                        "duplicate_proposition",
                        ValidationSeverity.ERROR,
                        "An identical clinical proposition is repeated.",
                        proposition_id=proposition_id,
                    )
                )
            proposition_signatures.add(signature)

            if proposition.attribution is not None:
                attribution = proposition.attribution
                _validate_evidence_reference(
                    attribution.evidence,
                    propositions.evidence_blocks,
                    issues,
                    owner_code="attribution",
                    proposition_id=proposition_id,
                )
                referenced_evidence_ids.update(attribution.evidence.evidence_ids)
                if attribution.actor_text not in attribution.evidence.quote:
                    issues.append(
                        _issue(
                            "actor_outside_attribution_span",
                            ValidationSeverity.ERROR,
                            "Attribution actor_text is not contained in its source span.",
                            proposition_id=proposition_id,
                        )
                    )
            elif (
                proposition.proposition_type == PropositionType.DIAGNOSIS_ASSERTION
                and any(cue in proposition.evidence.quote for cue in _ATTRIBUTION_CUES)
            ):
                issues.append(
                    _issue(
                        "possible_missing_attribution",
                        ValidationSeverity.WARNING,
                        "Diagnosis assertion contains an attribution cue but has no attribution.",
                        proposition_id=proposition_id,
                    )
                )

            owner_modifier_signatures: set[tuple] = set()
            for modifier in proposition.modifiers:
                _validate_modifier(
                    modifier,
                    propositions.evidence_blocks,
                    modifier_ids,
                    issues,
                    proposition_id=proposition_id,
                )
                referenced_evidence_ids.update(modifier.evidence.evidence_ids)
                shared_modifier_evidence[
                    (
                        tuple(modifier.evidence.evidence_ids),
                        modifier.evidence.quote,
                        modifier.value_text,
                    )
                ].append(proposition_id)
                modifier_signature = (
                    modifier.modifier_type,
                    modifier.value_text,
                    tuple(modifier.evidence.evidence_ids),
                    modifier.evidence.quote,
                )
                if modifier_signature in owner_modifier_signatures:
                    issues.append(
                        _issue(
                            "duplicate_modifier",
                            ValidationSeverity.ERROR,
                            "An identical modifier is repeated under the same proposition.",
                            proposition_id=proposition_id,
                            modifier_id=modifier.modifier_id,
                        )
                    )
                owner_modifier_signatures.add(modifier_signature)
                if not set(modifier.evidence.evidence_ids) & set(proposition.evidence.evidence_ids):
                    issues.append(
                        _issue(
                            "modifier_evidence_disconnected",
                            ValidationSeverity.ERROR,
                            "Modifier and owning proposition do not share an evidence block.",
                            proposition_id=proposition_id,
                            modifier_id=modifier.modifier_id,
                        )
                    )

        proposition_modifier_signatures = set(shared_modifier_evidence)
        event_modifier_signatures: set[tuple] = set()
        for modifier in propositions.event_modifiers:
            _validate_modifier(modifier, propositions.evidence_blocks, modifier_ids, issues)
            referenced_evidence_ids.update(modifier.evidence.evidence_ids)
            if modifier.modifier_type in _LOCAL_MODIFIER_TYPES:
                issues.append(
                    _issue(
                        "possible_local_modifier_at_event_level",
                        ValidationSeverity.WARNING,
                        f"{modifier.modifier_type} usually belongs to a specific proposition.",
                        modifier_id=modifier.modifier_id,
                    )
                )
            modifier_signature = (
                tuple(modifier.evidence.evidence_ids),
                modifier.evidence.quote,
                modifier.value_text,
            )
            if modifier_signature in event_modifier_signatures:
                issues.append(
                    _issue(
                        "duplicate_event_modifier",
                        ValidationSeverity.ERROR,
                        "An identical event modifier is repeated.",
                        modifier_id=modifier.modifier_id,
                    )
                )
            event_modifier_signatures.add(modifier_signature)
            if modifier_signature in proposition_modifier_signatures:
                issues.append(
                    _issue(
                        "modifier_has_multiple_ownership_levels",
                        ValidationSeverity.ERROR,
                        "The same modifier evidence is assigned at both event and proposition levels.",
                        modifier_id=modifier.modifier_id,
                    )
                )

        for (_, _, value_text), owners in shared_modifier_evidence.items():
            if len(set(owners)) > 1:
                issues.append(
                    _issue(
                        "shared_modifier_evidence",
                        ValidationSeverity.INFO,
                        f"Modifier evidence {value_text!r} is shared by propositions "
                        f"{', '.join(owners)}.",
                    )
                )

        expected_types = _EXPECTED_PROPOSITION_TYPES.get(primary_frame.primary_frame)
        if expected_types and not any(
            proposition.proposition_type in expected_types
            for proposition in propositions.propositions
        ):
            issues.append(
                _issue(
                    "primary_frame_content_mismatch",
                    ValidationSeverity.WARNING,
                    f"No typical proposition type was found for {primary_frame.primary_frame}.",
                )
            )

        proposition_modifier_count = sum(
            len(proposition.modifiers) for proposition in propositions.propositions
        )
        metrics = PropositionValidationMetrics(
            proposition_count=len(propositions.propositions),
            event_modifier_count=len(propositions.event_modifiers),
            proposition_modifier_count=proposition_modifier_count,
            attributed_proposition_count=sum(
                proposition.attribution is not None for proposition in propositions.propositions
            ),
            evidence_block_count=len(propositions.evidence_blocks),
            referenced_evidence_block_count=len(referenced_evidence_ids & set(evidence_by_id)),
            evidence_block_coverage=(
                round(len(referenced_evidence_ids & set(evidence_by_id)) / len(evidence_by_id), 4)
                if evidence_by_id
                else 0.0
            ),
        )
        return GraphUnitPropositionValidation(
            graph_unit_id=unit.graph_unit_id,
            is_graph_ready=not any(
                issue.severity == ValidationSeverity.ERROR for issue in issues
            ),
            metrics=metrics,
            issues=issues,
        )


def _index_segments(items: list, label: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        if item.segment_id in indexed:
            raise ValueError(f"{label} contain duplicate segment_id {item.segment_id}")
        indexed[item.segment_id] = item
    return indexed


def _index_units(items: list, segment_id: str, label: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        if item.graph_unit_id in indexed:
            raise ValueError(
                f"{label} contain duplicate graph_unit_id {item.graph_unit_id} in {segment_id}"
            )
        indexed[item.graph_unit_id] = item
    return indexed


def _require_same_ids(expected: set[str], actual: set[str], label: str) -> None:
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} do not align; missing={missing}, extra={extra}")


def _validate_modifier(
    modifier,
    evidence_blocks: list[EvidenceBlock],
    seen_ids: set[str],
    issues: list[PropositionValidationIssue],
    proposition_id: str | None = None,
) -> None:
    if modifier.modifier_id in seen_ids:
        issues.append(
            _issue(
                "duplicate_modifier_id",
                ValidationSeverity.ERROR,
                f"Duplicate modifier_id {modifier.modifier_id}.",
                proposition_id=proposition_id,
                modifier_id=modifier.modifier_id,
            )
        )
    seen_ids.add(modifier.modifier_id)
    _validate_evidence_reference(
        modifier.evidence,
        evidence_blocks,
        issues,
        owner_code="modifier",
        proposition_id=proposition_id,
        modifier_id=modifier.modifier_id,
    )


def _validate_evidence_reference(
    evidence: EvidenceReference,
    evidence_blocks: list[EvidenceBlock],
    issues: list[PropositionValidationIssue],
    *,
    owner_code: str,
    proposition_id: str | None = None,
    modifier_id: str | None = None,
) -> None:
    evidence_by_id = {block.evidence_id: block for block in evidence_blocks}
    if len(set(evidence.evidence_ids)) != len(evidence.evidence_ids):
        issues.append(
            _issue(
                f"{owner_code}_duplicate_evidence_id",
                ValidationSeverity.ERROR,
                "Evidence reference contains duplicate evidence IDs.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )
        return
    missing = [evidence_id for evidence_id in evidence.evidence_ids if evidence_id not in evidence_by_id]
    if missing:
        issues.append(
            _issue(
                f"{owner_code}_unknown_evidence_id",
                ValidationSeverity.ERROR,
                f"Evidence reference contains unknown evidence IDs: {missing}.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )
        return
    positions_by_id = {
        block.evidence_id: index for index, block in enumerate(evidence_blocks)
    }
    positions = [positions_by_id[evidence_id] for evidence_id in evidence.evidence_ids]
    if positions != list(range(positions[0], positions[-1] + 1)):
        issues.append(
            _issue(
                f"{owner_code}_noncontiguous_evidence",
                ValidationSeverity.ERROR,
                "Evidence IDs must reference contiguous blocks in source order.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )
        return
    referenced_text = "".join(evidence_by_id[evidence_id].text for evidence_id in evidence.evidence_ids)
    if evidence.quote not in referenced_text:
        issues.append(
            _issue(
                f"{owner_code}_quote_not_found",
                (
                    ValidationSeverity.WARNING
                    if owner_code == "modifier"
                    else ValidationSeverity.ERROR
                ),
                "Evidence quote is not an exact continuous substring of its evidence blocks.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )


def _issue(
    code: str,
    severity: ValidationSeverity,
    message: str,
    *,
    proposition_id: str | None = None,
    modifier_id: str | None = None,
) -> PropositionValidationIssue:
    return PropositionValidationIssue(
        code=code,
        severity=severity,
        message=message,
        proposition_id=proposition_id,
        modifier_id=modifier_id,
    )
