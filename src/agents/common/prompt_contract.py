"""Shared output contract appended after specialty content and guidelines."""

from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def specialty_output_contract(
    *,
    pointer_style: str,
    initial_stage: bool = True,
    partitioned_evidence: bool = False,
    defer_case_evidence: bool = False,
    extra_rules: tuple[str, ...] = (),
) -> str:
    targets = ", ".join(item.value for item in SpecialistTarget)
    rules = [
        f"专科问题的 specialty 只能是：{targets}；shared_context 不是专科。",
    ]
    if defer_case_evidence:
        rules.append(
            "本阶段只把每项 assessment 拆成原子 claims，不选择或返回病例证据；"
            "程序将在下一阶段生成固定 claim × evidence 槽位。"
        )
    elif pointer_style == "evidence_id":
        rules.append(
            "一个 EvidencePointer 代表一张患者证据图：evidence_ids 可填写同一 "
            "Graph Unit 内一个或多个 ID；不得把同一图按 Evidence ID、命题或节点拆成多份证据。"
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
    if not defer_case_evidence:
        rules.append(
            "同一原文证据块在同一判断的结构化证据中只引用一次；"
            "若 schema 提供 evidence_relations，分别用 direction 表示支持方向、"
            "用 function 表示证据功能。"
        )
    rules.append("所有 specialist_opinion_ids 必须为空列表。")
    rules.extend(extra_rules)
    return (
        "输出前最终契约（优先级高于示例和指南片段）：\n- "
        + "\n- ".join(rules)
    )
