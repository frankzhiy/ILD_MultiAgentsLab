from copy import deepcopy

import pytest

from src.agents.common.decision_state import (
    JudgmentChangeProposal,
    JudgmentContent,
    SpecialtyJudgmentUpdate,
    active_judgments,
    apply_judgment_update,
    initialize_decision_state,
    project_active_specialty_outputs,
)


def assessment(assessment_id="pulm_001"):
    return {
        "assessment_id": assessment_id,
        "role": "primary",
        "assessment_type": "working_diagnosis",
        "statement": "现有资料支持纤维化性间质性肺病。",
        "status": "favored",
        "assessability": "partially_assessable", "direction": "supports",
        "confidence": "moderate", "clinical_role": "primary",
        "medical_basis": "病例记录存在纤维化相关描述。",
        "decision_impact": "进入病因和模式层面的进一步整合。",
        "claims": [{"statement": "存在纤维化性间质性肺病。"}],
        "evidence": {
            "evidence_relations": [{
                "evidence_ids": ["ev_001"],
                "direction": "supports",
                "function": "foundational",
            }],
        },
        "guideline_evidence": [],
        "limitations": ["仅依据病例文字资料。"],
        "conditions": {
            "subject": "纤维化性间质性肺病",
            "professional_level": "disease_diagnosis",
            "timeframe": {
                "kind": "current",
                "start": "",
                "end": "",
                "description": "当前病例评估时点。",
            },
            "evidence_scope": {
                "evidence_ids": ["ev_001"],
                "source_types": ["case_evidence"],
                "scope_limitations": ["仅依据病例文字资料。"],
            },
            "applicability_conditions": ["仅依据病例文字资料。"],
        },
    }


def outputs():
    result = {}
    for specialty in (
        "pulmonology",
        "thoracic_radiology",
        "rheumatology",
        "pathology",
    ):
        item = assessment(f"{specialty}_001")
        result[specialty] = {
            "specialty_assessments": {
                "specialty_question": "本专科当前判断是什么？",
                "assessability": "assessable",
                "assessments": [item],
                "evidence_gaps": [],
                "boundaries": ["不替代其他专科判断。"],
            },
            "interspecialty_questions": {"questions": []},
        }
    return result


def proposal(state, change_type, content=None):
    current = active_judgments(state, "pulmonology")[0]
    return JudgmentChangeProposal(
        change_type=change_type,
        target_judgment_id=current.judgment_id,
        base_version_id=current.version_id,
        proposed_content=content,
        rationale="本轮其他专科意见改变了判断边界。",
        trigger_issue_ids=["Q001"],
        considered_source_refs=["R01-Q001-thoracic_radiology-A"],
    )


def update(proposal_value, transaction="R01-pulmonology-judgment-update"):
    return SpecialtyJudgmentUpdate(
        specialty="pulmonology",
        transaction_id=transaction,
        proposals=[proposal_value],
    )


def test_initial_state_has_one_active_version_and_projects_only_active_content():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]

    assert current.version_id == "pulmonology_001@v001"
    assert current.assessment.claims[0].claim_id == "pulmonology_001@v001_c001"
    assert current.assessment.conditions.evidence_scope.evidence_ids == ["ev_001"]
    projected = project_active_specialty_outputs(state)
    item = projected["pulmonology"]["specialty_assessments"]["assessments"][0]
    assert item["version_id"] == current.version_id
    assert item["judgment_id"] == current.judgment_id


def test_progression_judgment_gets_a_longitudinal_time_condition():
    specialty_outputs = outputs()
    item = specialty_outputs["pulmonology"]["specialty_assessments"]["assessments"][0]
    item["assessment_type"] = "progression"
    item["conditions"]["timeframe"] = {
        "kind": "current",
        "start": "",
        "end": "",
        "description": "基于当前病例所提供的资料。",
    }

    state = initialize_decision_state("case-1", specialty_outputs)
    current = active_judgments(state, "pulmonology")[0]

    assert current.assessment.conditions.professional_level == "severity_or_trajectory"
    assert current.assessment.conditions.timeframe.kind == "longitudinal"
    assert "纵向病程" in current.assessment.conditions.timeframe.description


def test_supplement_creates_a_new_version_and_is_idempotent():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    content.medical_basis += " 影像科意见进一步支持这一判断。"
    change = update(proposal(state, "supplement", content))

    changed = apply_judgment_update(state, change, round_number=1)
    active = active_judgments(changed, "pulmonology")[0]
    history = next(item for item in changed.judgments if item.judgment_id == active.judgment_id)

    assert active.version_id == "pulmonology_001@v002"
    assert active.previous_version_id == "pulmonology_001@v001"
    assert history.versions[0].lifecycle_status == "superseded"
    replayed = apply_judgment_update(changed, change, round_number=1)
    assert replayed.model_dump() == changed.model_dump()


def test_supplement_can_add_evidence_without_changing_judgment_conditions():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    relation = deepcopy(content.evidence.evidence_relations[0])
    relation.evidence_ids = ["ev_002"]
    content.evidence.evidence_relations.append(relation)
    content.medical_basis += " 新增病例证据进一步支持该判断。"

    changed = apply_judgment_update(
        state,
        update(proposal(state, "supplement", content)),
        round_number=1,
    )
    active = active_judgments(changed, "pulmonology")[0]

    assert active.assessment.statement == current.assessment.statement
    assert active.assessment.conditions.subject == current.assessment.conditions.subject
    assert active.assessment.conditions.evidence_scope.evidence_ids == [
        "ev_001",
        "ev_002",
    ]


def test_supplement_preserves_prior_patient_evidence_if_draft_omits_it():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    content.medical_basis += " 新增讨论依据。"
    content.evidence.evidence_relations = []

    changed = apply_judgment_update(
        state,
        update(proposal(state, "supplement", content)),
        round_number=1,
    )
    active = active_judgments(changed, "pulmonology")[0]

    assert active.assessment.evidence.evidence_relations == current.assessment.evidence.evidence_relations
    assert active.assessment.conditions.evidence_scope.evidence_ids == ["ev_001"]


def test_qualify_cannot_silently_strengthen_status():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    content.status = "supported"

    with pytest.raises(ValueError, match="cannot strengthen"):
        apply_judgment_update(
            state,
            update(proposal(state, "qualify", content)),
            round_number=1,
        )


def test_qualify_inherits_prior_limits_when_full_content_omits_them():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    content.status = "possible"
    content.limitations = ["仍需原始影像复核。"]
    content.conditions.applicability_conditions = []
    content.conditions.evidence_scope.scope_limitations = []

    changed = apply_judgment_update(
        state,
        update(proposal(state, "qualify", content)),
        round_number=1,
    )
    active = active_judgments(changed, "pulmonology")[0]

    assert active.assessment.limitations == [
        "仅依据病例文字资料。", "仍需原始影像复核。",
    ]
    assert "仅依据病例文字资料。" in active.assessment.conditions.applicability_conditions
    assert "仅依据病例文字资料。" in active.assessment.conditions.evidence_scope.scope_limitations


def test_withdraw_removes_the_active_version_but_preserves_history():
    state = initialize_decision_state("case-1", outputs())
    change = proposal(state, "withdraw")
    change.considered_source_refs = ["R01-Q001-rheumatology-A"]
    changed = apply_judgment_update(
        state,
        update(change),
        round_number=1,
    )

    assert active_judgments(changed, "pulmonology") == []
    history = next(item for item in changed.judgments if item.specialty == "pulmonology")
    assert history.active_version_id is None
    assert len(history.versions) == 2
    assert history.versions[0].lifecycle_status == "superseded"
    assert history.versions[1].version_id == "pulmonology_001@v002"
    assert history.versions[1].previous_version_id == "pulmonology_001@v001"
    assert history.versions[1].lifecycle_status == "withdrawn"
    assert history.versions[1].considered_source_refs == [
        "R01-Q001-rheumatology-A"
    ]
    assert changed.change_events[-1].after_version_id == "pulmonology_001@v002"
    assert changed.change_events[-1].considered_source_refs == [
        "R01-Q001-rheumatology-A"
    ]


def test_stale_and_cross_specialty_updates_are_rejected():
    state = initialize_decision_state("case-1", outputs())
    stale = proposal(state, "maintain")
    stale.base_version_id = "pulmonology_001@v999"
    with pytest.raises(ValueError, match="Stale base"):
        apply_judgment_update(state, update(stale), round_number=1)

    foreign = JudgmentChangeProposal(
        change_type="maintain",
        target_judgment_id="rheumatology_001",
        base_version_id="rheumatology_001@v001",
        rationale="错误地尝试修改其他专科判断。",
    )
    with pytest.raises(ValueError, match="only its own"):
        apply_judgment_update(state, update(foreign), round_number=1)


def test_state_rejects_an_untraceable_judgment_change():
    state = initialize_decision_state("case-1", outputs())
    change = proposal(state, "withdraw")
    change.considered_source_refs = []

    with pytest.raises(ValueError, match="material it considered"):
        apply_judgment_update(state, update(change), round_number=1)


def test_projected_chair_input_excludes_superseded_version():
    state = initialize_decision_state("case-1", outputs())
    current = active_judgments(state, "pulmonology")[0]
    revised = JudgmentContent.from_assessment(current.assessment)
    revised.statement = "现有资料仅能支持未分类间质性肺病。"
    revised.claims = [deepcopy(revised.claims[0])]
    revised.claims[0].statement = "当前只能形成未分类间质性肺病判断。"
    changed = apply_judgment_update(
        state,
        update(proposal(state, "revise", revised)),
        round_number=1,
    )

    projected = project_active_specialty_outputs(changed)
    items = projected["pulmonology"]["specialty_assessments"]["assessments"]
    assert [item["version_id"] for item in items] == ["pulmonology_001@v002"]
    assert "未分类" in items[0]["statement"]
