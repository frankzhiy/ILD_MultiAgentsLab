"""Versioned specialty judgments shared by the MDT workflow."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agents.common.initial_output import (
    AssessmentRole,
    AssessmentStatus,
    AssessmentType,
    EvidenceBundle,
    JudgmentConditions,
    SpecialtyAssessment,
    SpecialtyAtomicClaim,
    synchronize_judgment_conditions,
)
from src.guidelines.models import GuidelineEvidencePointer


SpecialtyName = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
JudgmentChangeType = Literal[
    "maintain",
    "supplement",
    "qualify",
    "revise",
    "withdraw",
    "create",
]


class JudgmentContent(BaseModel):
    """Complete proposed content for a new judgment version."""

    model_config = ConfigDict(extra="forbid")

    role: AssessmentRole
    assessment_type: AssessmentType
    statement: str = Field(min_length=1)
    status: AssessmentStatus
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    claims: list[SpecialtyAtomicClaim] = Field(min_length=1)
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conditions: JudgmentConditions = Field(default_factory=JudgmentConditions)

    @classmethod
    def from_assessment(cls, assessment: SpecialtyAssessment) -> "JudgmentContent":
        data = assessment.model_dump(mode="json")
        data.pop("assessment_id", None)
        return cls.model_validate(data)

    def to_assessment(self, assessment_id: str) -> SpecialtyAssessment:
        return SpecialtyAssessment(
            assessment_id=assessment_id,
            **self.model_dump(mode="json"),
        )


class JudgmentChangeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_type: JudgmentChangeType
    target_judgment_id: str | None = None
    base_version_id: str | None = None
    proposed_content: JudgmentContent | None = None
    rationale: str = Field(min_length=1)
    trigger_issue_ids: list[str] = Field(default_factory=list)
    considered_source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.change_type == "create":
            if self.target_judgment_id is not None or self.base_version_id is not None:
                raise ValueError("create cannot target an existing judgment version")
            if self.proposed_content is None:
                raise ValueError("create requires proposed_content")
            return self
        if not self.target_judgment_id or not self.base_version_id:
            raise ValueError(f"{self.change_type} requires a target and base version")
        requires_content = self.change_type in {"supplement", "qualify", "revise"}
        if requires_content != (self.proposed_content is not None):
            raise ValueError(
                f"{self.change_type} "
                f"{'requires' if requires_content else 'must not include'} proposed_content"
            )
        return self


class SpecialtyJudgmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: SpecialtyName
    transaction_id: str = Field(min_length=1)
    proposals: list[JudgmentChangeProposal] = Field(default_factory=list)


class SpecialtyJudgmentUpdateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[JudgmentChangeProposal] = Field(default_factory=list)


class SpecialtyJudgmentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgment_id: str
    version_id: str
    version_number: int = Field(ge=1)
    specialty: SpecialtyName
    lifecycle_status: Literal["active", "superseded", "withdrawn"] = "active"
    change_type: Literal[
        "initial",
        "supplement",
        "qualify",
        "revise",
        "withdraw",
        "create",
    ]
    previous_version_id: str | None = None
    created_in_round: int = Field(default=0, ge=0, le=3)
    trigger_issue_ids: list[str] = Field(default_factory=list)
    considered_source_refs: list[str] = Field(default_factory=list)
    rationale: str = "首轮专科判断。"
    changed_fields: list[str] = Field(default_factory=list)
    assessment: SpecialtyAssessment
    content_hash: str


class SpecialtyJudgmentHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgment_id: str
    specialty: SpecialtyName
    active_version_id: str | None
    versions: list[SpecialtyJudgmentVersion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_active_version(self):
        for index, version in enumerate(self.versions, 1):
            if version.judgment_id != self.judgment_id:
                raise ValueError(f"{self.judgment_id} contains a foreign judgment version")
            if version.specialty != self.specialty:
                raise ValueError(f"{self.judgment_id} contains a foreign specialty version")
            if version.version_number != index or version.version_id != _version_id(
                self.judgment_id, index
            ):
                raise ValueError(f"{self.judgment_id} has a broken version sequence")
            expected_previous = self.versions[index - 2].version_id if index > 1 else None
            if version.previous_version_id != expected_previous:
                raise ValueError(f"{self.judgment_id} has a broken version lineage")
        active = [item.version_id for item in self.versions if item.lifecycle_status == "active"]
        if len(active) > 1:
            raise ValueError(f"{self.judgment_id} has more than one active version")
        if self.active_version_id != (active[0] if active else None):
            raise ValueError(f"{self.judgment_id} active_version_id is inconsistent")
        return self


class JudgmentChangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    specialty: SpecialtyName
    judgment_id: str
    change_type: JudgmentChangeType
    before_version_id: str | None = None
    after_version_id: str | None = None
    round_number: int = Field(ge=0, le=3)
    trigger_issue_ids: list[str] = Field(default_factory=list)
    considered_source_refs: list[str] = Field(default_factory=list)
    rationale: str
    changed_fields: list[str] = Field(default_factory=list)


class MultiSpecialtyDecisionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_decision_state.v1"] = "mdt_decision_state.v1"
    case_id: str
    revision: int = Field(default=1, ge=1)
    judgments: list[SpecialtyJudgmentHistory] = Field(default_factory=list)
    specialty_context: dict[str, dict[str, Any]] = Field(default_factory=dict)
    discussion_answers: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    change_events: list[JudgmentChangeEvent] = Field(default_factory=list)
    applied_transaction_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_judgments(self):
        ids = [item.judgment_id for item in self.judgments]
        if len(ids) != len(set(ids)):
            raise ValueError("judgment_id values must be globally unique")
        return self


def initialize_decision_state(
    case_id: str,
    specialty_outputs: dict[str, dict[str, Any]],
) -> MultiSpecialtyDecisionState:
    histories: list[SpecialtyJudgmentHistory] = []
    contexts: dict[str, dict[str, Any]] = {}
    for specialty, raw in specialty_outputs.items():
        assessments_block = raw.get("specialty_assessments") or {}
        contexts[specialty] = {
            "specialty_question": assessments_block.get("specialty_question", ""),
            "assessability": assessments_block.get("assessability", "not_assessable"),
            "evidence_gaps": deepcopy(assessments_block.get("evidence_gaps") or []),
            "boundaries": deepcopy(assessments_block.get("boundaries") or []),
            "questions": deepcopy(
                (raw.get("interspecialty_questions") or {}).get("questions") or []
            ),
        }
        for assessment_raw in assessments_block.get("assessments") or []:
            assessment = SpecialtyAssessment.model_validate(assessment_raw)
            synchronize_judgment_conditions(assessment)
            judgment_id = assessment.assessment_id
            version_id = _version_id(judgment_id, 1)
            _prepare_version_assessment(assessment, version_id)
            version = SpecialtyJudgmentVersion(
                judgment_id=judgment_id,
                version_id=version_id,
                version_number=1,
                specialty=specialty,
                change_type="initial",
                assessment=assessment,
                content_hash=_assessment_hash(assessment),
            )
            histories.append(
                SpecialtyJudgmentHistory(
                    judgment_id=judgment_id,
                    specialty=specialty,
                    active_version_id=version_id,
                    versions=[version],
                )
            )
    return MultiSpecialtyDecisionState(
        case_id=case_id,
        judgments=histories,
        specialty_context=contexts,
        discussion_answers={specialty: [] for specialty in specialty_outputs},
    )


def active_judgments(
    state: MultiSpecialtyDecisionState,
    specialty: str | None = None,
) -> list[SpecialtyJudgmentVersion]:
    values = []
    for history in state.judgments:
        if specialty is not None and history.specialty != specialty:
            continue
        if history.active_version_id is None:
            continue
        values.append(next(
            item for item in history.versions if item.version_id == history.active_version_id
        ))
    return values


def project_active_specialty_outputs(
    state: MultiSpecialtyDecisionState,
) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    specialties = list(state.specialty_context)
    for specialty in specialties:
        context = state.specialty_context[specialty]
        assessments = []
        for version in active_judgments(state, specialty):
            item = version.assessment.model_dump(mode="json")
            item.update({
                "judgment_id": version.judgment_id,
                "version_id": version.version_id,
                "previous_version_id": version.previous_version_id,
                "origin": "initial_assessment" if version.change_type == "initial" else "judgment_update",
            })
            assessments.append(item)
        assessments.extend(deepcopy(state.discussion_answers.get(specialty) or []))
        outputs[specialty] = {
            "specialty_assessments": {
                "specialty_question": context["specialty_question"],
                "assessability": context["assessability"],
                "assessments": assessments,
                "evidence_gaps": deepcopy(context["evidence_gaps"]),
                "boundaries": deepcopy(context["boundaries"]),
            },
            "interspecialty_questions": {
                "questions": deepcopy(context["questions"]),
            },
        }
    return outputs


def apply_judgment_update(
    state: MultiSpecialtyDecisionState,
    update: SpecialtyJudgmentUpdate,
    *,
    round_number: int,
) -> MultiSpecialtyDecisionState:
    if update.transaction_id in state.applied_transaction_ids:
        return state
    result = state.model_copy(deep=True)
    history_by_id = {item.judgment_id: item for item in result.judgments}
    targeted = [
        proposal.target_judgment_id
        for proposal in update.proposals
        if proposal.target_judgment_id is not None
    ]
    if len(targeted) != len(set(targeted)):
        raise ValueError("One specialty update cannot change the same judgment twice")

    for proposal in update.proposals:
        if proposal.change_type != "maintain":
            if not proposal.trigger_issue_ids:
                raise ValueError("A judgment change must identify its triggering issue")
            if not proposal.considered_source_refs:
                raise ValueError("A judgment change must identify the material it considered")
        if proposal.change_type == "create":
            judgment_id = _new_judgment_id(result, update.specialty)
            assessment = proposal.proposed_content.to_assessment(judgment_id)
            synchronize_judgment_conditions(assessment)
            version_id = _version_id(judgment_id, 1)
            _prepare_version_assessment(assessment, version_id)
            version = SpecialtyJudgmentVersion(
                judgment_id=judgment_id,
                version_id=version_id,
                version_number=1,
                specialty=update.specialty,
                change_type="create",
                created_in_round=round_number,
                trigger_issue_ids=proposal.trigger_issue_ids,
                considered_source_refs=proposal.considered_source_refs,
                rationale=proposal.rationale,
                changed_fields=["created"],
                assessment=assessment,
                content_hash=_assessment_hash(assessment),
            )
            result.judgments.append(SpecialtyJudgmentHistory(
                judgment_id=judgment_id,
                specialty=update.specialty,
                active_version_id=version_id,
                versions=[version],
            ))
            result.change_events.append(_event(
                update, proposal, judgment_id, round_number, None, version_id, ["created"]
            ))
            continue

        history = history_by_id.get(proposal.target_judgment_id or "")
        if history is None:
            raise ValueError(f"Unknown judgment_id: {proposal.target_judgment_id}")
        if history.specialty != update.specialty:
            raise ValueError("A specialty may update only its own judgments")
        if history.active_version_id != proposal.base_version_id:
            raise ValueError(
                f"Stale base version for {history.judgment_id}: {proposal.base_version_id}"
            )
        current = next(
            item for item in history.versions if item.version_id == history.active_version_id
        )
        if proposal.change_type == "maintain":
            result.change_events.append(_event(
                update, proposal, history.judgment_id, round_number,
                current.version_id, current.version_id, [],
            ))
            continue
        current.lifecycle_status = "superseded"
        if proposal.change_type == "withdraw":
            version_number = current.version_number + 1
            version_id = _version_id(history.judgment_id, version_number)
            withdrawn = SpecialtyJudgmentVersion(
                judgment_id=history.judgment_id,
                version_id=version_id,
                version_number=version_number,
                specialty=update.specialty,
                lifecycle_status="withdrawn",
                change_type="withdraw",
                previous_version_id=current.version_id,
                created_in_round=round_number,
                trigger_issue_ids=proposal.trigger_issue_ids,
                considered_source_refs=proposal.considered_source_refs,
                rationale=proposal.rationale,
                changed_fields=["lifecycle_status"],
                assessment=current.assessment.model_copy(deep=True),
                content_hash=current.content_hash,
            )
            history.versions.append(withdrawn)
            history.active_version_id = None
            result.change_events.append(_event(
                update, proposal, history.judgment_id, round_number,
                current.version_id, version_id, ["lifecycle_status"],
            ))
            continue

        assessment, changed_fields = prepare_judgment_change(current, proposal)
        version_number = current.version_number + 1
        version_id = _version_id(history.judgment_id, version_number)
        _prepare_version_assessment(assessment, version_id)
        version = SpecialtyJudgmentVersion(
            judgment_id=history.judgment_id,
            version_id=version_id,
            version_number=version_number,
            specialty=update.specialty,
            change_type=proposal.change_type,
            previous_version_id=current.version_id,
            created_in_round=round_number,
            trigger_issue_ids=proposal.trigger_issue_ids,
            considered_source_refs=proposal.considered_source_refs,
            rationale=proposal.rationale,
            changed_fields=changed_fields,
            assessment=assessment,
            content_hash=_assessment_hash(assessment),
        )
        history.versions.append(version)
        history.active_version_id = version_id
        result.change_events.append(_event(
            update, proposal, history.judgment_id, round_number,
            current.version_id, version_id, changed_fields,
        ))

    result.revision += 1
    result.applied_transaction_ids.append(update.transaction_id)
    return MultiSpecialtyDecisionState.model_validate(result.model_dump(mode="json"))


def prepare_judgment_change(
    current: SpecialtyJudgmentVersion,
    proposal: JudgmentChangeProposal,
) -> tuple[SpecialtyAssessment, list[str]]:
    """Validate a proposed version with the same rules before and during commit."""

    if proposal.proposed_content is None:
        raise ValueError(f"{proposal.change_type} requires proposed_content")
    previous = current.assessment.model_copy(deep=True)
    synchronize_judgment_conditions(previous)
    assessment = proposal.proposed_content.to_assessment(current.judgment_id)
    synchronize_judgment_conditions(assessment)
    if proposal.change_type == "supplement":
        present = {
            json.dumps(relation.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            for relation in assessment.evidence.evidence_relations
        }
        for relation in previous.evidence.evidence_relations:
            key = json.dumps(relation.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            if key not in present:
                assessment.evidence.evidence_relations.append(relation.model_copy(deep=True))
        existing_guides = {
            (item.chunk_id, tuple(item.quote_unit_ids))
            for item in assessment.guideline_evidence
        }
        assessment.guideline_evidence.extend(
            item.model_copy(deep=True)
            for item in previous.guideline_evidence
            if (item.chunk_id, tuple(item.quote_unit_ids)) not in existing_guides
        )
        synchronize_judgment_conditions(assessment)
    elif proposal.change_type == "qualify":
        assessment.limitations = list(dict.fromkeys([
            *previous.limitations, *assessment.limitations,
        ]))
        assessment.conditions.applicability_conditions = list(dict.fromkeys([
            *previous.conditions.applicability_conditions,
            *assessment.conditions.applicability_conditions,
        ]))
        assessment.conditions.evidence_scope.scope_limitations = list(dict.fromkeys([
            *previous.conditions.evidence_scope.scope_limitations,
            *assessment.conditions.evidence_scope.scope_limitations,
        ]))
        synchronize_judgment_conditions(assessment)
    _align_unchanged_claim_ids(previous, assessment)
    changed_fields = _changed_fields(previous, assessment)
    _validate_change_kind(
        proposal.change_type, previous, assessment, changed_fields
    )
    return assessment, changed_fields


def record_discussion_answers(
    state: MultiSpecialtyDecisionState,
    responses: list[Any],
    reviews: list[Any],
    *,
    round_number: int,
) -> MultiSpecialtyDecisionState:
    result = state.model_copy(deep=True)
    for response in responses:
        bucket = result.discussion_answers.setdefault(response.specialty, [])
        for answer in response.answers:
            relations = []
            for use in answer.evidence_uses:
                effect = use.effect
                direction = "supports" if effect == "supporting" else "weakens" if effect == "weakening" else "neutral"
                function = {
                    "supporting": "foundational",
                    "weakening": "foundational",
                    "discriminating": "discriminating",
                    "qualifying": "qualifying",
                    "background": "background",
                }[effect]
                relations.append({
                    "segment_id": use.segment_id,
                    "graph_unit_id": use.graph_unit_id,
                    "evidence_ids": use.evidence_ids,
                    "proposition_ids": use.proposition_ids,
                    "node_ids": [node.get("node_id") for node in use.graph_nodes if node.get("node_id")],
                    "quote": use.quote,
                    "direction": direction,
                    "function": function,
                })
            bucket.append({
                "assessment_id": answer.answer_id,
                "role": "primary",
                "assessment_type": "other",
                "statement": f"对问题 {answer.issue_id} 的会中回答：{answer.answer}",
                "status": "not_assessable" if answer.answerability == "not_assessable" else "supported" if answer.confidence == "high" else "favored" if answer.confidence == "moderate" else "possible",
                "medical_basis": answer.medical_basis,
                "decision_impact": "用于更新该问题的回答与复核状态。",
                "claims": [
                    {"claim_id": claim.claim_id, "statement": claim.statement}
                    for claim in answer.answer_claims
                ],
                "evidence": {"evidence_relations": relations},
                "guideline_evidence": [item.model_dump(mode="json") for item in answer.guideline_evidence],
                "limitations": [answer.remaining_limitation] if answer.remaining_limitation else [],
                "conditions": {
                    "subject": answer.answer,
                    "professional_level": "observation",
                    "timeframe": {"kind": "current", "start": "", "end": "", "description": "本轮 MDT 讨论。"},
                    "evidence_scope": {"evidence_ids": list(dict.fromkeys(eid for rel in relations for eid in rel["evidence_ids"])), "source_types": ["case_evidence"] if relations else [], "scope_limitations": [answer.remaining_limitation] if answer.remaining_limitation else []},
                    "applicability_conditions": [answer.remaining_limitation] if answer.remaining_limitation else [],
                },
                "origin": "discussion_answer",
                "answered_question_id": answer.issue_id,
                "_discussion_round": round_number,
            })
    latest_reviews = {(item.issue_id, item.reviewer_specialty): item for item in reviews}
    for review in latest_reviews.values():
        if review.evidence_gap is None:
            continue
        context = result.specialty_context[review.reviewer_specialty]
        gap = review.evidence_gap.model_dump(mode="json")
        gap.update({
            "_discussion_round": round_number,
            "_discussion_issue_id": review.issue_id,
            "_discussion_disposition": review.outcome,
        })
        context["evidence_gaps"].append(gap)
    return result


def _event(update, proposal, judgment_id, round_number, before, after, fields):
    return JudgmentChangeEvent(
        transaction_id=update.transaction_id,
        specialty=update.specialty,
        judgment_id=judgment_id,
        change_type=proposal.change_type,
        before_version_id=before,
        after_version_id=after,
        round_number=round_number,
        trigger_issue_ids=proposal.trigger_issue_ids,
        considered_source_refs=proposal.considered_source_refs,
        rationale=proposal.rationale,
        changed_fields=fields,
    )


def _prepare_version_assessment(
    assessment: SpecialtyAssessment,
    version_id: str,
) -> None:
    for index, claim in enumerate(assessment.claims, 1):
        if not claim.claim_id:
            claim.claim_id = f"{version_id}_c{index:03d}"
    if len(assessment.claims) == 1:
        for relation in assessment.evidence.evidence_relations:
            if not relation.target_claim_id:
                relation.target_claim_id = assessment.claims[0].claim_id


def _align_unchanged_claim_ids(
    previous: SpecialtyAssessment,
    current: SpecialtyAssessment,
) -> None:
    if [item.statement for item in previous.claims] != [
        item.statement for item in current.claims
    ]:
        return
    for old, new in zip(previous.claims, current.claims, strict=True):
        new.claim_id = old.claim_id
    if len(current.claims) == 1:
        for relation in current.evidence.evidence_relations:
            if not relation.target_claim_id:
                relation.target_claim_id = current.claims[0].claim_id


def _validate_change_kind(change_type, previous, current, fields) -> None:
    if not fields:
        raise ValueError(f"{change_type} must produce a substantive state change")
    if change_type == "supplement":
        forbidden = {
            "role", "assessment_type", "statement", "status", "decision_impact",
            "claims", "limitations",
        }.intersection(fields)
        if forbidden:
            raise ValueError(f"supplement changed core judgment fields: {sorted(forbidden)}")
        _validate_supplement_conditions(previous.conditions, current.conditions)
    elif change_type == "qualify":
        if previous.assessment_type != current.assessment_type:
            raise ValueError("qualify cannot change assessment_type")
        if previous.conditions.subject != current.conditions.subject:
            raise ValueError("qualify cannot change the judgment subject")
        if previous.conditions.professional_level != current.conditions.professional_level:
            raise ValueError("qualify cannot change the professional level")
        ranks = {
            "supported": 5,
            "favored": 4,
            "possible": 3,
            "unclassifiable": 2,
            "not_assessable": 1,
            "not_applicable": 0,
        }
        if ranks[current.status] > ranks[previous.status]:
            raise ValueError("qualify cannot strengthen the judgment status")
        if not _is_superset(current.limitations, previous.limitations):
            raise ValueError("qualify cannot remove existing limitations")
        if not _is_superset(
            current.conditions.applicability_conditions,
            previous.conditions.applicability_conditions,
        ):
            raise ValueError("qualify cannot broaden applicability conditions")
        if not _is_superset(
            current.conditions.evidence_scope.scope_limitations,
            previous.conditions.evidence_scope.scope_limitations,
        ):
            raise ValueError("qualify cannot remove evidence-scope limitations")


def _validate_supplement_conditions(previous, current) -> None:
    if (
        current.subject != previous.subject
        or current.professional_level != previous.professional_level
        or current.timeframe != previous.timeframe
        or current.applicability_conditions != previous.applicability_conditions
        or current.evidence_scope.scope_limitations
        != previous.evidence_scope.scope_limitations
    ):
        raise ValueError("supplement cannot change judgment conditions or limitations")
    if not _is_superset(
        current.evidence_scope.evidence_ids,
        previous.evidence_scope.evidence_ids,
    ):
        raise ValueError("supplement cannot remove patient evidence from scope")
    if not _is_superset(
        current.evidence_scope.source_types,
        previous.evidence_scope.source_types,
    ):
        raise ValueError("supplement cannot remove evidence source types")


def _changed_fields(previous: SpecialtyAssessment, current: SpecialtyAssessment) -> list[str]:
    old = previous.model_dump(mode="json")
    new = current.model_dump(mode="json")
    return sorted(key for key in old if old[key] != new[key] and key != "assessment_id")


def _is_superset(current: list[Any], previous: list[Any]) -> bool:
    return set(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in current) >= set(
        json.dumps(item, sort_keys=True, ensure_ascii=False) for item in previous
    )


def _assessment_hash(assessment: SpecialtyAssessment) -> str:
    payload = json.dumps(
        assessment.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _version_id(judgment_id: str, number: int) -> str:
    return f"{judgment_id}@v{number:03d}"


def _new_judgment_id(state: MultiSpecialtyDecisionState, specialty: str) -> str:
    prefix = f"{specialty}_judgment_"
    numbers = [
        int(item.judgment_id.removeprefix(prefix))
        for item in state.judgments
        if item.judgment_id.startswith(prefix)
        and item.judgment_id.removeprefix(prefix).isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"
