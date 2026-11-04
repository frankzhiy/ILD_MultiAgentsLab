"""Shared output contract appended after specialty content and guidelines."""

from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def specialty_output_contract(
    *,
    pointer_style: str,
    initial_stage: bool = True,
    partitioned_evidence: bool = False,
    claim_evidence: bool = False,
    extra_rules: tuple[str, ...] = (),
) -> str:
    targets = ", ".join(item.value for item in SpecialistTarget)
    rules = [
        f"专科问题的 specialty 只能是：{targets}；shared_context 不是专科。",
    ]
    if claim_evidence:
        rules.extend([
            "assessability=not_assessable 时，confidence 必须为 unknown；对不可评价性的把握不作为诊断置信度。",
            "正式判断不再输出混合status。direction只针对conditions.subject中的中性对象；"
            "区分supports、insufficient_support、against、excluded、indeterminate；"
            "confidence仅表示对本项判断的把握，不转换成支持或确诊。",
            "本阶段同时生成原子 claims 与每条 claim 的 evidence_relations；"
            "只返回实际使用的关联，不枚举 claim × evidence 组合，不返回无关槽位或 null。",
            "每条 claim 的 evidence_relations 只填写 evidence_ids、direction、function；"
            "claim_id、target_claim_id、原文和位置由程序补全，assessment.evidence 由程序汇总。",
            "同一证据可用于不同 claims；同一 claim 内同一证据只赋予一种方向与功能。"
            "审查关键支持证据和反证，不把资料未描述当作明确阴性。",
            "foundational 必须为 supports 或 weakens；background 必须为 neutral；"
            "discriminating、qualifying 可为 supports、weakens 或 neutral。",
            "只有当前专科可用于诊断判断的证据可作非 background 关联；"
            "其余背景资料若实际需要只能作 neutral/background，或用于 related_evidence。",
            "没有直接病例证据的资料缺失或条件性判断可用空 evidence_relations，"
            "在 medical_basis 或 limitations 说明具体依据与限制，不编造引用；"
            "未引用的证据不表示已审查并判定无关。",
        ])
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
    if not claim_evidence:
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
