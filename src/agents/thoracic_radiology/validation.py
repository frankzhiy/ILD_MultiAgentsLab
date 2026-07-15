"""Validation and exact proposition resolution for thoracic-radiology v2."""

from __future__ import annotations

from collections.abc import Iterable

from src.agents.common.validation import (
    case_units,
    iter_evidence_pointers,
    require_specialty_input,
    validate_chair_question_order,
)
from src.agents.thoracic_radiology.evidence_projection import (
    RadiologyWorkingInput,
    build_radiology_working_input,
)
from src.agents.thoracic_radiology.models import (
    ChairAnswer,
    DiscussionEvidenceMap,
    DiscussionUpdateAndConsult,
    EvidencePointer,
    InitialCaseReconstruction,
    InitialConsultFormulation,
    RadiologyTask,
    RadiologyTaskAssessment,
    ResolvedPropositionQuote,
    SpecialistOpinion,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyInitialAssessment,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


def require_radiology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(
        case_input,
        MdtSpecialty.THORACIC_RADIOLOGY,
        "ThoracicRadiologyAgent",
    )


def resolve_proposition_pointers(value: object, case_input: SpecialtyCaseInput) -> None:
    """Resolve exact proposition quotes while retaining legacy evidence-ID support."""

    units = case_units(case_input)
    evidence_to_unit: dict[str, object] = {}
    for unit in units.values():
        for block in unit.clinical_propositions.evidence_blocks:
            if block.evidence_id in evidence_to_unit:
                raise ValueError(f"Duplicate evidence_id in specialty input: {block.evidence_id}")
            evidence_to_unit[block.evidence_id] = unit

    for pointer in iter_evidence_pointers(value, EvidencePointer):
        unit = _pointer_unit(pointer, units, evidence_to_unit)
        pointer.graph_unit_id = unit.graph_unit.graph_unit_id
        pointer.segment_id = unit.graph_unit.segment_id
        propositions = {
            item.proposition_id: item for item in unit.clinical_propositions.propositions
        }
        if pointer.proposition_ids:
            missing = sorted(set(pointer.proposition_ids) - set(propositions))
            if missing:
                raise ValueError(
                    f"Evidence pointer {pointer.graph_unit_id} has unknown proposition_ids: "
                    f"{missing}"
                )
            ordered = [
                item
                for item in unit.clinical_propositions.propositions
                if item.proposition_id in set(pointer.proposition_ids)
            ]
            pointer.proposition_ids = [item.proposition_id for item in ordered]
            pointer.resolved_quotes = [
                ResolvedPropositionQuote(
                    proposition_id=item.proposition_id,
                    evidence_ids=item.evidence.evidence_ids,
                    quote=item.evidence.quote,
                )
                for item in ordered
            ]
            evidence_ids = {
                evidence_id
                for item in ordered
                for evidence_id in item.evidence.evidence_ids
            }
            pointer.evidence_ids = [
                block.evidence_id
                for block in unit.clinical_propositions.evidence_blocks
                if block.evidence_id in evidence_ids
            ]
            exact_node_ids = {
                f"{pointer.graph_unit_id}::{item.proposition_id}" for item in ordered
            }
            pointer.node_ids = [
                node.node_id
                for node in unit.local_graph.nodes
                if node.node_id in exact_node_ids
            ]
            pointer.quote = "\n".join(item.evidence.quote for item in ordered)
        else:
            known_evidence = {
                block.evidence_id: block.text
                for block in unit.clinical_propositions.evidence_blocks
            }
            missing = sorted(set(pointer.evidence_ids) - set(known_evidence))
            if missing:
                raise ValueError(
                    f"Evidence pointer has unknown evidence_ids: {missing}"
                )
            selected = set(pointer.evidence_ids)
            pointer.evidence_ids = [
                block.evidence_id
                for block in unit.clinical_propositions.evidence_blocks
                if block.evidence_id in selected
            ]
            pointer.node_ids = [
                node.node_id
                for node in unit.local_graph.nodes
                if selected.intersection(node.evidence.evidence_ids)
            ]
            pointer.quote = "".join(known_evidence[item] for item in pointer.evidence_ids)


def validate_case_reconstruction(
    result: InitialCaseReconstruction,
    case_input: SpecialtyCaseInput,
    working_input: RadiologyWorkingInput | None = None,
) -> InitialCaseReconstruction:
    working_input = working_input or build_radiology_working_input(case_input)
    resolve_proposition_pointers(result, case_input)
    _validate_reconstruction(result, case_input, working_input)
    return result


def validate_initial_formulation(
    result: InitialConsultFormulation,
    reconstruction: InitialCaseReconstruction,
    case_input: SpecialtyCaseInput,
    working_input: RadiologyWorkingInput | None = None,
) -> InitialConsultFormulation:
    working_input = working_input or build_radiology_working_input(case_input)
    resolve_proposition_pointers(result, case_input)
    _validate_formulation(result, reconstruction, case_input, working_input, {})
    return result


def validate_initial_assessment(
    result: ThoracicRadiologyInitialAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
) -> ThoracicRadiologyInitialAssessment:
    del clinical_rules
    if result.case_id != case_input.case_id:
        raise ValueError(
            f"Assessment case_id {result.case_id} does not match {case_input.case_id}"
        )
    working_input = build_radiology_working_input(case_input)
    if result.legacy_import:
        _upgrade_legacy_initial(result, working_input)
    resolve_proposition_pointers(result, case_input)
    _validate_reconstruction(result.reconstruction, case_input, working_input)
    _validate_formulation(result, result.reconstruction, case_input, working_input, {})
    return result


def validate_specialist_opinions(
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> None:
    resolve_proposition_pointers(discussion_input.specialist_opinions, discussion_input.case_input)
    units = case_units(discussion_input.case_input)
    opinion_ids = [item.opinion_id for item in discussion_input.specialist_opinions]
    _require_unique(opinion_ids, "specialist opinion")
    for opinion in discussion_input.specialist_opinions:
        if opinion.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist opinion cannot use shared_context as its specialty")
        for claim in opinion.claims:
            for pointer in claim.evidence:
                unit = units[pointer.graph_unit_id]
                specialties = unit.graph_unit.mdt_specialty
                if (
                    opinion.specialty not in specialties
                    and MdtSpecialty.SHARED_CONTEXT not in specialties
                ):
                    raise ValueError(
                        f"Opinion {opinion.opinion_id} cites unit {pointer.graph_unit_id} "
                        f"outside {opinion.specialty}'s evidence scope"
                    )


def validate_evidence_map(
    result: DiscussionEvidenceMap,
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> DiscussionEvidenceMap:
    resolve_proposition_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _require_known_unique(result.specialist_opinions_used, opinions, "used opinion")
    working = build_radiology_working_input(discussion_input.case_input)
    for item in result.mapped_findings:
        opinion = opinions.get(item.opinion_id)
        if opinion is None:
            raise ValueError(f"Unknown mapped opinion_id: {item.opinion_id}")
        if item.target_layer == "reported_content":
            if opinion.specialty != MdtSpecialty.THORACIC_RADIOLOGY:
                raise ValueError(
                    "Only a thoracic radiology opinion may target reported_content"
                )
            _validate_projection_pointers(item.evidence, working)
        _validate_authorized_by_opinions(
            item.evidence, [item.opinion_id], opinions
        )
    mapped_ids = {item.opinion_id for item in result.mapped_findings}
    if not mapped_ids.issubset(result.specialist_opinions_used):
        raise ValueError("specialist_opinions_used must include every mapped opinion_id")
    return result


def validate_update_and_consult(
    result: DiscussionUpdateAndConsult,
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> DiscussionUpdateAndConsult:
    resolve_proposition_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    working = build_radiology_working_input(discussion_input.case_input)
    _validate_reported_content_update(result, opinions, working)
    _require_unique([str(item.task) for item in result.task_updates], "task update")
    for update in result.task_updates:
        if update.updated_assessment.task != update.task:
            raise ValueError("Task update task must match updated_assessment.task")
        _validate_task_assessment(
            update.updated_assessment,
            discussion_input.initial_assessment.reconstruction,
            working,
            opinions,
        )
        _validate_authorized_by_opinions(
            update.supporting_evidence,
            update.specialist_opinion_ids,
            opinions,
        )
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    return result


def validate_discussion_response(
    result: ThoracicRadiologyDiscussionResponse,
    discussion_input: ThoracicRadiologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> ThoracicRadiologyDiscussionResponse:
    del clinical_rules
    if result.case_id != discussion_input.case_input.case_id:
        raise ValueError(
            f"Discussion case_id {result.case_id} does not match "
            f"{discussion_input.case_input.case_id}"
        )
    resolve_proposition_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _require_known_unique(result.specialist_opinions_used, opinions, "used opinion")
    working = build_radiology_working_input(discussion_input.case_input)
    _validate_reconstruction(
        result.updated_assessment.reconstruction,
        discussion_input.case_input,
        working,
    )
    _validate_formulation(
        result.updated_assessment,
        result.updated_assessment.reconstruction,
        discussion_input.case_input,
        working,
        opinions,
    )
    _require_unique([str(item.task) for item in result.task_changes], "task change")
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    used_by_result = {
        opinion_id
        for update in result.task_changes
        for opinion_id in update.specialist_opinion_ids
    }
    used_by_result.update(item.opinion_id for item in result.mapped_findings)
    used_by_result.update(
        opinion_id
        for answer in result.chair_answers
        for opinion_id in answer.specialist_opinion_ids
    )
    if not used_by_result.issubset(result.specialist_opinions_used):
        raise ValueError(
            "specialist_opinions_used omits opinions used by the discussion response"
        )
    return result


def _pointer_unit(pointer, units, evidence_to_unit):
    if pointer.graph_unit_id:
        unit = units.get(pointer.graph_unit_id)
        if unit is None:
            raise ValueError(
                f"Unknown graph_unit_id in evidence pointer: {pointer.graph_unit_id}"
            )
        if pointer.evidence_ids:
            mismatched = [
                item
                for item in pointer.evidence_ids
                if item not in evidence_to_unit or evidence_to_unit[item] is not unit
            ]
            if mismatched:
                raise ValueError(
                    f"Evidence IDs do not belong to {pointer.graph_unit_id}: {mismatched}"
                )
        return unit
    referenced = {
        evidence_to_unit[item].graph_unit.graph_unit_id
        for item in pointer.evidence_ids
        if item in evidence_to_unit
    }
    missing = sorted(set(pointer.evidence_ids) - set(evidence_to_unit))
    if missing:
        raise ValueError(f"Evidence pointer has unknown evidence_ids: {missing}")
    if len(referenced) != 1:
        raise ValueError(
            "Legacy evidence pointer evidence_ids must belong to exactly one graph unit"
        )
    return units[next(iter(referenced))]


def _validate_reconstruction(
    result: InitialCaseReconstruction,
    case_input: SpecialtyCaseInput,
    working: RadiologyWorkingInput,
) -> None:
    del case_input
    for exam in result.examinations:
        if exam.body_scope not in {"thoracic", "mixed", "uncertain"}:
            raise ValueError("v2 examinations must remain within thoracic-radiology scope")
        _validate_projection_pointers(exam.source_evidence, working)
    for statement in result.reported_statements:
        _validate_projection_pointers(statement.evidence, working)
    _validate_all_pointers(result.orientation.context_evidence)
    if any(item.task == RadiologyTask.CONDITIONAL_IPF_HRCT for item in result.task_plan):
        item = next(
            item for item in result.task_plan if item.task == RadiologyTask.CONDITIONAL_IPF_HRCT
        )
        if item.activation == "active" and not _explicit_ipf_context(working):
            raise ValueError(
                "conditional_ipf_hrct may be active only when the case explicitly presents "
                "suspected/diagnosed IPF context"
            )


def _validate_formulation(
    result,
    reconstruction: InitialCaseReconstruction,
    case_input: SpecialtyCaseInput,
    working: RadiologyWorkingInput,
    opinions: dict[str, SpecialistOpinion],
) -> None:
    del case_input
    assessments = result.task_assessments
    _require_unique([str(item.task) for item in assessments], "task assessment")
    statement_ids = {item.statement_id for item in reconstruction.reported_statements}
    for assessment in assessments:
        unknown = set(assessment.reported_statement_ids) - statement_ids
        if unknown:
            raise ValueError(
                f"Task {assessment.task} references unknown reported_statement_ids: "
                f"{sorted(unknown)}"
            )
        _validate_task_assessment(assessment, reconstruction, working, opinions)
    _validate_core_pe_wording(result.core_answer.answer, assessments)
    coverage = result.review_coverage
    _require_unique([str(item.domain) for item in coverage], "guide coverage domain")
    for question in result.specialist_questions:
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
        _validate_all_pointers(question.related_evidence)
    for action in result.action_items:
        _validate_all_pointers(action.related_evidence)


def _validate_task_assessment(
    assessment: RadiologyTaskAssessment,
    reconstruction: InitialCaseReconstruction,
    working: RadiologyWorkingInput,
    opinions: dict[str, SpecialistOpinion],
) -> None:
    pointers = [*assessment.supporting_evidence, *assessment.conflicting_evidence]
    if assessment.specialist_opinion_ids:
        _validate_authorized_by_opinions(
            pointers, assessment.specialist_opinion_ids, opinions
        )
    else:
        _validate_projection_pointers(pointers, working)
    _validate_all_pointers(assessment.related_evidence)
    if (
        assessment.task == RadiologyTask.ILD_MORPHOLOGIC_PATTERN
        and assessment.answerability == "answered"
        and assessment.confidence in {"very_high", "high"}
        and not any(item.evidence_level == "feature_level" for item in reconstruction.examinations)
    ):
        raise ValueError(
            "High-confidence morphologic pattern requires feature-level text evidence"
        )
    if assessment.task == RadiologyTask.LONGITUDINAL_CHANGE:
        if assessment.answerability in {"answered", "partially_answered"}:
            combined = " ".join(
                item.text for item in reconstruction.reported_statements
            )
            if len(reconstruction.examinations) < 2 and not any(
                marker in combined for marker in ("较前", "新发", "增加", "进展", "稳定", "改善")
            ):
                raise ValueError(
                    "Longitudinal assessment requires multiple exams or explicit comparison text"
                )
    if assessment.task == RadiologyTask.TARGETED_PULMONARY_VASCULAR:
        _validate_pe_wording(assessment.conclusion, assessment.supporting_evidence)


def _validate_projection_pointers(
    pointers: Iterable[EvidencePointer], working: RadiologyWorkingInput
) -> None:
    eligible = working.eligible_statement_keys()
    for pointer in pointers:
        if not pointer.proposition_ids:
            raise ValueError(
                "v2 thoracic imaging evidence requires exact proposition_ids"
            )
        invalid = {
            (pointer.graph_unit_id, proposition_id)
            for proposition_id in pointer.proposition_ids
            if (pointer.graph_unit_id, proposition_id) not in eligible
        }
        if invalid:
            raise ValueError(
                "Evidence pointer includes non-thoracic or ineligible propositions: "
                f"{sorted(invalid)}"
            )


def _upgrade_legacy_initial(
    result: ThoracicRadiologyInitialAssessment,
    working: RadiologyWorkingInput,
) -> None:
    """Convert v1 block references to eligible propositions and drop old route errors."""

    eligible_by_unit = {
        unit.graph_unit_id: [
            statement
            for statement in unit.statements
            if statement.thoracic_imaging_eligible
        ]
        for unit in working.evidence_units
    }

    def exact(pointers: list[EvidencePointer]) -> list[EvidencePointer]:
        mapped = []
        for pointer in pointers:
            if pointer.proposition_ids:
                mapped.append(pointer)
                continue
            evidence_ids = set(pointer.evidence_ids)
            pointer.proposition_ids = [
                statement.proposition_id
                for statement in eligible_by_unit.get(pointer.graph_unit_id, [])
                if evidence_ids.intersection(statement.evidence_ids)
            ]
            if pointer.proposition_ids:
                mapped.append(pointer)
        return mapped

    examinations = []
    for exam in result.reconstruction.examinations:
        exam.source_evidence = exact(exam.source_evidence)
        if exam.source_evidence:
            examinations.append(exam)
    exam_ids = {exam.exam_id for exam in examinations}
    for exam in examinations:
        exam.possible_same_exam_as = [
            item for item in exam.possible_same_exam_as if item in exam_ids
        ]
    result.reconstruction.examinations = examinations

    statements = []
    for statement in result.reconstruction.reported_statements:
        statement.evidence = exact(statement.evidence)
        if statement.evidence and statement.exam_id in exam_ids:
            statements.append(statement)
    result.reconstruction.reported_statements = statements
    statement_ids = {item.statement_id for item in statements}

    for assessment in result.task_assessments:
        assessment.reported_statement_ids = [
            item for item in assessment.reported_statement_ids if item in statement_ids
        ]
        assessment.supporting_evidence = exact(assessment.supporting_evidence)
        assessment.conflicting_evidence = exact(assessment.conflicting_evidence)


def _validate_all_pointers(pointers: Iterable[EvidencePointer]) -> None:
    for pointer in pointers:
        if not pointer.graph_unit_id or not pointer.segment_id:
            raise ValueError("Evidence pointer has not been resolved")


def _validate_authorized_by_opinions(
    pointers: Iterable[EvidencePointer],
    opinion_ids: Iterable[str],
    opinions: dict[str, SpecialistOpinion],
) -> None:
    opinion_ids = list(opinion_ids)
    _require_known_unique(opinion_ids, opinions, "specialist opinion")
    allowed = {
        (pointer.graph_unit_id, proposition_id)
        for opinion_id in opinion_ids
        for claim in opinions[opinion_id].claims
        for pointer in claim.evidence
        for proposition_id in pointer.proposition_ids
    }
    for pointer in pointers:
        requested = {
            (pointer.graph_unit_id, proposition_id)
            for proposition_id in pointer.proposition_ids
        }
        if requested and not requested.issubset(allowed):
            raise ValueError(
                "Evidence is not authorized by the cited formal specialist opinions"
            )


def _validate_reported_content_update(result, opinions, working) -> None:
    if not result.added_examinations and not result.added_reported_statements:
        if result.reported_content_opinion_ids:
            raise ValueError(
                "reported_content_opinion_ids supplied without reported-content update"
            )
        return
    _require_known_unique(
        result.reported_content_opinion_ids, opinions, "reported-content opinion"
    )
    if not result.reported_content_opinion_ids or any(
        opinions[item].specialty != MdtSpecialty.THORACIC_RADIOLOGY
        for item in result.reported_content_opinion_ids
    ):
        raise ValueError(
            "New examinations or reported statements require a formal thoracic radiology opinion"
        )
    pointers = [
        pointer
        for exam in result.added_examinations
        for pointer in exam.source_evidence
    ] + [
        pointer
        for statement in result.added_reported_statements
        for pointer in statement.evidence
    ]
    _validate_projection_pointers(pointers, working)
    _validate_authorized_by_opinions(
        pointers, result.reported_content_opinion_ids, opinions
    )


def _validate_chair_answers(
    answers: list[ChairAnswer],
    discussion_input: ThoracicRadiologyDiscussionInput,
    opinions: dict[str, SpecialistOpinion],
) -> None:
    validate_chair_question_order(answers, discussion_input.chair_questions)
    for answer in answers:
        _validate_authorized_by_opinions(
            answer.supporting_evidence,
            answer.specialist_opinion_ids,
            opinions,
        ) if answer.specialist_opinion_ids else _validate_all_pointers(
            answer.supporting_evidence
        )


def _validate_core_pe_wording(
    answer: str, assessments: list[RadiologyTaskAssessment]
) -> None:
    pe = next(
        (
            item
            for item in assessments
            if item.task == RadiologyTask.TARGETED_PULMONARY_VASCULAR
        ),
        None,
    )
    if pe is not None:
        _validate_pe_wording(answer, pe.supporting_evidence)


def _validate_pe_wording(text: str, pointers: Iterable[EvidencePointer]) -> None:
    quotes = " ".join(pointer.quote for pointer in pointers)
    only_central_negative = "中央型肺栓塞" in quotes and "未见" in quotes
    if only_central_negative and any(
        phrase in text for phrase in ("排除肺栓塞", "未见肺栓塞", "无肺栓塞")
    ):
        raise ValueError(
            "A central-PE-only negative report cannot be expanded to exclusion of all PE"
        )


def _explicit_ipf_context(working: RadiologyWorkingInput) -> bool:
    return any(
        marker in unit.text
        for unit in working.orientation_units
        for marker in ("疑似IPF", "特发性肺纤维化", "IPF")
    )


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({item for item in values if values.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate {label} values: {duplicates}")


def _require_known_unique(values: list[str], known: dict, label: str) -> None:
    _require_unique(values, label)
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ValueError(f"Unknown {label} IDs: {unknown}")
