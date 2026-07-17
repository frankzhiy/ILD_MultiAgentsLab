import json

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.pathology.agent import PathologyAgent
from src.agents.pathology.models import (
    ChairQuestion,
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    DomainChange,
    EvidencePointer,
    HistopathologicPattern,
    HistopathologicPatternAssessment,
    InitialConsultFormulation,
    InitialMorphologicAssessment,
    InitialSpecimenReconstruction,
    MappedSpecialistFinding,
    MorphologicFeature,
    PathologyDiscussionInput,
    PathologyDiscussionState,
    PathologyDomain,
    PathologyFormulation,
    PathologyInitialAssessment,
    SourceAssessment,
    SpecimenRecord,
    SpecialistClaim,
    SpecialistOpinion,
)
from src.agents.pathology.validation import (
    validate_evidence_map,
    validate_initial_assessment,
    validate_initial_stage,
    validate_specialist_opinions,
)
from src.llm.base import LLMResponse
from src.reporting.pathology_report import render_pathology_report
from src.schemas.semantic_graphing.clinical_proposition import (
    ClinicalProposition,
    DocumentClinicalPropositions,
    EvidenceBlock,
    EvidenceReference,
    GraphUnitClinicalPropositions,
    PropositionType,
    SegmentClinicalPropositions,
)
from src.schemas.semantic_graphing.document import (
    ClassifiedSegment,
    DocumentClassification,
    DiscourseUnitType,
    SourceType,
)
from src.schemas.semantic_graphing.graph_unit import (
    DocumentGraphUnits,
    GraphUnit,
    MdtSpecialty,
    SegmentGraphUnits,
)
from src.schemas.semantic_graphing.local_graph import (
    DocumentLocalGraphs,
    GraphUnitLocalGraph,
    LocalGraphBuildStatus,
    LocalGraphSummary,
    SegmentLocalGraphs,
)
from src.schemas.semantic_graphing.primary_frame import (
    DocumentPrimaryFrames,
    GraphUnitPrimaryFrame,
    PrimaryFrame,
    SegmentPrimaryFrames,
)
from src.schemas.semantic_graphing.proposition_validation import (
    DocumentPropositionValidation,
    GraphUnitPropositionValidation,
    PropositionValidationSummary,
    PropositionValidationMetrics,
    SegmentPropositionValidation,
)
from src.schemas.specialty_agent_input import (
    EvidenceRole,
    SpecialtyCaseInput,
    SpecialtyCaseSummary,
    SpecialtySegmentInput,
    SpecialtyUnitInput,
)


PROMPT_DIR = "src/prompts/pathology"
TEXT = "右下叶外科肺活检病理报告：胸膜下斑片状纤维化伴成纤维细胞灶，符合UIP模式。"
EVIDENCE_ID = "seg_001_gu_001_ev_001"


class FakeLLM:
    supports_json_schema = False

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        return LLMResponse(
            content=json.dumps(self.responses.pop(0), ensure_ascii=False),
            raw={},
        )


def case_input(*, pathology_owned: bool = True) -> SpecialtyCaseInput:
    specialties = [MdtSpecialty.PATHOLOGY] if pathology_owned else [MdtSpecialty.PULMONOLOGY]
    role = EvidenceRole.OWNED if pathology_owned else EvidenceRole.REFERENCE_ONLY
    may_support = pathology_owned
    segment = ClassifiedSegment(
        segment_id="seg_001",
        text=TEXT,
        unit_type=DiscourseUnitType.STANDALONE_PATHOLOGY_REPORT,
        contained_source_types=[SourceType.PATHOLOGY_FINDINGS],
        clinical_frame="pathology_report",
        start_char=0,
        end_char=len(TEXT),
        confidence=1.0,
        rationale="独立病理报告。",
    )
    graph_unit = GraphUnit(
        graph_unit_id="seg_001_gu_001",
        segment_id="seg_001",
        text=TEXT,
        source_type=SourceType.PATHOLOGY_FINDINGS,
        mdt_specialty=specialties,
        primary_frame=PrimaryFrame.STANDALONE_EXAMINATION,
        status="present",
        certainty="high",
        start_char=0,
        end_char=len(TEXT),
        segment_start_char=0,
        segment_end_char=len(TEXT),
        rationale="病理报告事件核。",
    )
    block = EvidenceBlock(evidence_id=EVIDENCE_ID, text=TEXT)
    reference = EvidenceReference(evidence_ids=[EVIDENCE_ID], quote=TEXT)
    propositions = GraphUnitClinicalPropositions(
        graph_unit_id=graph_unit.graph_unit_id,
        primary_frame=PrimaryFrame.STANDALONE_EXAMINATION,
        evidence_blocks=[block],
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.FINDING,
                concept_text="病理报告符合UIP模式",
                status="present",
                certainty="high",
                evidence=reference,
                rationale="报告明确陈述。",
            )
        ],
    )
    validation = GraphUnitPropositionValidation(
        graph_unit_id=graph_unit.graph_unit_id,
        is_graph_ready=True,
        metrics=PropositionValidationMetrics(
            proposition_count=1,
            proposition_modifier_count=0,
            attributed_proposition_count=0,
            evidence_block_count=1,
            referenced_evidence_block_count=1,
            evidence_block_coverage=1.0,
        ),
    )
    unit = SpecialtyUnitInput(
        segment_index=1,
        unit_index=1,
        evidence_role=role,
        may_support_diagnostic_claim=may_support,
        allowed_uses=(
            ["diagnostic_support", "clinical_interpretation", "specialist_question"]
            if may_support
            else ["case_orientation", "related_evidence", "specialist_question"]
        ),
        locator_status="available",
        graph_unit=graph_unit,
        primary_frame=GraphUnitPrimaryFrame(
            graph_unit_id=graph_unit.graph_unit_id,
            primary_frame=PrimaryFrame.STANDALONE_EXAMINATION,
            rationale="独立检查。",
        ),
        clinical_propositions=propositions,
        proposition_validation=validation,
        local_graph=GraphUnitLocalGraph(
            graph_unit_id=graph_unit.graph_unit_id,
            segment_id=segment.segment_id,
            primary_frame=PrimaryFrame.STANDALONE_EXAMINATION,
            build_status=LocalGraphBuildStatus.BUILT,
            evidence_blocks=[block],
        ),
    )
    return SpecialtyCaseInput(
        case_id="pathology-case",
        target_specialty=MdtSpecialty.PATHOLOGY,
        source_run_dir="/tmp/pathology-case",
        segments=[SpecialtySegmentInput(segment_index=1, segment=segment, units=[unit])],
        summary=SpecialtyCaseSummary(
            segment_count=1,
            unit_count=1,
            owned_unit_count=1 if pathology_owned else 0,
            shared_context_unit_count=0,
            reference_only_unit_count=0 if pathology_owned else 1,
            available_locator_count=1,
            degraded_locator_count=0,
        ),
    )


def pointer():
    return EvidencePointer(evidence_ids=[EVIDENCE_ID])


def review(domain, status="assessed"):
    return {"domain": domain, "status": status, "rationale": "已按病理规则复核。"}


def initial_stages():
    evidence = pointer()
    reconstruction = InitialSpecimenReconstruction(
        domain_reviews=[
            review(PathologyDomain.SOURCE_AND_MATERIAL),
            review(PathologyDomain.SPECIMEN_AND_SAMPLING),
        ],
        source_assessment=SourceAssessment(
            assessment="当前输入包含正式病理报告。",
            confidence="high",
            reasoning_summary="来源由报告标题和内容明确。",
            supporting_evidence=[evidence],
            material_status="pathology_report_only",
            review_basis="formal_pathology_report",
        ),
        specimens=[
            SpecimenRecord(
                specimen_id="specimen_001",
                procedure="surgical_lung_biopsy",
                site="右下叶",
                description="外科肺活检标本；当前只提供文字报告。",
                source_authority="formal_pathology_report",
                adequacy="adequate",
                representativeness="possibly_representative",
                supporting_evidence=[evidence],
            )
        ],
    )
    morphology = InitialMorphologicAssessment(
        domain_reviews=[
            review(PathologyDomain.TISSUE_ARCHITECTURE),
            review(PathologyDomain.PRIMARY_PATTERN),
            review(PathologyDomain.COEXISTING_AND_ACUTE, "partially_assessable"),
            review(PathologyDomain.ETIOLOGIC_CLUES, "not_assessable"),
            review(PathologyDomain.ANCILLARY_STUDIES, "not_assessable"),
        ],
        morphologic_features=[
            MorphologicFeature(
                assessment="报告描述胸膜下斑片状纤维化及成纤维细胞灶。",
                confidence="high",
                reasoning_summary="仅复述正式报告明确描述的形态。",
                supporting_evidence=[evidence],
                compartment="pleural_subpleural",
                feature="胸膜下斑片状纤维化伴成纤维细胞灶",
                status="present",
                diagnostic_significance="支持 UIP 模式。",
            )
        ],
        pattern_assessments=[
            HistopathologicPatternAssessment(
                assessment="正式报告支持 UIP 模式。",
                confidence="high",
                reasoning_summary="依据正式报告而非独立阅片。",
                supporting_evidence=[evidence],
                pattern=HistopathologicPattern.UIP,
                role="dominant",
                status="supported",
                fibrotic_status="fibrotic",
                ipf_histopathology_category="not_assessable",
            )
        ],
    )
    formulation = InitialConsultFormulation(
        domain_reviews=[
            review(PathologyDomain.PATHOLOGY_FORMULATION),
            review(PathologyDomain.SPECIALIST_INTEGRATION, "deferred_to_specialist"),
            review(PathologyDomain.DECISION_RELEVANT_GAPS, "partially_assessable"),
        ],
        pathology_formulation=PathologyFormulation(
            classification_status="pattern_supported",
            primary_pattern=HistopathologicPattern.UIP,
            formulation="正式病理报告支持 UIP 模式；病因需 MDT 整合。",
            confidence="high",
            reasoning_summary="UIP 是形态模式，不能单独等同 IPF。",
            supporting_evidence=[evidence],
        ),
    )
    return reconstruction, morphology, formulation


def agent(responses):
    return PathologyAgent(
        FakeLLM(responses),
        initial_specimen_reconstruction_prompt_path=f"{PROMPT_DIR}/initial_specimen_reconstruction.md",
        initial_morphologic_assessment_prompt_path=f"{PROMPT_DIR}/initial_morphologic_assessment.md",
        initial_consult_formulation_prompt_path=f"{PROMPT_DIR}/initial_consult_formulation.md",
        discussion_evidence_mapping_prompt_path=f"{PROMPT_DIR}/discussion_evidence_mapping.md",
        discussion_state_update_prompt_path=f"{PROMPT_DIR}/discussion_state_update.md",
        discussion_consult_response_prompt_path=f"{PROMPT_DIR}/discussion_consult_response.md",
        clinical_rules={},
        temperature=0,
        max_tokens=4000,
    )


def payload(value):
    return value.model_dump(mode="json")


def test_shared_input_builder_already_supports_pathology_target(tmp_path):
    case = case_input()
    segment = case.segments[0]
    unit = segment.units[0]
    documents = {
        "discourse_segments": DocumentClassification(segments=[segment.segment]),
        "graph_units": DocumentGraphUnits(
            segments=[
                SegmentGraphUnits(
                    segment_id=segment.segment.segment_id,
                    graph_units=[unit.graph_unit],
                )
            ]
        ),
        "primary_frames": DocumentPrimaryFrames(
            segments=[
                SegmentPrimaryFrames(
                    segment_id=segment.segment.segment_id,
                    units=[unit.primary_frame],
                )
            ]
        ),
        "clinical_propositions": DocumentClinicalPropositions(
            segments=[
                SegmentClinicalPropositions(
                    segment_id=segment.segment.segment_id,
                    units=[unit.clinical_propositions],
                )
            ]
        ),
        "proposition_validation": DocumentPropositionValidation(
            is_graph_ready=True,
            summary=PropositionValidationSummary(
                segment_count=1,
                unit_count=1,
                graph_ready_unit_count=1,
                error_count=0,
                warning_count=0,
                info_count=0,
            ),
            segments=[
                SegmentPropositionValidation(
                    segment_id=segment.segment.segment_id,
                    units=[unit.proposition_validation],
                )
            ],
        ),
        "local_graphs": DocumentLocalGraphs(
            summary=LocalGraphSummary(
                segment_count=1,
                unit_count=1,
                built_graph_count=1,
                blocked_graph_count=0,
                node_count=0,
                edge_count=0,
            ),
            segments=[
                SegmentLocalGraphs(
                    segment_id=segment.segment.segment_id,
                    units=[unit.local_graph],
                )
            ],
        ),
    }
    for suffix, document in documents.items():
        (tmp_path / f"{case.case_id}_{suffix}.json").write_text(
            document.model_dump_json(), encoding="utf-8"
        )

    rebuilt = build_specialty_case_input(tmp_path, MdtSpecialty.PATHOLOGY)

    assert rebuilt.target_specialty == MdtSpecialty.PATHOLOGY
    assert rebuilt.summary.owned_unit_count == 1
    assert rebuilt.segments[0].units[0].evidence_role == EvidenceRole.OWNED


def test_initial_assessment_aggregates_three_stages_and_renders(tmp_path):
    case = case_input()
    stages = initial_stages()
    result, trace = agent([payload(item) for item in stages]).initial_assessment(case)

    assert result.pathology_formulation.primary_pattern == HistopathologicPattern.UIP
    assert result.source_assessment.direct_slides_reviewed is False
    assert len(result.domain_reviews) == len(PathologyDomain)
    assert [item["stage"] for item in trace["stages"]] == [
        "initial_specimen_reconstruction",
        "initial_morphologic_assessment",
        "initial_consult_formulation",
    ]
    report = render_pathology_report(result, case, tmp_path / "report.html")
    html = report.read_text(encoding="utf-8")
    resolved = result.pathology_formulation.supporting_evidence[0]
    assert "病理科首轮评估" in html
    assert "片段 ·" in html and resolved.segment_id in html
    assert "证据 ·" in html and resolved.evidence_ids[0] in html
    assert "节点 ·" in html
    expected_node = resolved.node_ids[0] if resolved.node_ids else "节点 · 无"
    assert expected_node in html
    assert resolved.quote in html


def test_no_material_state_cannot_contain_pattern_findings():
    case = case_input()
    state = PathologyInitialAssessment(
        domain_reviews=[review(domain, "not_assessable") for domain in PathologyDomain],
        source_assessment=SourceAssessment(
            assessment="当前输入未提供可评价病理材料。",
            confidence="high",
            reasoning_summary="这是输入可评价性说明，不表示患者未活检。",
            material_status="no_pathology_material",
            review_basis="no_material",
        ),
        pathology_formulation=PathologyFormulation(
            classification_status="no_pathology_material",
            formulation="当前无病理材料可形成组织学模式。",
            confidence="unknown",
            reasoning_summary="不能从缺失资料推断阴性或模式。",
        ),
        pattern_assessments=[
            initial_stages()[1].pattern_assessments[0]
        ],
    )
    with pytest.raises(ValueError, match="No pathology material"):
        validate_initial_assessment(state, case)


def test_initial_assessment_without_pathology_material_does_not_invent_pattern():
    case = case_input(pathology_owned=False)
    reconstruction = InitialSpecimenReconstruction(
        domain_reviews=[
            review(PathologyDomain.SOURCE_AND_MATERIAL, "not_assessable"),
            review(PathologyDomain.SPECIMEN_AND_SAMPLING, "not_assessable"),
        ],
        source_assessment=SourceAssessment(
            assessment="当前输入未提供可评价病理材料。",
            confidence="high",
            reasoning_summary="不把未提供改写为未做活检。",
            material_status="no_pathology_material",
            review_basis="no_material",
        ),
    )
    morphology = InitialMorphologicAssessment(
        domain_reviews=[
            review(domain, "not_assessable")
            for domain in (
                PathologyDomain.TISSUE_ARCHITECTURE,
                PathologyDomain.PRIMARY_PATTERN,
                PathologyDomain.COEXISTING_AND_ACUTE,
                PathologyDomain.ETIOLOGIC_CLUES,
                PathologyDomain.ANCILLARY_STUDIES,
            )
        ]
    )
    formulation = InitialConsultFormulation(
        domain_reviews=[
            review(PathologyDomain.PATHOLOGY_FORMULATION, "not_assessable"),
            review(PathologyDomain.SPECIALIST_INTEGRATION, "not_assessable"),
            review(PathologyDomain.DECISION_RELEVANT_GAPS, "not_assessable"),
        ],
        pathology_formulation=PathologyFormulation(
            classification_status="no_pathology_material",
            formulation="当前无病理材料可形成组织学模式。",
            confidence="unknown",
            reasoning_summary="保留不可评价状态。",
        ),
    )
    result, _ = agent(
        [payload(reconstruction), payload(morphology), payload(formulation)]
    ).initial_assessment(case)

    assert result.specimens == []
    assert result.pattern_assessments == []
    assert result.pathology_formulation.primary_pattern is None


def test_limited_specimen_rejects_high_confidence_supported_pattern():
    case = case_input()
    reconstruction, morphology, formulation = initial_stages()
    reconstruction.specimens[0].adequacy = "limited"
    reconstruction.specimens[0].representativeness = "not_assessable"
    state = PathologyInitialAssessment(
        domain_reviews=[
            *reconstruction.domain_reviews,
            *morphology.domain_reviews,
            *formulation.domain_reviews,
        ],
        source_assessment=reconstruction.source_assessment,
        specimens=reconstruction.specimens,
        morphologic_features=morphology.morphologic_features,
        pattern_assessments=morphology.pattern_assessments,
        pathology_formulation=formulation.pathology_formulation,
    )
    with pytest.raises(ValueError, match="High-confidence pathology pattern"):
        validate_initial_assessment(state, case)


def test_reference_only_evidence_cannot_support_initial_pathology_claim():
    case = case_input(pathology_owned=False)
    stage = initial_stages()[0]
    with pytest.raises(ValueError, match="不能直接支持病理科诊断性判断"):
        validate_initial_stage(stage, case)


def test_discussion_authorizes_reference_evidence_by_exact_specialist_claim():
    case = case_input(pathology_owned=False)
    initial = PathologyInitialAssessment(
        domain_reviews=[review(domain, "not_assessable") for domain in PathologyDomain],
        source_assessment=SourceAssessment(
            assessment="当前输入未提供可评价病理材料。",
            confidence="high",
            reasoning_summary="输入可评价性说明。",
            material_status="no_pathology_material",
            review_basis="no_material",
        ),
        pathology_formulation=PathologyFormulation(
            classification_status="no_pathology_material",
            formulation="当前无病理材料可形成组织学模式。",
            confidence="unknown",
            reasoning_summary="保留不可评价状态。",
        ),
    )
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.PULMONOLOGY,
        opinion_id="pulm-001",
        summary="呼吸科提供需病理关注的正式意见。",
        claims=[SpecialistClaim(claim="需核对既往病理报告", evidence=[pointer()])],
        confidence="moderate",
    )
    discussion = PathologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
    )
    validate_specialist_opinions(discussion)
    evidence_map = DiscussionEvidenceMap(
        specialist_opinions_used=[opinion.opinion_id],
        mapped_findings=[
            MappedSpecialistFinding(
                opinion_id=opinion.opinion_id,
                relationship="supplementary",
                affected_domains=[PathologyDomain.SPECIALIST_INTEGRATION],
                pathology_effect="正式意见授权该上下文进入病理会中整合。",
                evidence=[pointer()],
            )
        ],
    )

    assert validate_evidence_map(evidence_map, discussion).mapped_findings


def test_discussion_updates_same_state_and_answers_chair_question():
    case = case_input()
    stages = initial_stages()
    initial, _ = agent([payload(item) for item in stages]).initial_assessment(case)
    updated = PathologyDiscussionState.model_validate(
        {
            **initial.model_dump(mode="python"),
            "phase": "discussion_update",
            "domain_reviews": [
                review(domain, "reviewed_unchanged") for domain in PathologyDomain
            ],
        }
    )
    changes = [
        DomainChange(
            domain=domain,
            change_status="reviewed_unchanged",
            initial_view="首轮状态",
            updated_view="复核后不变",
            reason="没有足以改变病理状态的新证据。",
        )
        for domain in PathologyDomain
    ]
    evidence_map = DiscussionEvidenceMap()
    update = DiscussionStateUpdate(updated_state=updated, domain_changes=changes)
    consult = DiscussionConsultOutput(
        chair_answers=[
            {
                "question_id": "q1",
                "answerability": "answered",
                "answer": "现有正式报告支持 UIP 模式，但不能单独诊断 IPF。",
                "confidence": "high",
                "reasoning_summary": "回答沿用更新后的病理状态。",
            }
        ]
    )
    result, trace = agent([payload(evidence_map), payload(update), payload(consult)]).discussion_response(
        PathologyDiscussionInput(
            case_input=case,
            initial_assessment=initial,
            chair_questions=[ChairQuestion(question_id="q1", question="是否为IPF？")],
        )
    )
    assert result.chair_answers[0].question_id == "q1"
    assert result.updated_state.phase == "discussion_update"
    assert [item["stage"] for item in trace["stages"]] == [
        "discussion_evidence_mapping",
        "discussion_state_update",
        "discussion_consult_response",
    ]
