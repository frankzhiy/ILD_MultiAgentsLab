"""Compact, proposition-level working input for the thoracic-radiology agent.

The shared specialty input intentionally keeps every graph unit and every semantic
artifact.  That is useful for audit, but it is too large and too coarse-grained for
text-only radiology reasoning: one encounter-level graph unit can contain symptoms,
CT findings, echocardiography, treatment, and response.  This module builds a
read-only specialty projection without changing the shared input contract.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.clinical_proposition import PropositionType
from src.schemas.semantic_graphing.graph_unit import (
    GraphUnitCertainty,
    GraphUnitStatus,
    MdtSpecialty,
    SpecialistTarget,
)
from src.schemas.specialty_agent_input import EvidenceRole, SpecialtyCaseInput


class ProjectionDisposition(StrEnum):
    THORACIC_IMAGING = "thoracic_imaging"
    CLINICAL_CONTEXT = "clinical_context"
    OTHER_IMAGING = "other_imaging"
    OUT_OF_SCOPE = "out_of_scope"


class ProjectedStatementKind(StrEnum):
    EXAMINATION = "examination"
    REPORTED_FINDING = "reported_finding"
    REPORTED_IMPRESSION = "reported_impression"
    REPORTED_RECOMMENDATION = "reported_recommendation"
    CLINICAL_CONTEXT = "clinical_context"


class DescriptionLevelHint(StrEnum):
    FEATURE_LEVEL = "feature_level"
    IMPRESSION_LEVEL = "impression_level"
    LABEL_ONLY = "label_only"
    OUT_OF_SCOPE = "out_of_scope"


class OrientationUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    segment_id: str
    temporal_anchor: str | None = None
    source_type: str
    evidence_role: EvidenceRole
    mdt_specialty: list[MdtSpecialty]
    clinical_context: str | None = None
    text: str


class ProjectedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_id: str
    graph_unit_id: str
    proposition_id: str
    proposition_type: str
    kind: ProjectedStatementKind
    disposition: ProjectionDisposition
    thoracic_imaging_eligible: bool
    concept_text: str
    status: GraphUnitStatus
    certainty: GraphUnitCertainty
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)
    attribution: str | None = None


class ProjectedEvidenceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    segment_id: str
    temporal_anchor: str | None = None
    source_type: str
    evidence_role: EvidenceRole
    mdt_specialty: list[MdtSpecialty]
    clinical_context: str | None = None
    description_level_hint: DescriptionLevelHint
    statements: list[ProjectedStatement] = Field(default_factory=list)


class ExcludedRadiologyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    evidence_role: EvidenceRole
    reason: str
    text: str


class RadiologyProjectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orientation_unit_count: int = Field(ge=0)
    radiology_candidate_unit_count: int = Field(ge=0)
    thoracic_evidence_unit_count: int = Field(ge=0)
    excluded_candidate_unit_count: int = Field(ge=0)
    thoracic_statement_count: int = Field(ge=0)
    clinical_context_statement_count: int = Field(ge=0)


class RadiologyWorkingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "thoracic_radiology.working_input.v2"
    case_id: str
    target_specialty: SpecialistTarget
    source_run_dir: str
    orientation_units: list[OrientationUnit] = Field(default_factory=list)
    evidence_units: list[ProjectedEvidenceUnit] = Field(default_factory=list)
    excluded_radiology_candidates: list[ExcludedRadiologyCandidate] = Field(
        default_factory=list
    )
    summary: RadiologyProjectionSummary

    def eligible_statement_keys(self) -> set[tuple[str, str]]:
        return {
            (statement.graph_unit_id, statement.proposition_id)
            for unit in self.evidence_units
            for statement in unit.statements
            if statement.thoracic_imaging_eligible
        }


_CHEST_MODALITY_RE = re.compile(
    r"(?:HRCT|CTPA|肺动脉CT|胸部CT|肺部CT|肺CT|胸片|胸部X线|肺窗|高分辨率CT)",
    re.IGNORECASE,
)
_OTHER_MODALITY_RE = re.compile(
    r"(?:超声心动图|心脏彩超|双下肢|下肢动脉|下肢静脉|肺功能|"
    r"冠状动脉CT|冠脉CT|腹部彩超|甲状腺|关节超声|骨科)",
    re.IGNORECASE,
)
_CARDIAC_OR_LIMB_CONCEPT_RE = re.compile(
    r"(?:左房|右房|左室|右室|室间隔|二尖瓣|三尖瓣|主动脉窦|"
    r"下肢动脉|下肢深静脉|膝关节|肺功能|呼吸储备|肺容量|气道阻力)",
    re.IGNORECASE,
)
_FEATURE_LEVEL_RE = re.compile(
    r"(?:网格|蜂窝|牵拉性|磨玻璃|实变|条片|胸膜下|外周带|"
    r"间质增粗|纹理|马赛克|气体潴留|上叶|中叶|下叶|舌段)",
    re.IGNORECASE,
)
_IMAGING_LABEL_RE = re.compile(
    r"(?:纤维化|间质性肺炎|间质性改变|肺气肿|肺大疱|结节|淋巴结|"
    r"肺动脉高压|胸膜增厚|肺部感染|肺不张|UIP|NSIP|OP)",
    re.IGNORECASE,
)
_CLINICAL_DIAGNOSIS_RE = re.compile(
    r"(?:收住|入院诊断|初步诊断|临床诊断|出院诊断|工作诊断)", re.IGNORECASE
)


def build_radiology_working_input(case_input: SpecialtyCaseInput) -> RadiologyWorkingInput:
    """Project the shared specialty input into a compact radiology working view."""

    if case_input.target_specialty != MdtSpecialty.THORACIC_RADIOLOGY:
        raise ValueError(
            "Radiology working input requires target_specialty=thoracic_radiology"
        )

    orientation_units: list[OrientationUnit] = []
    evidence_units: list[ProjectedEvidenceUnit] = []
    excluded: list[ExcludedRadiologyCandidate] = []
    candidate_count = 0

    for segment in case_input.segments:
        for unit in segment.units:
            graph_unit = unit.graph_unit
            orientation_units.append(
                OrientationUnit(
                    graph_unit_id=graph_unit.graph_unit_id,
                    segment_id=graph_unit.segment_id,
                    temporal_anchor=graph_unit.temporal_anchor,
                    source_type=str(graph_unit.source_type),
                    evidence_role=unit.evidence_role,
                    mdt_specialty=graph_unit.mdt_specialty,
                    clinical_context=graph_unit.clinical_context,
                    text=graph_unit.text,
                )
            )
            if MdtSpecialty.THORACIC_RADIOLOGY not in graph_unit.mdt_specialty:
                continue
            candidate_count += 1

            if not _contains_thoracic_imaging(graph_unit.text):
                excluded.append(
                    ExcludedRadiologyCandidate(
                        graph_unit_id=graph_unit.graph_unit_id,
                        evidence_role=unit.evidence_role,
                        reason=(
                            "unit虽被路由至胸部影像科，但未发现胸部CT/HRCT/CTPA/胸片信号；"
                            "其中的肺功能、心脏超声或下肢超声只能作为病例背景。"
                        ),
                        text=graph_unit.text,
                    )
                )
                continue

            statements = [
                _project_statement(unit.graph_unit.text, unit.graph_unit.graph_unit_id, item)
                for item in unit.clinical_propositions.propositions
            ]
            evidence_units.append(
                ProjectedEvidenceUnit(
                    graph_unit_id=graph_unit.graph_unit_id,
                    segment_id=graph_unit.segment_id,
                    temporal_anchor=graph_unit.temporal_anchor,
                    source_type=str(graph_unit.source_type),
                    evidence_role=unit.evidence_role,
                    mdt_specialty=graph_unit.mdt_specialty,
                    clinical_context=graph_unit.clinical_context,
                    description_level_hint=_description_level(graph_unit.text, statements),
                    statements=statements,
                )
            )

    thoracic_count = sum(
        item.thoracic_imaging_eligible
        for unit in evidence_units
        for item in unit.statements
    )
    context_count = sum(
        item.disposition == ProjectionDisposition.CLINICAL_CONTEXT
        for unit in evidence_units
        for item in unit.statements
    )
    return RadiologyWorkingInput(
        case_id=case_input.case_id,
        target_specialty=case_input.target_specialty,
        source_run_dir=case_input.source_run_dir,
        orientation_units=orientation_units,
        evidence_units=evidence_units,
        excluded_radiology_candidates=excluded,
        summary=RadiologyProjectionSummary(
            orientation_unit_count=len(orientation_units),
            radiology_candidate_unit_count=candidate_count,
            thoracic_evidence_unit_count=len(evidence_units),
            excluded_candidate_unit_count=len(excluded),
            thoracic_statement_count=thoracic_count,
            clinical_context_statement_count=context_count,
        ),
    )


def build_radiology_reconstruction_prompt_input(
    case_input: SpecialtyCaseInput,
    working_input: RadiologyWorkingInput,
) -> dict:
    """Full verbatim case context plus the small proposition view radiology can cite."""

    return {
        "case_id": case_input.case_id,
        "case_context": [
            {
                "segment_id": item.segment.segment_id,
                "text": item.segment.text,
            }
            for item in case_input.segments
        ],
        "imaging_evidence": _radiology_evidence_view(working_input),
        "excluded_candidate_ids": [
            item.graph_unit_id for item in working_input.excluded_radiology_candidates
        ],
    }


def build_radiology_evidence_prompt_input(
    working_input: RadiologyWorkingInput,
) -> dict:
    return {
        "case_id": working_input.case_id,
        "imaging_evidence": _radiology_evidence_view(working_input),
    }


def radiology_proposition_schema_constraints(
    working_input: RadiologyWorkingInput,
) -> dict[str, list[dict[str, set[str]]]]:
    alternatives = [
        {
            "graph_unit_id": {unit.graph_unit_id},
            "proposition_ids": {
                statement.proposition_id
                for statement in unit.statements
                if statement.thoracic_imaging_eligible
            },
        }
        for unit in working_input.evidence_units
    ]
    alternatives = [item for item in alternatives if item["proposition_ids"]]
    return {
        "supporting_evidence": alternatives,
        "conflicting_evidence": alternatives,
    }


def _radiology_evidence_view(working_input: RadiologyWorkingInput) -> list[dict]:
    return [
        {
            "graph_unit_id": unit.graph_unit_id,
            "evidence_role": unit.evidence_role,
            "description_level_hint": unit.description_level_hint,
            "statements": [
                {
                    "proposition_id": statement.proposition_id,
                    "kind": statement.kind,
                    "disposition": statement.disposition,
                    "concept_text": statement.concept_text,
                    "status": statement.status,
                    "certainty": statement.certainty,
                    "quote": statement.quote,
                    "attribution": statement.attribution,
                }
                for statement in unit.statements
            ],
        }
        for unit in working_input.evidence_units
    ]


def _contains_thoracic_imaging(text: str) -> bool:
    return bool(_CHEST_MODALITY_RE.search(text))


def _project_statement(unit_text: str, graph_unit_id: str, proposition) -> ProjectedStatement:
    proposition_type = proposition.proposition_type
    concept = proposition.concept_text
    quote = proposition.evidence.quote
    other_imaging = _is_other_imaging_statement(unit_text, concept, quote)
    clinical_diagnosis = (
        proposition_type == PropositionType.DIAGNOSIS_ASSERTION
        and bool(_CLINICAL_DIAGNOSIS_RE.search(quote))
    )
    eligible_type = proposition_type in {
        PropositionType.EXAMINATION,
        PropositionType.FINDING,
        PropositionType.DIAGNOSIS_ASSERTION,
        PropositionType.PLAN,
        PropositionType.INFORMATION_AVAILABILITY,
    } and not clinical_diagnosis
    thoracic_eligible = eligible_type and not other_imaging

    if other_imaging:
        disposition = ProjectionDisposition.OTHER_IMAGING
    elif thoracic_eligible:
        disposition = ProjectionDisposition.THORACIC_IMAGING
    elif proposition_type in {
        PropositionType.SYMPTOM,
        PropositionType.SIGN,
        PropositionType.TREATMENT,
        PropositionType.MEDICATION,
        PropositionType.OUTCOME,
        PropositionType.PROCEDURE,
        PropositionType.BACKGROUND_CONDITION,
    } or clinical_diagnosis:
        disposition = ProjectionDisposition.CLINICAL_CONTEXT
    else:
        disposition = ProjectionDisposition.OUT_OF_SCOPE

    if proposition_type == PropositionType.EXAMINATION:
        kind = ProjectedStatementKind.EXAMINATION
    elif proposition_type == PropositionType.DIAGNOSIS_ASSERTION and not clinical_diagnosis:
        kind = ProjectedStatementKind.REPORTED_IMPRESSION
    elif proposition_type == PropositionType.PLAN:
        kind = ProjectedStatementKind.REPORTED_RECOMMENDATION
    elif proposition_type in {PropositionType.FINDING, PropositionType.INFORMATION_AVAILABILITY}:
        kind = ProjectedStatementKind.REPORTED_FINDING
    else:
        kind = ProjectedStatementKind.CLINICAL_CONTEXT

    attribution = None
    if proposition.attribution is not None:
        attribution = str(proposition.attribution.attribution_type)
    return ProjectedStatement(
        statement_id=f"{graph_unit_id}::{proposition.proposition_id}",
        graph_unit_id=graph_unit_id,
        proposition_id=proposition.proposition_id,
        proposition_type=str(proposition_type),
        kind=kind,
        disposition=disposition,
        thoracic_imaging_eligible=thoracic_eligible,
        concept_text=concept,
        status=proposition.status,
        certainty=proposition.certainty,
        evidence_ids=proposition.evidence.evidence_ids,
        quote=quote,
        attribution=attribution,
    )


def _is_other_imaging_statement(unit_text: str, concept: str, quote: str) -> bool:
    scoped = f"{concept} {quote}"
    if _OTHER_MODALITY_RE.search(scoped):
        return True
    if _OTHER_MODALITY_RE.search(unit_text) and _CARDIAC_OR_LIMB_CONCEPT_RE.search(scoped):
        return True
    return False


def _description_level(
    unit_text: str, statements: list[ProjectedStatement]
) -> DescriptionLevelHint:
    thoracic_text = " ".join(
        item.quote for item in statements if item.thoracic_imaging_eligible
    )
    if not thoracic_text:
        return DescriptionLevelHint.OUT_OF_SCOPE
    if _FEATURE_LEVEL_RE.search(thoracic_text):
        return DescriptionLevelHint.FEATURE_LEVEL
    if _IMAGING_LABEL_RE.search(thoracic_text) or _IMAGING_LABEL_RE.search(unit_text):
        return DescriptionLevelHint.IMPRESSION_LEVEL
    return DescriptionLevelHint.LABEL_ONLY
