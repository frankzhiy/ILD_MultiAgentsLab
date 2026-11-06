"""The chair-owned clinical decision snapshot; reports only project this object."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from src.guidelines.models import GuidelineEvidencePointer
from src.agents.common.diagnostic_resolution import DiagnosticExploration, DiagnosticResolution
from src.agents.common.clinical_concepts import Confidence, Assessability, JudgmentDirection, DIRECTION_DESCRIPTION, CONFIDENCE_DESCRIPTION, concept_key

Specialty = Literal["pulmonology", "thoracic_radiology", "rheumatology", "pathology"]


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
    subject: str | SkipJsonSchema[None] = Field(default=None, min_length=1, exclude_if=lambda value: value is None, description="该诊断维度评价的中性对象，例如组织学UIP模式；不把否定陈述作为对象。")
    status: SkipJsonSchema[Literal["supported", "favored", "possible", "indeterminate", "not_assessable", "not_applicable", "unclassifiable"] | None] = Field(default=None, exclude_if=lambda value: value is None)
    assessability: Assessability | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    direction: JudgmentDirection | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None, description=DIRECTION_DESCRIPTION.replace("conditions.subject", "该维度对应的中性判断对象"))
    confidence: Confidence | SkipJsonSchema[Literal["not_applicable"]] = Field(description=CONFIDENCE_DESCRIPTION)
    rationale: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_confidence(self):
        expected = {"not_assessable": "unknown", "not_applicable": "not_applicable"}
        if self.status in expected and self.confidence != expected[self.status]:
            raise ValueError(f"Facet assessability and confidence disagree: status={self.status!r} requires confidence={expected[self.status]!r}")
        if self.assessability == "not_assessable" and (self.confidence != "unknown" or self.direction != "indeterminate"):
            raise ValueError("An unassessable facet has unknown confidence and indeterminate direction")
        return self


class DiagnosticCandidate(StrictModel):
    timeframe: JudgmentTimeframe = Field(default_factory=lambda: JudgmentTimeframe(kind="unknown", description="时间未明确"))
    candidate_id: str = Field(default="", min_length=1, exclude_if=lambda value: not value, description="引用diagnostic_exploration登记的候选编号；同一概念沿用同一编号。")
    diagnosis: SkipJsonSchema[str] = ""
    position: Literal["preferred", "alternative", "unresolved"]
    confidence: Confidence = Field(description="对该候选作为本问题解释的把握度；与候选相对地位及确诊标准分开。")
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
    assessability: Assessability
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
        keys = [c.candidate_id or concept_key(c.diagnosis) for c in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate diagnostic candidate within one clinical problem")
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
    undetermined: str = Field(
        min_length=1,
        description="收起时显示的标题：能独立读懂、以问号结束的具体临床问句，不使用内部编号。",
        json_schema_extra={"pattern": r"[？?]\s*$"},
    )
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


class ChairDiagnosticOpinion(StrictModel):
    case_formulation: str = Field(min_length=1, description="将病程、主要发现及其关系提炼为全病例临床问题。")
    diagnosis: str = Field(min_length=1, description="直接给出最可能的具体工作诊断及急性叠加、并存关系；不附加层级自述、职责声明或泛化免责句。")
    reasoning: str = Field(min_length=1, description="将保留的专科判断与病例证据联系起来，说明整体诊断为何成立。")
    differential_reasoning: str = Field(min_length=1, description="比较最有竞争力的解释，说明取舍；无法择一时说明具体障碍。")
    limitations: list[str] = Field(default_factory=list)
    problem_ids: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list, description="作为诊断依据的已采纳或限定后采纳的正式专科判断。")
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def grounded_opinion(self):
        if not self.source_refs and not self.evidence_refs:
            raise ValueError("主持人全病例诊断意见必须引用保留的专科判断或病例证据")
        return self


class TeamSynthesis(StrictModel):
    schema_version: SkipJsonSchema[Literal["team_synthesis.v2"]] = "team_synthesis.v2"
    revision: SkipJsonSchema[int] = 0
    dependency_versions: SkipJsonSchema[dict[str, str]] = Field(default_factory=dict)
    snapshot_hash: SkipJsonSchema[str] = ""
    source_catalog: SkipJsonSchema[dict[str, dict]] = Field(default_factory=dict)
    evidence_catalog: SkipJsonSchema[dict[str, dict]] = Field(default_factory=dict)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    judgment_reviews: list[JudgmentReview] = Field(min_length=1)
    diagnostic_exploration: DiagnosticExploration | None = None
    diagnostic_resolution: DiagnosticResolution | None = None
    # Historical snapshots remain readable; generation and reporting require this opinion.
    diagnostic_opinion: ChairDiagnosticOpinion | None = None
    problems: list[ClinicalProblem] = Field(min_length=1)
    diagnostic_facets: list[DiagnosticFacet] = Field(default_factory=list)
    judgment_boundaries: list[JudgmentBoundary] = Field(default_factory=list)
    disagreements: list[SpecialtyDisagreement] = Field(default_factory=list)
    issues: list[ClinicalIssue] = Field(default_factory=list)
    issue_dispositions: list[IssueDisposition] = Field(default_factory=list)
    evidence_needs: list[KeyEvidenceNeed] = Field(default_factory=list)
    conditional_contributions: list[ConditionalContribution] = Field(default_factory=list)
    dissent: list[str] = Field(default_factory=list)
    synthesis_rationale: str = Field(min_length=1, description="专科判断采纳、限定与未采纳的审查说明；整体诊断填写 diagnostic_opinion。")
    coverage_review: str = Field(min_length=1, description="复核主要问题、急性变化、非ILD线索和共同错误前提；说明有意义发现的去向。")
    acceptance: SkipJsonSchema[Literal["jointly_accepted", "accepted_with_boundaries", "chair_adjudicated", "explicit_dissent"]] = "chair_adjudicated"
    acceptance_records: SkipJsonSchema[list[SynthesisAcceptance]] = Field(default_factory=list)
    stop_kind: SkipJsonSchema[Literal["pending", "completed", "budget", "no_progress"]] = "pending"

    def validate_candidate_identity(self, *, require_current_ids=False):
        if self.diagnostic_exploration is None or self.diagnostic_resolution is None:
            return self
        candidates = {c.candidate_id: c for c in self.diagnostic_exploration.candidates}
        decisions = {d.candidate_id: d for d in self.diagnostic_resolution.candidate_dispositions}
        current = [c for p in self.problems for c in p.candidates]
        primary = next(p for p in self.problems if p.relationship == "primary")
        if not current and not candidates:
            if self.diagnostic_resolution.primary_candidate_id is not None:
                raise ValueError("Primary candidate ID must reference a current preferred candidate")
            self.diagnostic_resolution.primary_diagnosis = self.diagnostic_resolution.primary_diagnosis or primary.statement
            return self
        if not require_current_ids and not any(c.candidate_id for c in current) and self.diagnostic_resolution.primary_candidate_id is None:
            return self  # Historical names remain verbatim; never infer medical aliases.
        if len(decisions) != len(self.diagnostic_resolution.candidate_dispositions) or set(decisions) != set(candidates):
            raise ValueError("Review every registered candidate exactly once")
        for decision in decisions.values():
            if decision.outcome == "rejected" and decision.nonselection_reason is None:
                raise ValueError(f"State why candidate {decision.candidate_id} is not retained; non-retention is not exclusion")
        for problem in self.problems:
            for candidate in problem.candidates:
                if not candidate.candidate_id or candidate.candidate_id not in candidates:
                    raise ValueError("Every current candidate must reference its registered candidate_id")
                registered = candidates[candidate.candidate_id]
                if candidate.diagnosis and concept_key(candidate.diagnosis) not in {concept_key(n) for n in [registered.diagnosis, *registered.aliases]}:
                    raise ValueError("Current candidate name disagrees with its registered identity")
                candidate.diagnosis = registered.diagnosis
                decision = decisions[candidate.candidate_id]
                scope = "primary_diagnosis" if problem.relationship == "primary" else "other_problem"
                expected = "preferred" if candidate.position == "preferred" else "retained"
                if decision.scope != scope or decision.outcome not in {expected, "needs_discussion"}:
                    raise ValueError(
                        f"Current candidate {candidate.candidate_id} in problem {problem.problem_id} has "
                        f"position={candidate.position!r}, but its disposition has scope={decision.scope!r} "
                        f"and outcome={decision.outcome!r}. This placement requires scope={scope!r} and "
                        f"outcome={expected!r} or 'needs_discussion'. Reconcile the position and disposition."
                    )
        active_ids = {c.candidate_id for c in current}
        unplaced = sorted(d.candidate_id for d in decisions.values()
                          if d.outcome in {"preferred", "retained", "needs_discussion"} and d.candidate_id not in active_ids)
        if unplaced:
            raise ValueError(f"Every retained candidate must appear in its clinical problem: {unplaced}. "
                             "Use problems[*].candidates with the registered ID; a diagnostic facet alone does not place a candidate.")
        preferred = next((c for c in primary.candidates if c.position == "preferred"), None)
        if self.diagnostic_resolution.primary_candidate_id != (preferred.candidate_id if preferred else None):
            raise ValueError("Primary candidate ID must match the primary problem's preferred candidate")
        name = preferred.diagnosis if preferred else " / ".join(c.diagnosis for c in primary.candidates) or primary.statement
        if self.diagnostic_resolution.primary_diagnosis and concept_key(self.diagnostic_resolution.primary_diagnosis) != concept_key(name):
            raise ValueError("Primary diagnosis title disagrees with the current diagnostic candidates")
        self.diagnostic_resolution.primary_diagnosis = name
        return self

    @model_validator(mode="after")
    def coherent_problem_graph(self):
        reviewers = [r.specialty for r in self.acceptance_records]
        if len(reviewers) != len(set(reviewers)):
            raise ValueError("A specialty can accept a synthesis version only once")
        if any(r.revision != self.revision or r.snapshot_hash != self.snapshot_hash for r in self.acceptance_records):
            raise ValueError("Acceptance must refer to this exact synthesis revision and hash")
        self.acceptance = synthesis_acceptance_status(self)
        if self.diagnostic_resolution is not None:
            open_ids = {i.issue_id for i in self.issues}
            for candidate in self.diagnostic_resolution.candidate_dispositions:
                if set(candidate.issue_ids) - open_ids:
                    raise ValueError("Candidate disposition must refer to an open issue")
                if candidate.outcome == "needs_discussion" and not candidate.issue_ids:
                    raise ValueError("Route each unresolved candidate to a specific discussion issue")
            for gap in self.diagnostic_resolution.remaining_gaps:
                if set(gap.issue_ids) - open_ids:
                    raise ValueError("Diagnostic gap must refer to an open issue")
            if self.diagnostic_resolution.status == "needs_discussion" and not open_ids:
                raise ValueError("Incomplete diagnostic reasoning requires open discussion issues")
        ids = [problem.problem_id for problem in self.problems]
        if len(ids) != len(set(ids)) or sum(p.relationship == "primary" for p in self.problems) != 1:
            raise ValueError("Unique problem IDs and exactly one primary clinical problem required")
        if self.diagnostic_opinion is not None:
            required = {p.problem_id for p in self.problems
                        if p.relationship in {"primary", "acute_contributor"}}
            covered = set(self.diagnostic_opinion.problem_ids)
            if covered - set(ids) or not required <= covered:
                raise ValueError("diagnostic_opinion.problem_ids 必须引用现有问题并包含主要问题及急性叠加因素")
        invalid_problems = []
        for field, label in (("evidence_needs", "Evidence requests"),
                             ("diagnostic_facets", "Diagnostic facets"),
                             ("judgment_boundaries", "Judgment boundaries")):
            for index, item in enumerate(getattr(self, field)):
                if item.problem_id not in ids:
                    invalid_problems.append(
                        f"{label} must belong to a clinical problem; "
                        f"/{field}/{index}/problem_id={item.problem_id!r}"
                    )
        if invalid_problems:
            raise ValueError("; ".join(invalid_problems) + f"; allowed problem IDs={ids}")
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
        dimensions = [
            (f.problem_id, f.dimension, f.timeframe.model_dump_json(), concept_key(f.subject or ""))
            for f in self.diagnostic_facets
        ]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("Diagnostic facets must have unique dimensions for each subject within a problem and timeframe")
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
                             "remove_indices to delete redundant entries. Preserve the union of distinct "
                             "source_refs and evidence_refs from all merged entries before removing any entry. "
                             "Do not rename duplicate IDs.")
        return self.validate_candidate_identity()


class ChairDiagnosticOpinionDraft(ChairDiagnosticOpinion):
    diagnosis: SkipJsonSchema[str] = ""


class ChairSynthesisDraft(TeamSynthesis):
    diagnostic_exploration: SkipJsonSchema[DiagnosticExploration | None] = None
    diagnostic_resolution: DiagnosticResolution = Field(...)
    diagnostic_opinion: ChairDiagnosticOpinionDraft = Field(...)


def primary_diagnosis_text(team: TeamSynthesis) -> str:
    """Render the primary problem and its adjudicated candidates in their recorded roles."""
    primary = next(problem for problem in team.problems if problem.relationship == "primary")
    preferred = [item.diagnosis for item in primary.candidates if item.position == "preferred" and item.diagnosis]
    unresolved = [item.diagnosis for item in primary.candidates if item.position == "unresolved" and item.diagnosis]
    alternatives = [item.diagnosis for item in primary.candidates if item.position == "alternative" and item.diagnosis]
    if preferred:
        differential = unresolved + alternatives
        suffix = f"，鉴别诊断包括{'、'.join(differential)}" if differential else ""
        resolution = team.diagnostic_resolution
        prefix = "主要诊断为" if resolution and resolution.status == "specific_diagnosis" else "主要考虑"
        return f"{prefix}{preferred[0]}{suffix}。"
    if unresolved:
        names = "与".join(unresolved) if len(unresolved) == 2 else "、".join(unresolved)
        main = f"{names}尚需鉴别" if len(unresolved) > 1 else f"需进一步明确{names}"
        suffix = f"；另需鉴别{'、'.join(alternatives)}" if alternatives else ""
        return f"{main}{suffix}。"
    if alternatives:
        return f"需鉴别{'、'.join(alternatives)}。"
    return primary.statement


def case_diagnosis_text(team: TeamSynthesis) -> str:
    """Compose the persisted diagnosis from the reviewed problem list."""
    paragraphs = [primary_diagnosis_text(team)]
    for relationship, label in (
        ("acute_contributor", "近期变化及相关因素"),
        ("comorbidity", "并存问题"),
        ("unexplained_finding", "其他尚待明确的发现"),
    ):
        statements = [
            problem.statement for problem in team.problems if problem.relationship == relationship
        ]
        if statements:
            text = "；".join(statement.rstrip("。；") for statement in statements)
            paragraphs.append(f"{label}：{text}。")
    return "\n\n".join(paragraphs)


def synthesis_acceptance_status(team):
    if team.dissent or any(d.status == "open" for d in team.disagreements) or any(r.decision == "dissent" for r in team.acceptance_records):
        return "explicit_dissent"
    if {r.specialty for r in team.acceptance_records} != set(Specialty.__args__):
        return "chair_adjudicated"
    if team.judgment_boundaries or team.issues or team.evidence_needs or any(r.decision == "accept_with_boundaries" for r in team.acceptance_records):
        return "accepted_with_boundaries"
    return "jointly_accepted"


class MDTChairResult(StrictModel):
    schema_version: Literal["mdt_chair.v13"] = "mdt_chair.v13"
    case_id: str
    team_synthesis: TeamSynthesis


class MDTTeamReport(StrictModel):
    schema_version: Literal["mdt_final_report.v10"] = "mdt_final_report.v10"
    case_id: str
    team_synthesis: TeamSynthesis
    discussion_rounds: int
    stop_reason: str
