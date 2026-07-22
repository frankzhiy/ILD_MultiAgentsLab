"""Shared output contract appended after specialty content and guidelines."""

from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def specialty_output_contract(
    *,
    pointer_style: str,
    initial_stage: bool = True,
    partitioned_evidence: bool = False,
    extra_rules: tuple[str, ...] = (),
) -> str:
    targets = ", ".join(item.value for item in SpecialistTarget)
    rules = [
        f"专科问题的 specialty 只能是：{targets}；shared_context 不是专科。",
    ]
    if pointer_style == "evidence_id":
        rules.append(
            "每个 EvidencePointer 的 evidence_ids 恰好填写一个 ID；"
            "多个证据使用多个 EvidencePointer。"
        )
        if partitioned_evidence:
            rules.extend(
                [
                    "supporting_evidence 和 conflicting_evidence 只能引用 "
                    "diagnostic_evidence_units。",
                    "context_only_evidence_units 只能用于 related_evidence、"
                    "待确认观察或专科问题。",
                ]
            )
        else:
            rules.append(
                "supporting_evidence 和 conflicting_evidence 只能引用 "
                "may_support_diagnostic_claim=true 的 unit；其余 unit 只能用于 related_evidence。"
            )
    elif pointer_style == "radiology_proposition":
        rules.append(
            "supporting_evidence 和 conflicting_evidence 只能引用 "
            "disposition=thoracic_imaging 的 proposition。"
        )
    else:
        raise ValueError(f"Unknown pointer style: {pointer_style}")
    rules.append("所有 specialist_opinion_ids 必须为空列表。")
    rules.extend(extra_rules)
    return (
        "输出前最终契约（优先级高于示例和指南片段）：\n- "
        + "\n- ".join(rules)
    )
