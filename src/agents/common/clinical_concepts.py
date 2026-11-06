"""Shared meanings for clinical judgments; workflow and evidence states are separate."""

from typing import Literal

Assessability = Literal["assessable", "partially_assessable", "not_assessable"]
Answerability = Literal["answered", "partially_answered", "not_assessable"]
Confidence = Literal["high", "moderate", "low", "unknown"]
JudgmentDirection = Literal["supports", "insufficient_support", "against", "excluded", "indeterminate"]
ClinicalRole = Literal["primary", "competing_explanation", "acute_contributor", "comorbidity", "unexplained_finding", "boundary"]

DIRECTION_DESCRIPTION = (
    "针对 conditions.subject 中的中性判断对象：supports=已有正向依据，但不等于确诊；"
    "insufficient_support=当前正向依据不足，不表示存在反证；against=有实质反对依据，仍不等于排除；"
    "excluded=已有充分排除依据，须在 medical_basis 和 claims 中明确；indeterminate=目前无法判断方向。"
    "不能因未记载、尚未确立或未纳入候选就填写 against/excluded。"
)
CONFIDENCE_DESCRIPTION = "对本项判断方向的把握度，不是疾病存在概率，不决定候选地位或是否确诊。"


def concept_key(name: str) -> str:
    """Only normalize typography; medical aliases must be declared, never guessed."""
    return "".join(name.split()).casefold().replace("（", "(").replace("）", ")")


def normalize_differential_ranks(items) -> None:
    if len({item.rank for item in items}) != len(items):
        raise ValueError("Differential diagnosis ranks must be unique")
    items.sort(key=lambda item: item.rank)
    for rank, item in enumerate(items, start=1):
        item.rank = rank


LEGACY_JUDGMENT_NOTE = (
    "历史记录：status可能针对整句断言，direction的对象可能含否定；旧版against未区分支持不足与实质反证。"
    "结合原始陈述和病例依据重新评价，不能直接推导疾病阳性、反证或已排除。"
)


def validate_judgment_dimensions(value):
    if value.assessability == "not_assessable" and value.confidence != "unknown":
        raise ValueError("An unassessable judgment cannot have diagnostic confidence")
    if value.assessability == "not_assessable" and value.direction != "indeterminate":
        raise ValueError("An unassessable judgment has indeterminate direction")
    if value.direction == "excluded" and not any(
        relation.function != "background"
        for claim in value.claims for relation in claim.evidence_relations
    ):
        raise ValueError("An excluded judgment requires explicit patient evidence, not missing information")
    return value
