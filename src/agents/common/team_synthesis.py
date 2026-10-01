"""The chair-owned clinical decision snapshot; reports only project this object."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from src.guidelines.models import GuidelineEvidencePointer

Specialty = Literal["pulmonology", "thoracic_radiology", "rheumatology", "pathology"]
Confidence = Literal["high", "moderate", "low", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JudgmentTimeframe(BaseModel):
    """The time interval for which a specialty judgment is intended to hold."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "current",
        "historical",
        "longitudinal",
        "specified_period",
        "unknown",
    ] = "current"
    start: str = ""
    end: str = ""
    description: str = "基于当前病例所提供的资料。"


class ConditionalContribution(StrictModel):
    question: str = Field(min_length=1)
    possible_result: str = Field(min_length=1)
    decision_if_found: str = Field(min_length=1)
    cannot_establish: str = Field(min_length=1)
    acquisition_value: str = Field(min_length=1)


class JudgmentReview(StrictModel):
    source_ref: str
    disposition: Literal["adopt", "qualify", "defer", "reject"]
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    required_response: bool


class DiagnosticFacet(StrictModel):
    timeframe: JudgmentTimeframe = Field(default_factory=lambda: JudgmentTimeframe(kind="unknown", description="时间未明确"))
    dimension: Literal["ild_presence", "radiologic_pattern", "histopathologic_pattern", "etiologic_attribution", "disease_behavior", "acute_or_comorbid_factors", "severity"]
    problem_id: str
    statement: str = Field(min_length=1)
    status: Literal["supported", "favored", "possible", "indeterminate", "not_assessable", "not_applicable", "unclassifiable"]
    confidence: Literal["high", "moderate", "low", "unknown", "not_applicable"]
    rationale: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_confidence(self):
        expected = {"not_assessable": "unknown", "not_applicable": "not_applicable"}
        if self.status in expected and self.confidence != expected[self.status]:
            raise ValueError(f"Facet assessability and confidence disagree: status={self.status!r} requires confidence={expected[self.status]!r}")
        return self


class DiagnosticCandidate(StrictModel):
    timeframe: JudgmentTimeframe = Field(default_factory=lambda: JudgmentTimeframe(kind="unknown", description="时间未明确"))
    diagnosis: str = Field(min_length=1)
    position: Literal["preferred", "alternative", "unresolved"]
    confidence: Confidence
    rationale: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    opposing_evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ClinicalProblem(StrictModel):
    timeframe: JudgmentTimeframe = Field(default_factory=lambda: JudgmentTimeframe(kind="unknown", description="时间未明确"))
    problem_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    relationship: Literal["primary", "acute_contributor", "comorbidity", "unexplained_finding"]
    statement: str = Field(min_length=1)
    assessability: Literal["assessable", "partially_assessable", "not_assessable"]
    confidence: Confidence
    rationale: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    candidates: list[DiagnosticCandidate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_preference(self):
        if sum(item.position == "preferred" for item in self.candidates) > 1:
            raise ValueError("One clinical problem can have only one preferred explanation")
        if self.assessability == "not_assessable" and self.confidence != "unknown":
            raise ValueError("An unassessable problem has unknown confidence")
        return self


class IssueAssignment(StrictModel):
    specialty: Specialty
    question: str = Field(min_length=1)


class ClinicalIssue(StrictModel):
    issue_id: str = Field(min_length=1)
    origin: Literal["chair", "specialty"]
    kind: Literal["inference_gap", "omission", "contradiction", "rule_gap", "clarification"]
    question: str = Field(min_length=1)
    raised_by: list[Specialty] = Field(default_factory=list)
    target_specialties: list[Specialty] = Field(min_length=1)
    assignments: list[IssueAssignment] = Field(min_length=1)
    affected_specialties: list[Specialty] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    decision_impact: str = Field(min_length=1)
    closure_criterion: str = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_roles(self):
        if self.origin == "chair" and self.raised_by:
            raise ValueError("Chair-origin issues must leave raised_by empty; the chair is the requester")
        if self.origin == "specialty" and not self.raised_by:
            raise ValueError("Specialty-origin issues require their original requester")
        if set(self.raised_by) & set(self.target_specialties):
            raise ValueError("A requesting specialty cannot answer its own question")
        targets = [a.specialty for a in self.assignments]
        if len(targets) != len(set(targets)) or set(targets) != set(self.target_specialties):
            duplicates = {specialty: [index for index, target in enumerate(targets) if target == specialty]
                          for specialty in set(targets) if targets.count(specialty) > 1}
            raise ValueError("Provide exactly one specific assignment per target specialty; "
                             f"duplicate assignment indexes={duplicates}; "
                             f"missing specialties={sorted(set(self.target_specialties) - set(targets))}; "
                             f"unexpected specialties={sorted(set(targets) - set(self.target_specialties))}. "
                             "Merge distinct questions for a duplicated specialty into one assignment, "
                             "then remove only its redundant entries; preserve other specialties' assignments.")
        return self


class IssueDisposition(StrictModel):
    issue_id: str
    outcome: Literal["covered", "continue", "answered", "bounded", "deferred", "merged"]
    rationale: str = Field(min_length=1)
    response_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    linked_issue_ids: list[str] = Field(default_factory=list)


class JudgmentBoundary(StrictModel):
    boundary_id: str
    problem_id: str
    established: str = Field(min_length=1)
    undetermined: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class SpecialtyPosition(StrictModel):
    specialty: Specialty
    statement: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class SpecialtyDisagreement(StrictModel):
    disagreement_id: str
    kind: Literal["incompatible_judgments", "needs_clarification"]
    comparison_target: str = Field(min_length=1)
    positions: list[SpecialtyPosition] = Field(min_length=2)
    explanation: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    status: Literal["open", "resolved", "bounded"]
    resolution: str
    linked_issue_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def different_specialties(self):
        if len({p.specialty for p in self.positions}) < 2:
            raise ValueError("Cross-specialty disagreements need at least two specialties")
        return self


class DecisionBranch(StrictModel):
    result: str = Field(min_length=1)
    changed_decision: str = Field(min_length=1)


class KeyEvidenceNeed(StrictModel):
    need_id: str
    problem_id: str
    information: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    status: Literal["available", "partially_available", "missing"]
    available_information: str
    missing_information: str
    action: Literal["retrieve_existing", "obtain_new", "retain_boundary", "no_further_request"]
    branches: list[DecisionBranch] = Field(default_factory=list)
    feasibility_and_burden: str = Field(min_length=1)
    if_unavailable: str = Field(min_length=1)

    @model_validator(mode="after")
    def different_decisions(self):
        if self.action in {"retrieve_existing", "obtain_new"} and len({branch.changed_decision.strip() for branch in self.branches}) < 2:
            raise ValueError("A data request must distinguish at least two actual decisions")
        if self.status == "available" and self.missing_information.strip():
            raise ValueError("Available evidence cannot have missing information")
        if self.status in {"available", "partially_available"} and not self.available_information.strip():
            raise ValueError("Available or partial evidence must identify what is actually available")
        if self.status != "available" and not self.missing_information.strip():
            raise ValueError("Missing or partial evidence must identify what is missing")
        return self


class SynthesisAcceptanceDraft(StrictModel):
    decision: Literal["accept", "accept_with_boundaries", "dissent"]
    rationale: str = Field(min_length=1)
    boundaries: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded_acceptance(self):
        if self.decision == "accept_with_boundaries" and not self.boundaries:
            raise ValueError("Bounded acceptance must name its boundaries")
        return self


class SynthesisAcceptance(SynthesisAcceptanceDraft):
    specialty: Specialty
    revision: int = Field(ge=1)
    snapshot_hash: str = Field(min_length=1)


class TeamSynthesis(StrictModel):
    schema_version: SkipJsonSchema[Literal["team_synthesis.v2"]] = "team_synthesis.v2"
    revision: SkipJsonSchema[int] = 0
    dependency_versions: SkipJsonSchema[dict[str, str]] = Field(default_factory=dict)
    snapshot_hash: SkipJsonSchema[str] = ""
    source_catalog: SkipJsonSchema[dict[str, dict]] = Field(default_factory=dict)
    evidence_catalog: SkipJsonSchema[dict[str, dict]] = Field(default_factory=dict)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    judgment_reviews: list[JudgmentReview] = Field(min_length=1)
    problems: list[ClinicalProblem] = Field(min_length=1)
    diagnostic_facets: list[DiagnosticFacet] = Field(default_factory=list)
    judgment_boundaries: list[JudgmentBoundary] = Field(default_factory=list)
    disagreements: list[SpecialtyDisagreement] = Field(default_factory=list)
    issues: list[ClinicalIssue] = Field(default_factory=list)
    issue_dispositions: list[IssueDisposition] = Field(default_factory=list)
    evidence_needs: list[KeyEvidenceNeed] = Field(default_factory=list)
    conditional_contributions: list[ConditionalContribution] = Field(default_factory=list)
    dissent: list[str] = Field(default_factory=list)
    synthesis_rationale: str = Field(min_length=1)
    coverage_review: str = Field(min_length=1, description="复核主要问题、急性变化、非ILD线索和共同错误前提；说明有意义发现的去向。")
    acceptance: SkipJsonSchema[Literal["jointly_accepted", "accepted_with_boundaries", "chair_adjudicated", "explicit_dissent"]] = "chair_adjudicated"
    acceptance_records: SkipJsonSchema[list[SynthesisAcceptance]] = Field(default_factory=list)
    stop_kind: SkipJsonSchema[Literal["pending", "completed", "budget", "no_progress"]] = "pending"

    @model_validator(mode="after")
    def coherent_problem_graph(self):
        reviewers = [r.specialty for r in self.acceptance_records]
        if len(reviewers) != len(set(reviewers)):
            raise ValueError("A specialty can accept a synthesis version only once")
        if any(r.revision != self.revision or r.snapshot_hash != self.snapshot_hash for r in self.acceptance_records):
            raise ValueError("Acceptance must refer to this exact synthesis revision and hash")
        self.acceptance = synthesis_acceptance_status(self)
        ids = [problem.problem_id for problem in self.problems]
        if len(ids) != len(set(ids)) or sum(p.relationship == "primary" for p in self.problems) != 1:
            raise ValueError("Unique problem IDs and exactly one primary clinical problem required")
        if any(need.problem_id not in ids for need in self.evidence_needs):
            raise ValueError("Evidence requests must belong to a clinical problem")
        if any(f.problem_id not in ids for f in self.diagnostic_facets):
            raise ValueError("Diagnostic facets must belong to a clinical problem")
        if any(b.problem_id not in ids for b in self.judgment_boundaries):
            raise ValueError("Judgment boundaries must belong to a clinical problem")
        issue_ids = {i.issue_id for i in self.issues} | {d.issue_id for d in self.issue_dispositions}
        invalid_links = []
        for field in ("disagreements", "issue_dispositions"):
            for index, item in enumerate(getattr(self, field)):
                unknown = set(item.linked_issue_ids) - issue_ids
                if unknown:
                    invalid_links.append(f"/{field}/{index}/linked_issue_ids: {sorted(unknown)}")
        if invalid_links:
            raise ValueError("Linked issue IDs must refer to open issues or their recorded dispositions; "
                             f"invalid links={invalid_links}; allowed issue IDs={sorted(issue_ids)}. "
                             "Evidence need IDs are not issue IDs; remove those links or cite an actual issue.")
        dimensions = [(f.problem_id, f.dimension, f.timeframe.model_dump_json()) for f in self.diagnostic_facets]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("Diagnostic facets must have unique dimensions within each problem and timeframe")
        duplicate_errors = []
        for field, key in (("issues", "issue_id"), ("judgment_reviews", "source_ref"), ("issue_dispositions", "issue_id"), ("judgment_boundaries", "boundary_id"), ("disagreements", "disagreement_id"), ("evidence_needs", "need_id")):
            items = getattr(self, field)
            values = [getattr(item, key) for item in items]
            if len(values) != len(set(values)):
                positions = {}
                for index, value in enumerate(values):
                    positions.setdefault(value, []).append(index)
                duplicates = {value: indexes for value, indexes in positions.items() if len(indexes) > 1}
                duplicate_errors.append(f"/{field}: Duplicate {key}: {duplicates}")
        if duplicate_errors:
            raise ValueError("; ".join(duplicate_errors) +
                             "; merge distinct information into the retained entry, then use "
                             "remove_indices to delete redundant entries. Do not rename duplicate IDs.")
        return self


def synthesis_acceptance_status(team):
    if team.dissent or any(d.status == "open" for d in team.disagreements) or any(r.decision == "dissent" for r in team.acceptance_records):
        return "explicit_dissent"
    if {r.specialty for r in team.acceptance_records} != set(Specialty.__args__):
        return "chair_adjudicated"
    if team.judgment_boundaries or team.issues or team.evidence_needs or any(r.decision == "accept_with_boundaries" for r in team.acceptance_records):
        return "accepted_with_boundaries"
    return "jointly_accepted"


class MDTChairResult(StrictModel):
    schema_version: Literal["mdt_chair.v11"] = "mdt_chair.v11"
    case_id: str
    team_synthesis: TeamSynthesis


class MDTTeamReport(StrictModel):
    schema_version: Literal["mdt_final_report.v8"] = "mdt_final_report.v8"
    case_id: str
    team_synthesis: TeamSynthesis
    discussion_rounds: int
    stop_reason: str
