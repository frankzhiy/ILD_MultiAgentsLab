"""Deterministic quality gate for extracted clinical propositions."""

from collections import Counter, defaultdict

from src.schemas.semantic_graphing import (
    DocumentClinicalPropositions,
    DocumentGraphUnits,
    DocumentPrimaryFrames,
    DocumentPropositionValidation,
    GraphUnit,
    GraphUnitClinicalPropositions,
    GraphUnitPrimaryFrame,
    GraphUnitPropositionValidation,
    ModifierType,
    PrimaryFrame,
    PropositionType,
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
        evidence_ranges: list[tuple[int, int]] = []
        shared_modifier_spans: dict[tuple[int, int, str], list[str]] = defaultdict(list)

        if not propositions.propositions:
            issues.append(
                _issue(
                    "no_propositions",
                    ValidationSeverity.ERROR,
                    "Graph unit has no clinical propositions.",
                )
            )

        previous_proposition_start = -1
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
            if proposition.source_span.start_char < previous_proposition_start:
                issues.append(
                    _issue(
                        "propositions_out_of_source_order",
                        ValidationSeverity.WARNING,
                        "Propositions are not ordered by their source spans.",
                        proposition_id=proposition_id,
                    )
                )
            previous_proposition_start = proposition.source_span.start_char
            _validate_span(
                unit.text,
                proposition.source_span.text,
                proposition.source_span.start_char,
                proposition.source_span.end_char,
                issues,
                owner_code="proposition",
                proposition_id=proposition_id,
            )
            evidence_ranges.append(
                (proposition.source_span.start_char, proposition.source_span.end_char)
            )
            signature = (
                proposition.proposition_type,
                proposition.concept_text,
                proposition.status,
                proposition.source_span.start_char,
                proposition.source_span.end_char,
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
                _validate_span(
                    unit.text,
                    attribution.source_span.text,
                    attribution.source_span.start_char,
                    attribution.source_span.end_char,
                    issues,
                    owner_code="attribution",
                    proposition_id=proposition_id,
                )
                evidence_ranges.append(
                    (attribution.source_span.start_char, attribution.source_span.end_char)
                )
                if attribution.actor_text not in attribution.source_span.text:
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
                and any(cue in proposition.source_span.text for cue in _ATTRIBUTION_CUES)
            ):
                issues.append(
                    _issue(
                        "possible_missing_attribution",
                        ValidationSeverity.WARNING,
                        "Diagnosis assertion contains an attribution cue but has no attribution.",
                        proposition_id=proposition_id,
                    )
                )

            previous_modifier_start = -1
            owner_modifier_signatures: set[tuple] = set()
            for modifier in proposition.modifiers:
                _validate_modifier(
                    modifier,
                    unit.text,
                    modifier_ids,
                    issues,
                    proposition_id=proposition_id,
                )
                evidence_ranges.append(
                    (modifier.source_span.start_char, modifier.source_span.end_char)
                )
                if modifier.source_span.start_char < previous_modifier_start:
                    issues.append(
                        _issue(
                            "modifiers_out_of_source_order",
                            ValidationSeverity.WARNING,
                            "Proposition modifiers are not ordered by their source spans.",
                            proposition_id=proposition_id,
                            modifier_id=modifier.modifier_id,
                        )
                    )
                previous_modifier_start = modifier.source_span.start_char
                shared_modifier_spans[
                    (
                        modifier.source_span.start_char,
                        modifier.source_span.end_char,
                        modifier.value_text,
                    )
                ].append(proposition_id)
                modifier_signature = (
                    modifier.modifier_type,
                    modifier.value_text,
                    modifier.source_span.start_char,
                    modifier.source_span.end_char,
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
                if _crosses_sentence_boundary(
                    unit.text,
                    proposition.source_span.start_char,
                    proposition.source_span.end_char,
                    modifier.source_span.start_char,
                    modifier.source_span.end_char,
                ):
                    issues.append(
                        _issue(
                            "modifier_crosses_sentence_boundary",
                            ValidationSeverity.WARNING,
                            "Modifier and owning proposition are separated by a sentence boundary.",
                            proposition_id=proposition_id,
                            modifier_id=modifier.modifier_id,
                        )
                    )

        proposition_modifier_signatures = set(shared_modifier_spans)
        event_modifier_signatures: set[tuple] = set()
        previous_event_modifier_start = -1
        for modifier in propositions.event_modifiers:
            _validate_modifier(modifier, unit.text, modifier_ids, issues)
            evidence_ranges.append((modifier.source_span.start_char, modifier.source_span.end_char))
            if modifier.source_span.start_char < previous_event_modifier_start:
                issues.append(
                    _issue(
                        "event_modifiers_out_of_source_order",
                        ValidationSeverity.WARNING,
                        "Event modifiers are not ordered by their source spans.",
                        modifier_id=modifier.modifier_id,
                    )
                )
            previous_event_modifier_start = modifier.source_span.start_char
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
                modifier.source_span.start_char,
                modifier.source_span.end_char,
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

        for (_, _, value_text), owners in shared_modifier_spans.items():
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
            evidence_coverage=_evidence_coverage(len(unit.text), evidence_ranges),
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
    unit_text: str,
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
    _validate_span(
        unit_text,
        modifier.source_span.text,
        modifier.source_span.start_char,
        modifier.source_span.end_char,
        issues,
        owner_code="modifier",
        proposition_id=proposition_id,
        modifier_id=modifier.modifier_id,
    )


def _validate_span(
    unit_text: str,
    span_text: str,
    start_char: int,
    end_char: int,
    issues: list[PropositionValidationIssue],
    *,
    owner_code: str,
    proposition_id: str | None = None,
    modifier_id: str | None = None,
) -> None:
    if end_char > len(unit_text):
        issues.append(
            _issue(
                f"{owner_code}_span_out_of_bounds",
                ValidationSeverity.ERROR,
                f"Evidence span ends at {end_char}, beyond unit length {len(unit_text)}.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )
        return
    if unit_text[start_char:end_char] != span_text:
        issues.append(
            _issue(
                f"{owner_code}_span_mismatch",
                ValidationSeverity.ERROR,
                "Evidence span offsets do not resolve to the recorded source text.",
                proposition_id=proposition_id,
                modifier_id=modifier_id,
            )
        )


def _crosses_sentence_boundary(
    text: str,
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> bool:
    if first_end <= second_start:
        gap_start, gap_end = first_end, second_start
    elif second_end <= first_start:
        gap_start, gap_end = second_end, first_start
    else:
        return False
    return any(marker in text[gap_start:gap_end] for marker in "。！？\n")


def _evidence_coverage(unit_length: int, ranges: list[tuple[int, int]]) -> float:
    if unit_length == 0:
        return 0.0
    covered = [False] * unit_length
    for start, end in ranges:
        for index in range(max(0, start), min(unit_length, end)):
            covered[index] = True
    return round(sum(covered) / unit_length, 4)


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
