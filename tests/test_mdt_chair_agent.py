import json
from copy import deepcopy

import pytest

from src.agents.mdt_chair.agent import (
    MDTChairAgent,
    _integration_generation_model,
    _ledger_from_draft,
    _ledger_generation_model,
    _source_ref_schema_constraints,
    _discussion_previous_view,
    _materialize_evidence_need_conversion_refs,
    _validate_review_destinations,
    build_chair_prompt_bundle,
    build_semantic_evidence_catalog,
    materialize_integration_draft,
    rebase_integration_to_active_judgments,
    resolve_chair_references,
    resolve_semantic_ledger,
)
from src.agents.mdt_chair.models import (
    ChairSemanticLedger,
    MDTChairIntegration,
    MDTChairIntegrationDraft,
)
from src.agents.mdt_discussion.integration import (
    apply_review_outcomes,
    append_round_responses,
    reconcile_discussion_references,
)
from src.agents.mdt_discussion.models import (
    SpecialtyAnswerReview,
    SpecialtyRoundResponse,
    SpecialtyTaskAnswer,
)
from src.llm.base import LLMResponse
from src.llm.structured import json_schema_response_format


SPECIALTIES = (
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
)


def pointer(quote="病例原文证据。"):
    return {
        "segment_id": "seg_001",
        "graph_unit_id": "seg_001_gu_001",
        "evidence_ids": ["seg_001_gu_001_ev_001"],
        "node_ids": ["seg_001_gu_001::prop_001"],
        "quote": quote,
    }


def evidence_bundle():
    return {
        "supporting": [pointer()],
        "weakening": [],
        "discriminating": [],
        "background": [],
    }


def guideline():
    return {
        "chunk_id": "guideline_chunk_1",
        "relevance": "用于限定该判断。",
        "application": "适用于当前病例的证据边界。",
        "guideline_id": "guideline_1",
        "title": "ILD 指南",
        "organization": "Test",
        "year": 2025,
        "source_file": "guideline.pdf",
        "page": 1,
        "section_path": ["诊断"],
        "quote": "指南运行时不应进入主持人输入。",
    }


def outputs():
    labels = {
        "pulmonology": "当前临床资料支持纤维化性间质性肺病框架。",
        "thoracic_radiology": "未提供原始影像，具体形态模式不可评价。",
        "rheumatology": "现有资料不足以评价结缔组织病归因。",
        "pathology": "未提供病理材料，组织学模式不可评价。",
    }
    values = {}
    for specialty in SPECIALTIES:
        questions = []
        gaps = []
        if specialty == "pulmonology":
            questions = [{
                "target_specialty": "thoracic_radiology",
                "question": "请影像科解释现有文字描述能够支持到什么层级。",
                "why_it_matters": "限定影像结论层级。",
                "decision_unlocked": "区分影像回应与完整资料需求。",
                "related_evidence": [pointer()],
            }]
            gaps = [{
                "available_information": "仅有肺功能异常概述。",
                "missing_information": "完整肺功能原始数值和时间序列。",
                "why_it_matters": "影响进展评价。",
                "decision_unlocked": "完成纵向生理变化判断。",
                "related_evidence": [pointer()],
            }]
        if specialty == "pathology":
            questions = [{
                "target_specialty": "thoracic_radiology",
                "question": "请提供完整 HRCT 影像和病变分布信息。",
                "why_it_matters": "核对取材代表性。",
                "decision_unlocked": "评价取材与异常区域是否对应。",
                "related_evidence": [],
            }]
        values[specialty] = {
            "professional_conclusions": {
                "specialty_question": f"{specialty} 当前能回答什么？",
                "assessability": "partially_assessable",
                "conclusions": [{
                    "conclusion_id": f"{specialty}_1",
                    "role": "primary" if specialty == "pulmonology" else "scope_or_evaluability",
                    "conclusion_type": "working_diagnosis" if specialty == "pulmonology" else "assessability",
                    "statement": labels[specialty],
                    "status": "favored" if specialty == "pulmonology" else "not_assessable",
                    "medical_basis": "由当前病例资料形成。",
                    "decision_impact": "限定本轮讨论范围。",
                    "evidence": evidence_bundle(),
                    "guideline_evidence": [guideline()],
                    "limitations": ["仅限当前输入。"],
                }],
                "interspecialty_questions": questions,
                "evidence_gaps": gaps,
                "boundaries": ["不是最终 MDT 诊断。"],
            },
            "clinical_reasoning": {"must_not_enter_prompt": "内部推理不得输入主持人。"},
        }
    return values


def source_ref(bundle, specialty, source_type, occurrence=0):
    source_type = {
        "native_conclusion": "specialty_assessment",
        "native_question": "interspecialty_question",
        "evidence_gap": "assessment_evidence_need",
    }.get(source_type, source_type)
    return [
        ref
        for ref, source in bundle.source_registry.items()
        if source.specialty == specialty and source.source_type == source_type
    ][occurrence]


def assessable_conflict_outputs():
    values = outputs()
    radiology = values["thoracic_radiology"]["professional_conclusions"][
        "conclusions"
    ][0]
    radiology.update({
        "role": "primary",
        "conclusion_type": "morphologic_pattern",
        "status": "favored",
        "statement": "现有影像首选形态解释为模式 B。",
    })
    return values


def conflict_ledger_payload(bundle, nature, claims):
    payload = ledger_payload(bundle)
    boundary_group = payload["claim_groups"][1]
    payload["claim_groups"] = [{
        "label": "需要跨专科协调的判断",
        "disposition": "conflict",
        "conflict_nature": nature,
        "comparison_target": "当前主要诊断或形态解释",
        "comparison_conditions": "当前时点、现有资料和可比较的判断层级。",
        "why_incompatible": "两项判断不能同时作为当前首选。",
        "decision_impact": "影响诊断信度和下一步检查路径。",
        "claims": claims,
    }, boundary_group]
    return payload


def ledger_payload(bundle):
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    pathology = source_ref(bundle, "pathology", "native_conclusion")
    pulmonary_question = source_ref(bundle, "pulmonology", "native_question")
    pathology_data_request = source_ref(bundle, "pathology", "native_question")
    pulmonary_gap = source_ref(bundle, "pulmonology", "evidence_gap")
    return {
        "claim_groups": [
            {
                "label": "纤维化性 ILD 框架",
                "disposition": "integrated",
                "claims": [{
                    "source_ref": pulmonary,
                    "statement": "支持纤维化性 ILD 框架。",
                    "subject": "当前肺部疾病",
                    "dimension": "工作诊断层级",
                    "timeframe": "当前",
                    "evidence_scope": "现有临床资料",
                    "professional_level": "disease_diagnosis",
                    "position_role": "preferred",
                    "epistemic_status": "affirms",
                }],
            },
            {
                "label": "专科不可评价边界",
                "disposition": "boundary",
                "claims": [
                    {
                        "source_ref": ref,
                        "statement": "资料不足，不可评价。",
                        "subject": subject,
                        "dimension": "专业判断",
                        "timeframe": "当前",
                        "evidence_scope": "现有资料",
                        "professional_level": "assessability",
                        "position_role": "boundary",
                        "epistemic_status": "not_assessable",
                    }
                    for ref, subject in (
                        (radiology, "影像模式"),
                        (rheumatology, "风湿归因"),
                        (pathology, "组织学模式"),
                    )
                ],
            },
        ],
        "question_routes": [
            {
                "source_refs": [pulmonary_question],
                "route": "question",
                "normalized_question": "现有影像观点可支持到什么层级？",
                "evidence_requirement": "",
                "target_specialties": ["thoracic_radiology"],
                "answer_links": [{
                    "specialty": "thoracic_radiology",
                    "source_refs": [radiology],
                    "relation": "evidence_boundary",
                }],
            },
            {
                "source_refs": [pathology_data_request],
                "route": "evidence_need",
                "normalized_question": "",
                "evidence_requirement": "完整 HRCT 影像和病变分布。",
                "target_specialties": ["thoracic_radiology"],
                "answer_links": [],
            },
        ],
        "evidence_need_groups": [
            {
                "source_refs": [pulmonary_gap],
                "required_information": "完整肺功能原始数值和时间序列。",
                "decision_role": "non_blocking_refinement",
                "coverage_source_refs": [],
            },
            {
                "source_refs": [pathology_data_request],
                "required_information": "完整 HRCT 影像和病变分布。",
                "decision_role": "non_blocking_refinement",
                "coverage_source_refs": [],
            },
        ],
    }


def ledger_generation_payload(payload):
    result = deepcopy(payload)
    result["question_routes"] = {
        ref: {
            key: value
            for key, value in route.items()
            if key not in {"source_refs", "route_id", "target_specialties"}
        }
        for route in payload["question_routes"]
        for ref in route["source_refs"]
    }
    return result


def test_ledger_generation_requires_one_fixed_slot_per_source_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    model = _ledger_generation_model(bundle)
    payload = ledger_generation_payload(ledger_payload(bundle))
    assert set(model.model_validate(payload).question_routes.model_dump()) == (
        bundle.question_refs_to_classify
    )

    missing = deepcopy(payload)
    missing["question_routes"].pop(next(iter(bundle.question_refs_to_classify)))
    with pytest.raises(ValueError, match="Field required"):
        model.model_validate(missing)

    extra = deepcopy(payload)
    extra["question_routes"]["S999"] = next(iter(payload["question_routes"].values()))
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        model.model_validate(extra)


def test_question_inherits_linked_assessment_evidence_when_own_links_are_empty():
    values = outputs()
    question = values["pulmonology"]["professional_conclusions"]["interspecialty_questions"][0]
    question["related_evidence"] = []
    question["related_assessment_ids"] = ["pulmonology_1"]

    bundle = build_chair_prompt_bundle("case-1", values)
    question_ref = source_ref(bundle, "pulmonology", "interspecialty_question")
    assert bundle.source_evidence[question_ref]["background"]
    assert bundle.prompt_input["specialties"][0]["interspecialty_questions"][0]["related_evidence"]


def test_ledger_generation_keeps_current_judgments_in_public_sections():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    model = _ledger_generation_model(bundle)
    payload = ledger_generation_payload(ledger_payload(bundle))
    payload["claim_groups"][0]["disposition"] = "follow_up"
    with pytest.raises(ValueError):
        model.model_validate(payload)


def test_ledger_generation_rejects_mixed_group_and_missing_comparison_target():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    model = _ledger_generation_model(bundle)
    payload = ledger_generation_payload(ledger_payload(bundle))
    mixed = deepcopy(payload)
    mixed["claim_groups"][1]["claims"].append(
        deepcopy(mixed["claim_groups"][0]["claims"][0])
    )
    with pytest.raises(ValueError):
        model.model_validate(mixed)

    invalid_link = deepcopy(payload)
    invalid_link["claim_groups"][0]["claims"][0]["evidence_links"] = [{
        "evidence_ref": next(iter(bundle.evidence_registry)),
        "relation": "discriminates",
        "rationale": "区分候选解释。",
        "comparison_target": "",
    }]
    with pytest.raises(ValueError):
        model.model_validate(invalid_link)


def test_ledger_keeps_atomic_status_when_assessment_is_not_assessable():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    pulmonary = payload["claim_groups"][0]["claims"][0]["source_ref"]
    bundle.source_metadata[pulmonary].update({
        "status": "not_assessable",
        "version_id": "V001",
        "conditions": {"timeframe": {"description": "当前"}},
    })

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert ledger.claim_groups[0].claims[0].epistemic_status == "affirms"
    assert ledger.claim_groups[0].claims[0].position_role == "preferred"


def test_ledger_allows_omitting_nondecisive_specialty_assessment():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    payload["claim_groups"].pop()

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)
    assert len(ledger.claim_groups) == len(payload["claim_groups"])


def test_discussion_ledger_covers_active_versions_without_requiring_superseded_sources():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    revised_outputs = deepcopy(initial_outputs)
    revised_outputs["pulmonology"]["professional_conclusions"]["conclusions"][0]["statement"] = (
        "当前只能维持待分类的间质性肺病工作判断。"
    )
    bundle = build_chair_prompt_bundle(
        "case-1", revised_outputs, discussion_round=1, source_seed=initial_bundle,
    )
    old_ref = source_ref(initial_bundle, "pulmonology", "native_conclusion")
    active_ref = bundle.prompt_input["specialties"][0]["specialty_assessments"][0]["source_ref"]
    payload = ledger_payload(initial_bundle)
    payload["claim_groups"][0]["claims"][0]["source_ref"] = active_ref
    payload["question_routes"] = []
    payload["evidence_need_groups"] = []

    assert active_ref != old_ref
    assert old_ref in bundle.source_registry
    assert active_ref in _source_ref_schema_constraints(bundle)["IntegratedLedgerAtomicClaim"]["source_ref"]
    assert old_ref not in _source_ref_schema_constraints(bundle)["IntegratedLedgerAtomicClaim"]["source_ref"]
    resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    payload["claim_groups"][0]["claims"][0]["source_ref"] = old_ref
    with pytest.raises(ValueError, match="superseded specialty assessment"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def integration_payload(bundle):
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    pathology = source_ref(bundle, "pathology", "native_conclusion")
    pulmonary_question = source_ref(bundle, "pulmonology", "native_question")
    pathology_data_request = source_ref(bundle, "pathology", "native_question")
    pulmonary_gap = source_ref(bundle, "pulmonology", "evidence_gap")
    return {
        "integrated_conclusions": [{
            "statement": "现有临床资料支持在纤维化性间质性肺病框架内继续讨论。",
            "medical_basis": "呼吸科形成了实体判断，其余专科的不可评价内容不作为支持者。",
            "decision_impact": "限定本轮可整合到疾病框架层级。",
            "role": "primary",
            "conclusion_type": "working_diagnosis",
            "status": "favored",
            "limitations": [],
            "source_refs": [pulmonary],
        }],
        "assessment_boundaries": [{
            "topic": "影像、风湿及病理判断边界",
            "scope": "other",
            "status": "not_assessable",
            "statement": "三科现有资料不足，相关专业层级不可评价。",
            "reason": "缺少原始影像、风湿评价资料和病理材料。",
            "decision_impact": "不能形成影像模式、风湿归因或组织学模式结论。",
            "related_evidence_need_source_refs": [pathology_data_request],
            "source_refs": [radiology, rheumatology, pathology],
        }],
        "conflicts": [],
        "questions": [{
            "question": "现有影像观点能够支持到什么层级？",
            "answers": [{
                "specialty": "thoracic_radiology",
                "relation": "evidence_boundary",
                "answer": "现有文字不足以形成具体影像模式判断。",
                "source_refs": [radiology],
            }],
            "resolution_status": "partially_resolved",
            "answer_summary": "影像科已经回应其当前判断边界。",
            "remaining_clarification": "实体影像问题仍受资料限制。",
            "why_it_matters": "避免把回应误当作问题已经解决。",
            "decision_unlocked": "区分回应状态和解决状态。",
            "related_evidence_need_source_refs": [pathology_data_request],
            "source_refs": [pulmonary_question],
        }],
        "evidence_needs": [
            {
                "status": "missing",
                "required_information": "完整肺功能原始数值和时间序列。",
                "available_information": "仅有异常概述。",
                "remaining_information": "仍缺原始数值和对应时间点。",
                "why_it_matters": "影响进展评价。",
                "decision_unlocked": "完成纵向生理变化判断。",
                "source_refs": [pulmonary_gap],
            },
            {
                "status": "missing",
                "required_information": "完整 HRCT 影像和病变分布。",
                "available_information": "仅有不完整文字描述。",
                "remaining_information": "仍缺原始影像与完整分布。",
                "why_it_matters": "影响取材代表性和影像模式评价。",
                "decision_unlocked": "核对主要异常区域。",
                "source_refs": [pathology_data_request],
            },
        ],
    }


def integration_generation_payload(bundle, ledger_value):
    """Represent claim provenance once, as ledger claim selections."""

    payload = integration_payload(bundle)
    claims_by_disposition = {
        disposition: {
            claim["source_ref"]: f"T{group_index:03d}-A{claim_index:03d}"
            for group_index, group in enumerate(ledger_value["claim_groups"], 1)
            if group["disposition"] == disposition
            for claim_index, claim in enumerate(group["claims"], 1)
        }
        for disposition in ("integrated", "boundary", "conflict")
    }
    for item in payload["integrated_conclusions"]:
        item["atomic_claim_ids"] = [
            claims_by_disposition["integrated"][ref]
            for ref in item.pop("source_refs")
        ]
    for item in payload["assessment_boundaries"]:
        refs = item.pop("source_refs")
        item["atomic_claim_ids"] = [
            claims_by_disposition["boundary"][ref]
            for ref in refs
            if ref in claims_by_disposition["boundary"]
        ]
        item["question_source_refs"] = [
            ref
            for ref in refs
            if bundle.source_registry[ref].source_type == "interspecialty_question"
        ]
        blocking_refs = {
            ref
            for group in ledger_value["evidence_need_groups"]
            if group["decision_role"] == "blocking_boundary"
            for ref in group["source_refs"]
        }
        item["related_evidence_need_source_refs"] = [
            ref
            for ref in item["related_evidence_need_source_refs"]
            if ref in blocking_refs
        ]
    for conflict in payload["conflicts"]:
        for position in conflict["positions"]:
            position["atomic_claim_ids"] = [
                claims_by_disposition["conflict"][ref]
                for ref in position.pop("source_refs")
            ]
    return payload


def resolved_ledger(bundle):
    return resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload(bundle)), bundle
    )


def test_prompt_projection_excludes_internal_reasoning_and_runtime_guidelines():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    compact = json.dumps(bundle.prompt_input, ensure_ascii=False)
    assert "内部推理不得输入主持人" not in compact
    assert "指南运行时不应进入主持人输入" not in compact
    assert "guideline_evidence" not in compact
    assert "declared_roles" not in compact
    assert any(bundle.source_guidelines.values())
    assert "evidence_registry" not in bundle.prompt_input
    for specialty in bundle.prompt_input["specialties"]:
        for assessment in specialty["specialty_assessments"]:
            expected = {
                evidence_ref
                for values in bundle.source_evidence[assessment["source_ref"]].values()
                for evidence_ref in values
            }
            assert {
                option["evidence_ref"]
                for option in assessment["evidence_options"]
            } == expected


def test_chair_projects_relation_dimensions_into_legacy_evidence_groups():
    values = outputs()
    values["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"] = {
        "evidence_relations": [{
            **pointer(),
            "direction": "supports",
            "function": "qualifying",
        }]
    }

    bundle = build_chair_prompt_bundle("case-1", values)
    pulmonary = source_ref(bundle, "pulmonology", "specialty_assessment")

    assert bundle.source_evidence[pulmonary]["supporting"] == []
    assert bundle.source_evidence[pulmonary]["qualifying"] == [
        "seg_001_gu_001_ev_001"
    ]


def test_prompt_projection_labels_each_specialty_source_type():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    specialty = bundle.prompt_input["specialties"][0]
    assert {item["source_type"] for item in specialty["specialty_assessments"]} == {
        "specialty_assessment"
    }
    assert {item["source_type"] for item in specialty["interspecialty_questions"]} == {
        "interspecialty_question"
    }
    assert {item["source_type"] for item in specialty["evidence_needs"]} == {
        "assessment_evidence_need"
    }


def test_discussion_answer_is_not_projected_as_a_formal_specialty_judgment():
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="现有文字只能支持有限影像表型。",
        confidence="moderate",
        medical_basis="缺少原始影像。",
        changed_from_previous=False,
    )
    response = SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )
    current = append_round_responses(outputs(), [response])

    bundle = build_chair_prompt_bundle("case-1", current)
    radiology = next(
        item
        for item in bundle.prompt_input["specialties"]
        if item["specialty"] == "thoracic_radiology"
    )

    assert len(radiology["specialty_assessments"]) == 1
    assert len(radiology["discussion_answers"]) == 1
    assert radiology["discussion_answers"][0]["source_type"] == "discussion_answer"
    assert {
        bundle.source_registry[ref].source_type
        for ref in bundle.source_registry
        if bundle.source_metadata[ref].get("assessment_id") == answer.answer_id
    } == {"discussion_answer"}


def test_prior_chair_state_is_rebased_to_the_current_judgment_version():
    previous_outputs = outputs()
    prior_item = previous_outputs["pulmonology"]["professional_conclusions"][
        "conclusions"
    ][0]
    prior_item.update({
        "judgment_id": "pulmonology_1",
        "version_id": "pulmonology_1@v001",
        "previous_version_id": None,
        "conditions": {
            "subject": "纤维化性间质性肺病",
            "professional_level": "disease_diagnosis",
            "timeframe": {"kind": "current", "description": "当前评估时点。"},
            "evidence_scope": {
                "evidence_ids": ["seg_001_gu_001_ev_001"],
                "source_types": ["case_evidence"],
                "scope_limitations": [],
            },
            "applicability_conditions": [],
        },
    })
    previous_bundle = build_chair_prompt_bundle("case-1", previous_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(previous_bundle)),
        previous_bundle,
        resolved_ledger(previous_bundle),
    )
    old_ref = next(
        ref
        for ref, metadata in previous_bundle.source_metadata.items()
        if metadata.get("version_id") == "pulmonology_1@v001"
    )

    current_outputs = deepcopy(previous_outputs)
    current_item = current_outputs["pulmonology"]["professional_conclusions"][
        "conclusions"
    ][0]
    current_item.update({
        "statement": "现有资料仅支持未分类间质性肺病。",
        "version_id": "pulmonology_1@v002",
        "previous_version_id": "pulmonology_1@v001",
    })
    current_bundle = build_chair_prompt_bundle(
        "case-1", current_outputs, source_seed=previous_bundle
    )
    new_ref = next(
        ref
        for ref, metadata in current_bundle.source_metadata.items()
        if metadata.get("version_id") == "pulmonology_1@v002"
    )

    rebased = rebase_integration_to_active_judgments(previous, current_bundle)
    serialized = json.dumps(rebased.model_dump(mode="json"), ensure_ascii=False)
    current_prompt = json.dumps(current_bundle.prompt_input, ensure_ascii=False)

    assert old_ref not in serialized
    assert new_ref not in serialized
    assert new_ref in current_prompt
    assert "现有资料仅支持未分类间质性肺病。" in current_prompt


def test_llm_json_schemas_do_not_contain_program_generated_ids():
    schemas = json.dumps(
        [ChairSemanticLedger.model_json_schema(), MDTChairIntegration.model_json_schema()]
    )
    for field in (
        "claim_id", "topic_id", "route_id", "group_id", "conclusion_id",
        "boundary_id", "conflict_id", "question_id", "need_id", "case_id",
    ):
        assert f'"{field}"' not in schemas


def test_program_backfills_v5_ids_provenance_and_separates_boundaries():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger = resolved_ledger(bundle)
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)), bundle, ledger
    )
    assert result.schema_version == "mdt_chair.v9"
    assert result.case_id == "case-1"
    assert result.integrated_conclusions[0].conclusion_id == "IC001"
    assert result.integrated_conclusions[0].supporting_specialties == ["pulmonology"]
    assert result.integrated_conclusions[0].guideline_evidence
    assert result.assessment_boundaries[0].boundary_id == "B001"
    assert result.assessment_boundaries[0].specialties == [
        "thoracic_radiology", "rheumatology", "pathology"
    ]
    assert result.questions[0].question_id == "Q001"
    assert [item.need_id for item in result.evidence_needs] == ["EN001", "EN002"]


def test_chair_conclusion_uses_only_atomic_claim_evidence_links():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    evidence_ref = bundle.source_evidence[pulmonary]["supporting"][0]
    semantic_payload = ledger_payload(bundle)
    semantic_payload["claim_groups"][0]["claims"][0]["evidence_links"] = [{
        "evidence_ref": evidence_ref,
        "relation": "supports",
        "rationale": "该患者证据图直接支持纤维化性 ILD 框架这一原子判断。",
    }]
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(semantic_payload), bundle
    )
    synthesis_payload = integration_payload(bundle)
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    synthesis_payload["integrated_conclusions"][0]["source_refs"] = [radiology]
    synthesis_payload["integrated_conclusions"][0]["atomic_claim_ids"] = [
        "T001-A001"
    ]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(synthesis_payload), bundle, ledger
    )

    conclusion = result.integrated_conclusions[0]
    assert conclusion.atomic_claim_ids == ["T001-A001"]
    assert conclusion.source_refs == [pulmonary]
    assert len(conclusion.evidence.links) == 1
    assert conclusion.evidence.links[0].target_claim_id == "T001-A001"
    assert conclusion.evidence.links[0].evidence_ref == evidence_ref
    assert conclusion.evidence.links[0].relation == "supports"
    assert [item.evidence_ref for item in conclusion.evidence.supporting] == [
        evidence_ref
    ]
    assert conclusion.evidence.weakening == []


def test_integration_draft_derives_claim_sources_from_ledger():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    draft_payload = integration_generation_payload(bundle, ledger_value)
    schema = MDTChairIntegrationDraft.model_json_schema()
    for name in (
        "IntegratedConclusionDraft",
        "AssessmentBoundaryDraft",
        "ConflictPositionDraft",
    ):
        assert "source_refs" not in schema["$defs"][name]["properties"]

    ledger = resolved_ledger(bundle)
    result = materialize_integration_draft(
        MDTChairIntegrationDraft.model_validate(draft_payload), ledger, bundle
    )
    assert result.integrated_conclusions[0].source_refs == [
        source_ref(bundle, "pulmonology", "specialty_assessment")
    ]
    assert result.assessment_boundaries[0].source_refs == [
        source_ref(bundle, specialty, "specialty_assessment")
        for specialty in ("thoracic_radiology", "rheumatology", "pathology")
    ]

    draft_payload["assessment_boundaries"][0]["source_refs"] = [
        source_ref(bundle, "pulmonology", "specialty_assessment")
    ]
    with pytest.raises(ValueError, match="source_refs are derived"):
        MDTChairIntegrationDraft.model_validate(draft_payload)


def test_integration_draft_requires_explicit_claim_selection():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    payload = integration_generation_payload(bundle, ledger_value)
    payload["integrated_conclusions"][0]["atomic_claim_ids"] = []
    with pytest.raises(ValueError, match="at least 1 item"):
        MDTChairIntegrationDraft.model_validate(payload)

    payload = integration_generation_payload(bundle, ledger_value)
    payload["assessment_boundaries"][0]["atomic_claim_ids"] = []
    payload["assessment_boundaries"][0]["question_source_refs"] = []
    payload["assessment_boundaries"][0]["related_evidence_need_source_refs"] = []
    draft = MDTChairIntegrationDraft.model_validate(payload)
    result = materialize_integration_draft(draft, resolved_ledger(bundle), bundle)
    assert result.assessment_boundaries == []

    legacy = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    assert legacy.integrated_conclusions[0].atomic_claim_ids == []
    assert legacy.integrated_conclusions[0].evidence.links == []


def test_question_only_boundary_has_question_provenance_without_claim_evidence():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    payload = integration_generation_payload(bundle, ledger_value)
    boundary = payload["assessment_boundaries"][0]
    boundary["atomic_claim_ids"] = []
    boundary["question_source_refs"] = [
        source_ref(bundle, "pulmonology", "interspecialty_question")
    ]
    ledger = resolved_ledger(bundle)
    result = materialize_integration_draft(
        MDTChairIntegrationDraft.model_validate(payload), ledger, bundle
    )
    resolved = resolve_chair_references(result, bundle, ledger)
    boundary = resolved.assessment_boundaries[0]
    assert boundary.source_refs == [
        source_ref(bundle, "pulmonology", "interspecialty_question")
    ]
    assert boundary.atomic_claim_ids == []
    assert boundary.evidence.links == []


def test_blocking_need_only_boundary_has_no_claim_evidence():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger = resolved_ledger(bundle)
    need_group = ledger.evidence_need_groups[0]
    need_group.decision_role = "blocking_boundary"
    payload = integration_generation_payload(bundle, ledger_payload(bundle))
    payload["integrated_conclusions"] = []
    payload["questions"] = []
    payload["evidence_needs"] = []
    boundary = payload["assessment_boundaries"][0]
    boundary["atomic_claim_ids"] = []
    boundary["question_source_refs"] = []
    boundary["related_evidence_need_source_refs"] = need_group.source_refs

    result = materialize_integration_draft(
        MDTChairIntegrationDraft.model_validate(payload), ledger, bundle
    )
    result = resolve_chair_references(result, bundle, ledger)
    assert result.assessment_boundaries[0].source_refs == need_group.source_refs
    assert result.assessment_boundaries[0].atomic_claim_ids == []
    assert result.assessment_boundaries[0].evidence.links == []


def test_limitation_only_need_cannot_become_chair_request():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    for group in ledger_value["evidence_need_groups"]:
        group["decision_role"] = "limitation_only"
    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(ledger_value), bundle)
    model = _integration_generation_model(ledger)
    payload = integration_generation_payload(bundle, ledger_value)
    payload["evidence_needs"] = []

    assert model.model_validate(payload).evidence_needs == []
    assert _source_ref_schema_constraints(bundle, semantic_ledger=ledger)["EvidenceNeed"]["source_refs"] == set()


def test_conflict_draft_derives_each_position_source_from_selected_claim():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger = resolved_ledger(bundle)
    conflict_group = ledger.claim_groups[0]
    conflict_group.disposition = "conflict"
    conflict_group.conflict_nature = "direct_contradiction"
    opposing = ledger.claim_groups[1].claims[0].model_copy(deep=True)
    opposing.claim_id = "T001-A002"
    opposing.epistemic_status = "denies"
    conflict_group.claims.append(opposing)
    payload = {
        "integrated_conclusions": [],
        "assessment_boundaries": [],
        "questions": [],
        "evidence_needs": [],
        "conflicts": [{
            "topic": "同一命题",
            "conflict_nature": "direct_contradiction",
            "conflict_domain": "diagnostic_interpretation",
            "comparison_target": "命题 X",
            "comparison_conditions": "相同条件",
            "positions": [
                {"stance": "affirms", "position": "肯定", "atomic_claim_ids": ["T001-A001"]},
                {"stance": "denies", "position": "否定", "atomic_claim_ids": ["T001-A002"]},
            ],
            "why_incompatible": "不能同时成立",
            "decision_impact": "需要讨论",
            "resolution_requirement": "核对依据",
        }],
    }
    result = materialize_integration_draft(
        MDTChairIntegrationDraft.model_validate(payload), ledger, bundle
    )
    assert [position.source_refs for position in result.conflicts[0].positions] == [
        [claim.source_ref] for claim in conflict_group.claims
    ]

    payload["conflicts"][0]["positions"][1]["atomic_claim_ids"] = ["T002-A001"]
    with pytest.raises(ValueError, match="belongs to boundary, not conflict"):
        materialize_integration_draft(
            MDTChairIntegrationDraft.model_validate(payload), ledger, bundle
        )


def test_no_integrated_ledger_claims_forbid_generated_conclusions():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    ledger_value["claim_groups"][0]["disposition"] = "follow_up"
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_value), bundle
    )
    model = _integration_generation_model(ledger)
    schema = json_schema_response_format(
        model,
        "mdt_chair_integration",
        string_field_constraints=_source_ref_schema_constraints(
            bundle, semantic_ledger=ledger
        ),
    )["json_schema"]["schema"]
    assert schema["properties"]["integrated_conclusions"]["maxItems"] == 0
    assert schema["properties"]["conflicts"]["maxItems"] == 0
    assert schema["$defs"]["IntegratedConclusionDraft"]["properties"][
        "atomic_claim_ids"
    ]["maxItems"] == 0

    payload = integration_generation_payload(bundle, ledger_payload(bundle))
    with pytest.raises(ValueError, match="at most 0 items"):
        model.model_validate(payload)


def test_ledger_rejects_boundary_claims_in_substantive_group():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    payload["claim_groups"][0]["claims"].append(
        payload["claim_groups"][1]["claims"][0]
    )
    with pytest.raises(ValueError, match="places boundary claims"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_ledger_reclassifies_single_specialty_placeholder_conflict():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    claim = payload["claim_groups"][0]["claims"][0]
    payload["claim_groups"].append({
        "label": "单科占位冲突",
        "disposition": "conflict",
        "conflict_nature": "direct_contradiction",
        "comparison_target": "诊断",
        "comparison_conditions": "现有资料",
        "why_incompatible": "仅一科意见",
        "decision_impact": "无跨科分歧",
        "claims": [claim],
    })
    payload["evidence_need_groups"].append({
        "source_refs": [],
        "required_information": "没有来源的占位需求",
        "decision_role": "limitation_only",
    })

    payload["question_routes"] = {
        route["source_refs"][0]: {
            key: value for key, value in route.items()
            if key not in {"source_refs", "target_specialties"}
        }
        for route in payload["question_routes"]
    }
    draft = _ledger_generation_model(bundle).model_validate(payload)
    ledger = resolve_semantic_ledger(_ledger_from_draft(draft, bundle), bundle)

    assert len(ledger.claim_groups) == 3
    assert ledger.claim_groups[-1].disposition == "integrated"
    assert all(group.source_refs for group in ledger.evidence_need_groups)
    assert {event["action"] for event in bundle.normalization_events} == {
        "reclassified_invalid_conflict", "dropped_unsourced_requests"
    }


def test_integration_retries_full_snapshot_after_invalid_section_selection():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger_value = ledger_payload(bundle)
    ledger_value["claim_groups"].pop(0)
    invalid = integration_generation_payload(bundle, ledger_payload(bundle))
    corrected = {
        "integrated_conclusions": [],
        "assessment_boundaries": [],
        "conflicts": [],
        "questions": [],
        "evidence_needs": [],
    }

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.formats = []

        def complete(self, messages, **kwargs):
            self.formats.append(kwargs["response_format"]["type"])
            payload = (
                ledger_generation_payload(ledger_value)
                if len(self.formats) == 1
                else invalid
                if len(self.formats) == 2
                else corrected
            )
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    llm = FakeLLM()
    result, trace = MDTChairAgent(
        llm,
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=2,
    ).integrate(bundle)
    assert result.integrated_conclusions == []
    assert trace["integration_generation"]["attempts"][0]["validated"] is False
    assert llm.formats == ["json_schema", "json_schema", "json_schema"]


def test_ledger_regenerates_full_snapshot_for_mixed_claim_group():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    corrected_ledger = ledger_payload(bundle)
    invalid_ledger = deepcopy(corrected_ledger)
    invalid_ledger["claim_groups"][0]["claims"].append(
        invalid_ledger["claim_groups"][1]["claims"][0]
    )

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.formats = []

        def complete(self, messages, **kwargs):
            self.formats.append(kwargs["response_format"]["type"])
            payload = (
                ledger_generation_payload(invalid_ledger)
                if len(self.formats) == 1
                else ledger_generation_payload(corrected_ledger)
                if len(self.formats) == 2
                else integration_generation_payload(bundle, corrected_ledger)
            )
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    llm = FakeLLM()
    result, trace = MDTChairAgent(
        llm,
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=2,
    ).integrate(bundle)
    assert result.integrated_conclusions
    assert trace["ledger_generation"]["attempts"][0]["validated"] is False
    assert llm.formats == ["json_schema", "json_schema", "json_schema"]


def test_semantic_ledger_rejects_two_relations_for_same_claim_locator():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    evidence_ref = bundle.source_evidence[pulmonary]["supporting"][0]
    payload = ledger_payload(bundle)
    payload["claim_groups"][0]["claims"][0]["evidence_links"] = [
        {
            "evidence_ref": evidence_ref,
            "relation": "supports",
            "rationale": "直接支持当前原子判断。",
        },
        {
            "evidence_ref": evidence_ref,
            "relation": "qualifies",
            "rationale": "又被错误地标作限定。",
        },
    ]

    with pytest.raises(ValueError, match="multiple relations"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_ledger_reports_relation_conflict_even_if_other_assessment_is_omitted():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    claim = payload["claim_groups"][0]["claims"][0]
    evidence_ref = bundle.source_evidence[claim["source_ref"]]["supporting"][0]
    claim["evidence_links"] = [
        {"evidence_ref": evidence_ref, "relation": "supports", "rationale": "主要证据。"},
        {"evidence_ref": evidence_ref, "relation": "qualifies", "rationale": "范围限制。"},
    ]
    payload["claim_groups"][1]["claims"].pop()

    with pytest.raises(ValueError) as error:
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert "multiple relations" in str(error.value)


def test_ledger_repairs_all_conflicting_evidence_links_without_regenerating():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    for group_index in (0, 1):
        claim = payload["claim_groups"][group_index]["claims"][0]
        evidence_ref = bundle.source_evidence[claim["source_ref"]]["supporting"][0]
        claim["evidence_links"] = [
            {"evidence_ref": evidence_ref, "relation": "supports", "rationale": "主要证据。"},
            {"evidence_ref": evidence_ref, "relation": "qualifies", "rationale": "范围限制。"},
        ]

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.formats = []

        def complete(self, messages, **kwargs):
            self.formats.append(kwargs["response_format"]["type"])
            if len(self.formats) == 1:
                value = ledger_generation_payload(payload)
            elif len(self.formats) == 2:
                assert "claim_groups[0].claims[0]" in messages[-1].content
                assert "claim_groups[1].claims[0]" in messages[-1].content
                value = {"edits": [
                    {"op": "remove_indices", "path": "/claim_groups/0/claims/0/evidence_links", "value": [1]},
                    {"op": "remove_indices", "path": "/claim_groups/1/claims/0/evidence_links", "value": [1]},
                ]}
            else:
                value = integration_generation_payload(bundle, payload)
            return LLMResponse(
                content=json.dumps(value, ensure_ascii=False),
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    llm = FakeLLM()
    result, trace = MDTChairAgent(
        llm,
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=2,
    ).integrate(bundle)

    assert result.integrated_conclusions
    assert llm.formats == ["json_schema", "json_object", "json_schema"]
    assert trace["ledger_generation"]["attempts"][0]["validated"] is False
    assert trace["ledger_generation"]["attempts"][1]["validated"] is True


def test_integration_schema_forbids_sections_without_ledger_basis():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    for route in payload["question_routes"]:
        route["route"] = "evidence_need"
        route["answer_links"] = []
    for group in payload["evidence_need_groups"]:
        group["decision_role"] = "blocking_boundary"
    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    schema = json_schema_response_format(
        _integration_generation_model(ledger), "mdt_chair_integration"
    )["json_schema"]["schema"]

    assert schema["properties"]["questions"]["maxItems"] == 0
    assert schema["properties"]["evidence_needs"]["maxItems"] == 0


def test_integration_schema_forbids_uncited_answers():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    for route in payload["question_routes"]:
        route["answer_links"] = []
    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    schema = json_schema_response_format(
        _integration_generation_model(ledger), "mdt_chair_integration",
        string_field_constraints=_source_ref_schema_constraints(
            bundle, semantic_ledger=ledger
        ),
    )["json_schema"]["schema"]

    question = schema["$defs"]["ChairQuestionWithoutAnswers"]
    assert question["properties"]["answers"]["maxItems"] == 0
    assert question["properties"]["source_refs"]["items"]["enum"] == [
        source_ref(bundle, "pulmonology", "native_question")
    ]


def test_semantic_ledger_rejects_evidence_from_another_assessment():
    values = outputs()
    foreign_pointer = pointer("另一条病例证据。")
    foreign_pointer.update({
        "segment_id": "seg_999",
        "graph_unit_id": "seg_999_gu_001",
        "evidence_ids": ["seg_999_gu_001_ev_001"],
        "node_ids": [],
    })
    values["rheumatology"]["professional_conclusions"]["conclusions"][0][
        "evidence"
    ]["supporting"] = [foreign_pointer]
    bundle = build_chair_prompt_bundle("case-1", values)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    allowed = {
        evidence_ref
        for values in bundle.source_evidence[pulmonary].values()
        for evidence_ref in values
    }
    foreign = next(
        evidence_ref
        for evidence_ref in bundle.evidence_registry
        if evidence_ref not in allowed
    )
    payload = ledger_payload(bundle)
    payload["claim_groups"][0]["claims"][0]["evidence_links"] = [{
        "evidence_ref": foreign,
        "relation": "supports",
        "rationale": "该证据属于另一条专科判断。",
    }]

    with pytest.raises(ValueError, match="outside its specialty source"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_assessment_boundary_accepts_unresolved_native_question_source():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    question_ref = source_ref(bundle, "pulmonology", "native_question")
    payload["assessment_boundaries"][0]["source_refs"] = [question_ref]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )

    boundary = result.assessment_boundaries[0]
    assert boundary.source_citations[0].source_type == "interspecialty_question"
    assert boundary.specialties == ["pulmonology"]


def test_discussion_program_rebuilds_question_answer_and_evidence_need_refs():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    relation_only_ref = previous.evidence_needs.pop(0).source_refs[0]
    previous.questions[0].related_evidence_need_source_refs.append(relation_only_ref)
    assert relation_only_ref not in {
        citation.source_ref
        for item in previous.questions
        for citation in item.source_citations
    }
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="现有文字只能支持有限影像表型。",
        confidence="moderate",
        medical_basis="缺少原始影像。",
        changed_from_previous=True,
        remaining_limitation="仍需完整HRCT。",
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="accept_answer",
        rationale="回答已覆盖原问题。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    current_bundle = build_chair_prompt_bundle(
        "case-1", current_outputs, source_seed=initial_bundle
    )
    previous_view, preserved = _discussion_previous_view(previous, current_bundle)
    stable_question_ref = previous_view["questions"][0]["source_refs"][0]
    assert stable_question_ref == previous.questions[0].source_refs[0]
    assert stable_question_ref in current_bundle.source_registry
    assert current_bundle.source_registry[stable_question_ref].source_type == (
        "interspecialty_question"
    )
    assert stable_question_ref in preserved["IntegratedQuestion"]["source_refs"]
    payload = integration_payload(current_bundle)
    payload["questions"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap"),
        source_ref(current_bundle, "rheumatology", "native_conclusion"),
    ]
    payload["questions"][0]["question"] = "主持人本轮生成了语义差异很大的问题文本。"
    payload["questions"][0]["answers"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap")
    ]
    payload["evidence_needs"][0]["source_refs"] = [
        source_ref(current_bundle, "rheumatology", "native_conclusion")
    ]
    result = MDTChairIntegration.model_validate(payload)

    reconcile_discussion_references(result, previous, responses, current_bundle, reviews)
    result = resolve_chair_references(result, current_bundle)

    question = result.questions[0]
    assert {item.source_type for item in question.source_citations} == {
        "interspecialty_question"
    }
    assert {
        current_bundle.source_registry[ref].source_type
        for ref in question.related_evidence_need_source_refs
    } <= {"interspecialty_question", "assessment_evidence_need"}
    assert relation_only_ref in question.related_evidence_need_source_refs
    assert all(
        len({citation.specialty for citation in item.source_citations}) == 1
        for item in question.answers
    )
    assert question.answers[-1].answer == answer.answer
    assert {item.source_type for item in question.answers[-1].source_citations} == {
        "discussion_answer"
    }
    assert all(
        citation.source_type
        in {
            "interspecialty_question",
            "assessment_evidence_need",
            "specialty_assessment",
        }
        for need in result.evidence_needs
        for citation in need.source_citations
    )
    assert result.evidence_needs[0].required_information == (
        previous.evidence_needs[0].required_information
    )


def test_discussion_bundle_does_not_reclassify_initial_questions():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    discussion_bundle = build_chair_prompt_bundle(
        "case-1",
        initial_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )

    initial_question_refs = {
        ref
        for ref, source in initial_bundle.source_registry.items()
        if source.source_type == "interspecialty_question"
    }
    registered_discussion_refs = {
        ref
        for ref, source in discussion_bundle.source_registry.items()
        if source.source_type == "interspecialty_question"
    }

    assert registered_discussion_refs == initial_question_refs
    assert discussion_bundle.question_refs_to_classify == set()
    assert all(
        specialty["interspecialty_questions"] == []
        for specialty in discussion_bundle.prompt_input["specialties"]
    )
    payload = ledger_payload(discussion_bundle)
    payload["question_routes"] = []
    payload["evidence_need_groups"] = []
    resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), discussion_bundle)


def test_discussion_source_refs_are_append_only_across_rounds():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    old_questions = {
        item.quote: ref
        for ref, item in initial_bundle.source_registry.items()
        if item.source_type == "interspecialty_question"
    }
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-pulmonology-A",
        task_id="R01-Q001-pulmonology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="基于现有资料完成回答。",
        confidence="moderate",
        medical_basis="现有资料足以形成有限判断。",
        changed_from_previous=True,
    )
    cumulative = append_round_responses(
        initial_outputs,
        [SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        [],
    )

    current = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=1,
        source_seed=initial_bundle,
    )

    assert {
        item.quote: ref
        for ref, item in current.source_registry.items()
        if item.source_type == "interspecialty_question"
    } == old_questions
    answer_ref = next(
        ref
        for ref, metadata in current.source_metadata.items()
        if metadata.get("assessment_id") == answer.answer_id
    )
    assert int(answer_ref[1:]) > max(int(ref[1:]) for ref in old_questions.values())


def test_discussion_ignores_llm_reclassification_of_stable_questions():
    bundle = build_chair_prompt_bundle("case-1", outputs(), discussion_round=1)
    prior_refs = {
        ref
        for ref, item in bundle.source_registry.items()
        if item.source_type == "interspecialty_question"
    }
    bundle.already_classified_question_refs = prior_refs
    payload = ledger_payload(build_chair_prompt_bundle("case-1", outputs()))

    result = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(payload),
        bundle,
    )

    assert result.question_routes == []
    assert any(
        event["action"] == "ignored_already_classified_questions"
        for event in bundle.normalization_events
    )


def test_discussion_bundle_classifies_new_candidates_only_in_their_round():
    initial_outputs = outputs()
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="形成新的有限影像判断。",
        confidence="moderate",
        medical_basis="基于现有材料。",
        changed_from_previous=True,
        new_questions=[{
            "target_specialty": "pulmonology",
            "question": "现有低氧是否能由肺实质异常充分解释？",
            "why_it_matters": "限定低氧归因。",
            "decision_unlocked": "决定是否并行评估其他机制。",
            "related_evidence": [],
        }],
        evidence_gaps=[{
            "available_information": "已有文字摘要。",
            "missing_information": "缺少原始HRCT。",
            "why_it_matters": "影响影像判断。",
            "decision_unlocked": "可完成形态评价。",
            "related_evidence": [],
        }],
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="convert_to_evidence_need",
        rationale="当前判断已成立，原始影像只用于进一步明确。",
        evidence_gap={
            "available_information": "已有文字摘要。",
            "missing_information": "缺少原始HRCT。",
            "why_it_matters": "可进一步提高影像判断的明确度。",
            "decision_unlocked": "进一步明确形态评价。",
            "related_evidence": [],
        },
    )]
    cumulative = append_round_responses(initial_outputs, responses, reviews)

    first_round = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=1,
    )
    second_round = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=2,
    )

    assert len(first_round.question_refs_to_classify) == 0
    assert len(first_round.evidence_need_refs_to_classify) == 1
    assert all(
        not specialty["interspecialty_questions"]
        for specialty in first_round.prompt_input["specialties"]
    )
    assert second_round.question_refs_to_classify == set()
    assert second_round.evidence_need_refs_to_classify == set()


def test_discussion_agent_updates_previous_issues_without_reclassifying_them():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="现有文字只能支持有限影像表型。",
        confidence="moderate",
        medical_basis="缺少原始影像。",
        changed_from_previous=True,
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="request_clarification",
        rationale="仍需限定可支持到什么层级。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    ledger_value = ledger_payload(bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            payload = (
                ledger_generation_payload(ledger_value)
                if self.calls == 1
                else integration_generation_payload(bundle, ledger_value)
            )
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"usage": {}},
            )

    agent = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.integrate(
        bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )

    assert trace["semantic_ledger"]["question_routes"] == []
    assert result.questions[0].question_id == "Q001"
    assert result.questions[0].answers[-1].answer == answer.answer


def test_discussion_accept_boundary_moves_stable_question_to_boundary():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    question = previous.questions[0]
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id=question.question_id,
        answerability="partially_answered",
        answer="现有资料能够形成判断边界，但不能完成实体判断。",
        confidence="moderate",
        medical_basis="缺少关键原始影像。",
        changed_from_previous=True,
        remaining_limitation="没有原始影像便不能完成模式判断。",
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=question.question_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="accept_boundary",
        rationale="没有关键影像便不能完成该判断。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    ledger_value = ledger_payload(bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []
    integration_value = integration_generation_payload(bundle, ledger_value)

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            value = (
                ledger_generation_payload(ledger_value)
                if self.calls == 1
                else integration_value
            )
            return LLMResponse(content=json.dumps(value, ensure_ascii=False), raw={})

    result, _ = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    ).integrate(
        bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )
    apply_review_outcomes(result, reviews)

    assert question.source_refs == previous.questions[0].source_refs
    assert any(
        set(question.source_refs).intersection(boundary.source_refs)
        for boundary in result.assessment_boundaries
    )
    assert result.questions == []


def test_discussion_projects_only_requester_approved_evidence_need():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="现有材料不能确认具体模式，但可确认有限纤维化表型。",
        confidence="moderate",
        medical_basis="现有资料只有影像文字摘要。",
        changed_from_previous=True,
        remaining_limitation="缺少可比原始影像。",
        evidence_gaps=[{
            "available_information": "已有影像文字摘要。",
            "missing_information": "缺少可比原始HRCT。",
            "why_it_matters": "影响影像模式和进展判断。",
            "decision_unlocked": "提高影像判断确定性。",
            "related_evidence": [],
        }],
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="convert_to_evidence_need",
        rationale="当前有限判断成立，原始影像只用于进一步明确。",
        evidence_gap={
            "available_information": "已有影像文字摘要。",
            "missing_information": "缺少可比原始HRCT。",
            "why_it_matters": "可进一步提高影像判断确定性。",
            "decision_unlocked": "进一步明确影像判断。",
            "related_evidence": [],
        },
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    current_bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    new_gap_ref = next(
        ref
        for ref, item in current_bundle.source_registry.items()
        if item.source_type == "assessment_evidence_need"
        and item.quote == "缺少可比原始HRCT。"
    )
    assert current_bundle.question_refs_to_classify == set()
    assert current_bundle.evidence_need_refs_to_classify == {new_gap_ref}
    assert current_bundle.source_metadata[new_gap_ref]["discussion_issue_id"] == "Q001"
    assert current_bundle.source_metadata[new_gap_ref]["discussion_disposition"] == (
        "convert_to_evidence_need"
    )
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    result = MDTChairIntegration.model_validate(integration_payload(current_bundle))

    _materialize_evidence_need_conversion_refs(
        result, previous, reviews, current_bundle
    )

    assert any(new_gap_ref in need.source_refs for need in result.evidence_needs)

    ledger_value = ledger_payload(current_bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            value = (
                ledger_generation_payload(ledger_value)
                if self.calls == 1
                else integration_generation_payload(current_bundle, ledger_value)
            )
            return LLMResponse(content=json.dumps(value, ensure_ascii=False), raw={})

    resolved, _ = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    ).integrate(
        current_bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )

    assert any(new_gap_ref in need.source_refs for need in resolved.evidence_needs)


def test_partially_answered_question_remains_on_public_board():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    question = result.questions[0]
    assert question.response_status == "all_responded"
    assert question.answer_status == "partially_answered"
    assert question.responded_by == ["thoracic_radiology"]
    assert question.awaiting_specialties == []
    assert question.related_evidence_need_ids == ["EN002"]


def test_reclassified_native_question_becomes_evidence_need_not_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    data_request = source_ref(bundle, "pathology", "native_question")
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    assert all(data_request not in item.source_refs for item in result.questions)
    reclassified = next(
        item for item in result.evidence_needs if data_request in item.source_refs
    )
    assert reclassified.raised_by == ["pathology"]
    assert reclassified.provided_by == []


def test_empty_integrated_conclusions_is_a_valid_current_result():
    result = MDTChairIntegration.model_validate({
        "integrated_conclusions": [],
        "assessment_boundaries": [],
        "conflicts": [],
        "questions": [],
        "evidence_needs": [],
    })
    assert result.integrated_conclusions == []


def test_conflict_ids_specialties_links_and_status_are_program_backfilled():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    payload = integration_payload(bundle)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    question = source_ref(bundle, "pulmonology", "native_question")
    data_request = source_ref(bundle, "pathology", "native_question")
    payload["conflicts"] = [{
        "topic": "同一资料下的直接相反判断",
        "conflict_nature": "direct_contradiction",
        "conflict_domain": "diagnostic_interpretation",
        "comparison_target": "当前资料直接确认命题 X。",
        "comparison_conditions": "同一对象、时间、资料和判断层级。",
        "positions": [
            {"stance": "affirms", "position": "直接肯定。", "source_refs": [pulmonary]},
            {"stance": "denies", "position": "直接否定。", "source_refs": [radiology]},
        ],
        "why_incompatible": "相同前提下不能同时成立。",
        "decision_impact": "阻止当前整合。",
        "resolution_requirement": "澄清既有观点并补足资料。",
        "related_question_source_refs": [question],
        "related_evidence_need_source_refs": [data_request],
    }]
    ledger_payload_value = conflict_ledger_payload(bundle, "direct_contradiction", [
        {
            "source_ref": pulmonary,
            "statement": "直接肯定命题 X。",
            "subject": "命题 X",
            "dimension": "诊断判断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "affirms",
        },
        {
            "source_ref": radiology,
            "statement": "直接否定命题 X。",
            "subject": "命题 X",
            "dimension": "诊断判断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "denies",
        },
    ])
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload_value), bundle
    )
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, ledger
    )
    conflict = result.conflicts[0]
    assert conflict.conflict_id == "CF001"
    assert conflict.specialties == ["pulmonology", "thoracic_radiology"]
    assert conflict.related_question_ids == ["Q001"]
    assert conflict.related_evidence_need_ids == ["EN002"]
    assert conflict.status == "pending_clarification_and_evidence"


def test_initial_ledger_accepts_decision_relevant_discordance():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "decision_relevant_discordance", [
        {
            "source_ref": pulmonary,
            "statement": "当前首选模式 A。",
            "subject": "当前主要形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "possible",
        },
        {
            "source_ref": radiology,
            "statement": "当前首选模式 B。",
            "subject": "当前主要形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "possible",
        },
    ])

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert ledger.claim_groups[0].conflict_nature == "decision_relevant_discordance"


def test_initial_integration_exposes_decision_relevant_discordance():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    ledger_payload_value = conflict_ledger_payload(
        bundle,
        "decision_relevant_discordance",
        [
            {
                "source_ref": ref,
                "statement": statement,
                "subject": "当前主要形态解释",
                "dimension": "形态模式",
                "timeframe": "当前",
                "evidence_scope": "现有资料",
                "professional_level": "morphologic_pattern",
                "position_role": "preferred",
                "epistemic_status": "possible",
            }
            for ref, statement in (
                (pulmonary, "当前首选模式 A。"),
                (radiology, "当前首选模式 B。"),
            )
        ],
    )
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload_value), bundle
    )
    payload = integration_payload(bundle)
    payload["conflicts"] = [{
        "topic": "主要形态解释不同",
        "conflict_nature": "decision_relevant_discordance",
        "conflict_domain": "morphologic_interpretation",
        "comparison_target": "当前主要形态解释",
        "comparison_conditions": "当前时点和现有资料。",
        "positions": [
            {"stance": "favors", "position": "首选模式 A。", "source_refs": [pulmonary]},
            {"stance": "favors", "position": "首选模式 B。", "source_refs": [radiology]},
        ],
        "why_incompatible": "不能同时作为当前首选形态解释。",
        "decision_impact": "影响诊断信度和取材策略。",
        "resolution_requirement": "由 MDT 比较影像与临床依据。",
        "related_question_source_refs": [],
        "related_evidence_need_source_refs": [],
    }]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, ledger
    )

    assert result.conflicts[0].conflict_nature == "decision_relevant_discordance"
    assert {item.stance for item in result.conflicts[0].positions} == {"favors"}


def test_initial_ledger_rejects_tentative_alternatives_as_discordance():
    values = assessable_conflict_outputs()
    values["thoracic_radiology"]["professional_conclusions"]["conclusions"][0][
        "status"
    ] = "possible"
    bundle = build_chair_prompt_bundle("case-1", values)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "decision_relevant_discordance", [
        {
            "source_ref": pulmonary,
            "statement": "模式 A 可能。",
            "subject": "形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "tentative",
            "epistemic_status": "possible",
        },
        {
            "source_ref": radiology,
            "statement": "模式 B 可能。",
            "subject": "形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "tentative",
            "epistemic_status": "possible",
        },
    ])

    with pytest.raises(ValueError, match="preferred primary specialty assessments"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_initial_ledger_rejects_direct_contradiction_across_levels():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "direct_contradiction", [
        {
            "source_ref": pulmonary,
            "statement": "肯定疾病诊断 X。",
            "subject": "疾病 X",
            "dimension": "疾病诊断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "affirms",
        },
        {
            "source_ref": radiology,
            "statement": "否定形态模式 X。",
            "subject": "模式 X",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "denies",
        },
    ])

    with pytest.raises(ValueError, match="one professional level"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_complete_boundary_answer_is_removed_from_public_question_board():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["answer_status"] = "boundary_answered"
    payload["questions"][0].pop("resolution_status", None)

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )
    assert result.questions == []


def test_accepted_boundary_must_be_materialized_with_original_question_source():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    question = previous.questions[0]
    question.raised_by = ["pulmonology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id=question.question_id,
        answer_id="answer-1",
        reviewer_specialty="pulmonology",
        outcome="accept_boundary",
        rationale="缺少关键证据，当前只能形成判断边界。",
    )
    result = previous.model_copy(deep=True)

    with pytest.raises(ValueError, match="must appear in assessment_boundaries"):
        _validate_review_destinations(result, previous, [review], bundle)

    result.assessment_boundaries[0].source_refs.extend(question.source_refs)
    _validate_review_destinations(result, previous, [review], bundle)


def test_unknown_source_id_is_rejected_without_medical_semantic_validator():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    payload["claim_groups"][0]["claims"][0]["source_ref"] = "S999"
    with pytest.raises(ValueError, match="unknown specialty source refs"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_integration_source_type_error_identifies_exact_field():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["source_refs"] = [
        source_ref(bundle, "pulmonology", "native_conclusion")
    ]

    with pytest.raises(ValueError, match=r"questions\[0\]\.source_refs"):
        resolve_chair_references(MDTChairIntegration.model_validate(payload), bundle)


def test_mixed_specialty_answer_refs_are_repaired_from_question_ledger():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload["questions"][0]["answers"][0]["source_refs"] = [
        pulmonary,
        rheumatology,
    ]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )

    assert result.questions[0].answers[0].source_refs == [radiology]
    assert result.questions[0].answers[0].specialty == "thoracic_radiology"
    assert [event["action"] for event in bundle.normalization_events[-2:]] == [
        "dropped_invalid_question_answer_source_refs",
        "restored_question_answer_source_refs_from_ledger",
    ]


def test_non_target_answer_without_ledger_becomes_unanswered():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["answers"][0]["source_refs"] = [
        source_ref(bundle, "pulmonology", "specialty_assessment")
    ]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle
    )

    assert result.questions[0].answers == []
    assert result.questions[0].answer_status == "unanswered"
    assert result.questions[0].response_status == "none_responded"


def test_semantic_ledger_drops_known_gap_refs_from_answer_and_coverage_links():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    gap_ref = source_ref(bundle, "pulmonology", "evidence_gap")
    payload["question_routes"][0]["answer_links"][0]["source_refs"] = [gap_ref]
    payload["evidence_need_groups"][0]["coverage_source_refs"] = [gap_ref]

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert ledger.question_routes[0].answer_links == []
    assert ledger.evidence_need_groups[0].coverage_source_refs == []
    assert bundle.normalization_events == [
        {
            "context": "question_routes[0].answer_links[0].source_refs",
            "action": "dropped_incompatible_known_source_refs",
                "allowed_source_types": [
                    "discussion_answer",
                    "specialty_assessment",
                ],
            "dropped": [
                {"source_ref": gap_ref, "source_type": "assessment_evidence_need"}
            ],
        },
        {
            "context": "evidence_need_groups[0].coverage_source_refs",
            "action": "dropped_incompatible_known_source_refs",
            "allowed_source_types": ["specialty_assessment"],
            "dropped": [
                {"source_ref": gap_ref, "source_type": "assessment_evidence_need"}
            ],
        },
    ]


def test_non_target_specialty_assessment_cannot_answer_a_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    pulmonary_assessment = source_ref(
        bundle, "pulmonology", "specialty_assessment"
    )
    payload["question_routes"][0]["answer_links"][0]["source_refs"] = [
        pulmonary_assessment
    ]

    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(payload), bundle
    )

    assert ledger.question_routes[0].target_specialties == ["thoracic_radiology"]
    assert ledger.question_routes[0].answer_links == []
    assert bundle.normalization_events[-1] == {
        "context": "question_routes[0].answer_links[0].source_refs",
        "action": "dropped_non_target_specialty_answers",
        "target_specialties": ["thoracic_radiology"],
        "dropped": [pulmonary_assessment],
    }


def test_semantic_graph_catalog_repairs_specialty_locator():
    propositions = {
        "segments": [{
            "segment_id": "seg_001",
            "units": [{
                "graph_unit_id": "seg_001_gu_001",
                "evidence_blocks": [{
                    "evidence_id": "seg_001_gu_001_ev_001", "text": "规范原文。"
                }],
                "propositions": [{
                    "proposition_id": "prop_001",
                    "evidence": {
                        "evidence_ids": ["seg_001_gu_001_ev_001"],
                        "quote": "规范命题原文。",
                    },
                }],
            }],
        }]
    }
    graphs = {"segments": [{"units": [{
        "graph_unit_id": "seg_001_gu_001", "segment_id": "seg_001", "nodes": []
    }]}]}
    catalog = build_semantic_evidence_catalog(propositions, graphs)
    source = outputs()
    source["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"]["supporting"][0].update(
        {"segment_id": "wrong", "quote": "wrong", "node_ids": []}
    )
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = next(iter(bundle.evidence_registry.values()))
    assert evidence.segment_id == "seg_001"
    assert evidence.quote == "规范原文。"
    assert evidence.evidence_ref == "seg_001_gu_001_ev_001"
    assert "E001" not in bundle.evidence_registry

    source_with_proposition = outputs()
    source_with_proposition["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"]["supporting"][0]["proposition_ids"] = ["prop_001"]
    proposition_bundle = build_chair_prompt_bundle(
        "case-1",
        source_with_proposition,
        semantic_evidence=catalog,
    )
    proposition_evidence = next(iter(proposition_bundle.evidence_registry.values()))
    assert proposition_evidence.evidence_ref == "seg_001_gu_001::prop_001"
    assert proposition_evidence.proposition_ids == ["seg_001_gu_001::prop_001"]


def test_agent_uses_ledger_then_integration_structured_calls():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    source_claim_id = "pulm_assess_001_c001"
    bundle.prompt_input["specialties"][0]["specialty_assessments"][0]["claims"] = [
        {"claim_id": source_claim_id, "statement": "专科内部原子判断。"}
    ]

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            payload = (
                ledger_generation_payload(ledger_payload(bundle))
                if len(self.calls) % 2
                else integration_generation_payload(bundle, ledger_payload(bundle))
            )
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            )

    llm = FakeLLM()
    agent = MDTChairAgent(
        llm,
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.integrate(bundle)
    assert len(llm.calls) == 2
    assert result.integrated_conclusions[0].conclusion_id == "IC001"
    assert trace["semantic_ledger"]["claim_groups"][0]["topic_id"] == "T001"
    assert "topic_ledger_chars" in trace["prompt_components"]
    assert source_claim_id in llm.calls[0][0][-1].content
    assert source_claim_id not in llm.calls[1][0][-1].content
    ledger_schema = llm.calls[0][1]["response_format"]["json_schema"]["schema"]
    integration_schema = llm.calls[1][1]["response_format"]["json_schema"]["schema"]
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    pulmonary_claim_schema = next(
        choice
        for choice in ledger_schema["$defs"]["IntegratedLedgerAtomicClaim"]["anyOf"]
        if choice["properties"]["source_ref"]["enum"] == [pulmonary]
    )
    allowed_pulmonary_evidence = sorted(
        evidence_ref
        for values in bundle.source_evidence[pulmonary].values()
        for evidence_ref in values
    )
    assert all(
        evidence_schema["properties"]["evidence_ref"]["enum"]
        == allowed_pulmonary_evidence
        for evidence_schema in pulmonary_claim_schema["properties"]["evidence_links"][
            "items"
        ]["anyOf"]
    )
    assert ledger_schema["$defs"]["BoundaryLedgerAtomicClaim"]["anyOf"][0][
        "properties"
    ]["epistemic_status"]["enum"] == [
        "indeterminate", "not_assessable", "not_applicable"
    ]
    assert ledger_schema["$defs"]["DiscriminatingLedgerEvidenceLink"][
        "properties"
    ]["comparison_target"]["minLength"] == 1
    route_schema = ledger_schema["$defs"]["ChairQuestionRoutes"]
    assert route_schema["required"] == [
        source_ref(bundle, "pulmonology", "native_question"),
        source_ref(bundle, "pathology", "native_question"),
    ]
    assert set(route_schema["properties"]) == set(route_schema["required"])
    assert route_schema["additionalProperties"] is False
    assert "source_refs" not in ledger_schema["$defs"]["LedgerQuestionRouteSlot"]["properties"]
    assert integration_schema["$defs"]["IntegratedQuestion"]["properties"][
        "source_refs"
    ]["items"]["enum"] == [
        source_ref(bundle, "pulmonology", "native_question"),
    ]
    assert integration_schema["$defs"]["QuestionAnswer"]["properties"][
        "source_refs"
    ]["items"]["enum"] == [
        source_ref(bundle, "thoracic_radiology", "native_conclusion")
    ]
    for name in (
        "IntegratedConclusionDraft",
        "AssessmentBoundaryDraft",
        "ConflictPositionDraft",
    ):
        assert "source_refs" not in integration_schema["$defs"][name]["properties"]
    assert integration_schema["$defs"]["AssessmentBoundaryDraft"]["properties"][
        "atomic_claim_ids"
    ]["items"]["enum"] == ["T002-A001", "T002-A002", "T002-A003"]

    alias_result, _ = agent.synthesize(bundle)
    assert len(llm.calls) == 4
    assert alias_result.questions[0].response_status == "all_responded"
