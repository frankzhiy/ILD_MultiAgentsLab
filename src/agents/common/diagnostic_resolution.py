"""Current diagnostic judgments from the fixed case input."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from src.agents.common.clinical_concepts import concept_key


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosticHypothesis(Model):
    candidate_id: str = Field(min_length=1)
    diagnosis: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list, exclude_if=lambda value: not value, description="同一医学概念的其他写法；疾病、形态模式及病因限定不同的概念不得作为别名合并。")
    level: Literal["disease", "syndrome", "pattern", "category", "finding"]
    explains: str = Field(min_length=1)
    fails_to_explain: str = Field(min_length=1)
    supporting_evidence_refs: list[str] = Field(min_length=1)
    discriminating_information: str = Field(min_length=1)
    retrieval_question: str = Field(min_length=1)


class DiagnosticExploration(Model):
    problem_representation: str = Field(min_length=1)
    candidates: list[DiagnosticHypothesis] = Field(default_factory=list)
    combined_phenotype_review: str = Field(min_length=1)
    unrepresented_findings: str = Field(min_length=1)
    insufficient_candidate_basis: str = Field(default="")

    @model_validator(mode="after")
    def candidate_basis(self):
        ids = [c.candidate_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate IDs must be unique")
        names = {}
        for candidate in self.candidates:
            for name in [candidate.diagnosis, *candidate.aliases]:
                key = concept_key(name)
                if key in names and names[key] != candidate.candidate_id:
                    raise ValueError(f"One clinical concept cannot have multiple candidate IDs: {name}")
                names[key] = candidate.candidate_id
        if not ids and not self.insufficient_candidate_basis.strip():
            raise ValueError("Explain why patient evidence supports no specific candidates")
        return self


    def validate_successor(self, previous):
        successor = self.model_copy(deep=True)
        current = {c.candidate_id: c for c in successor.candidates}
        errors = []
        for old in previous.candidates:
            new = current.get(old.candidate_id)
            if new is None:
                errors.append(f"Keep registered candidate ID {old.candidate_id}, even when no longer retained")
                continue
            if concept_key(old.diagnosis) != concept_key(new.diagnosis) or old.level != new.level:
                errors.append(f"Do not reuse candidate ID {old.candidate_id} for a different clinical concept")
                continue
            # Registered aliases belong to the unchanged identity across rounds.
            aliases = {concept_key(name): name for name in [*new.aliases, *old.aliases]}
            new.aliases = list(aliases.values())
        if errors:
            raise ValueError("; ".join(errors))
        return successor.candidate_basis()


class CandidateDisposition(Model):
    candidate_id: str
    scope: Literal["primary_diagnosis", "other_problem"] = "primary_diagnosis"
    outcome: Literal["preferred", "retained", "rejected", "needs_discussion"]
    issue_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    nonselection_reason: Literal["insufficient_support", "less_explanatory", "counterevidence", "excluded", "out_of_scope"] | None = Field(default=None, exclude_if=lambda value: value is None, description="仅rejected填写：尚缺支持、解释力较弱、存在反证、已有依据排除、超出当前问题范围。未保留不等于排除。")
    exclusion_evidence_refs: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def explicit_exclusion(self):
        if self.nonselection_reason is not None and self.outcome != "rejected":
            raise ValueError("Only a non-retained candidate has a nonselection_reason")
        if self.nonselection_reason == "excluded" and not self.exclusion_evidence_refs:
            raise ValueError("Exclusion requires explicit patient evidence")
        if self.exclusion_evidence_refs and self.nonselection_reason != "excluded":
            raise ValueError("Exclusion evidence cannot mark a candidate excluded without an exclusion decision")
        return self


class DiagnosticGap(Model):
    kind: Literal["inference", "patient_evidence"]
    scope: Literal["primary_diagnosis", "other_problem"] = "primary_diagnosis"
    question: str = Field(min_length=1)
    why_unresolved: str = Field(min_length=1)
    issue_ids: list[str] = Field(default_factory=list)


class DiagnosticResolution(Model):
    status: Literal["specific_diagnosis", "provisional_diagnosis", "specific_differential", "needs_discussion"]
    achieved_level: Literal["disease", "syndrome", "pattern", "category", "finding"]
    primary_candidate_id: str | None = Field(default=None, exclude_if=lambda value: value is None, description="主要问题首选候选的稳定编号；有限鉴别无法择一或无具体候选时为null。名称由程序从候选登记解析。")
    primary_diagnosis: SkipJsonSchema[str] = ""
    goal_review: str = Field(min_length=1, description="简述首选优于备选的判别依据；不复述层级标签、完成状态或规则遵循过程。")
    candidate_dispositions: list[CandidateDisposition] = Field(default_factory=list)
    remaining_gaps: list[DiagnosticGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def completion_boundary(self):
        specific = self.status in {"specific_diagnosis", "specific_differential"}
        if specific and self.achieved_level not in {"disease", "syndrome"}:
            raise ValueError("A category, pattern or finding does not complete a specific diagnosis")
        inference = [g for g in self.remaining_gaps if g.kind == "inference"]
        if any(g.scope == "primary_diagnosis" for g in inference) and self.status != "needs_discussion":
            raise ValueError("Unresolved inference must be discussed using the existing evidence")
        if any(not g.issue_ids for g in inference):
            raise ValueError("Route each remaining inference gap to an explicit issue")
        return self
