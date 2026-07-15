from pathlib import Path

import scripts.run.run_thoracic_radiology_agent as runner
from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.thoracic_radiology.models import (
    CaseOrientation,
    CoreConsultAnswer,
    InitialCaseReconstruction,
    RadiologyTask,
    RadiologyTaskAssessment,
    TaskPlanItem,
    ThoracicRadiologyInitialAssessment,
)
from src.llm.structured import StructuredGenerationError
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_DIR = Path("outputs/runs/20260714_163246_76-IPF_step2_step3")


def minimal_assessment(case):
    task = RadiologyTaskAssessment(
        task=RadiologyTask.SOURCE_RECONCILIATION,
        priority="primary",
        answerability="not_answerable",
        conclusion="没有可核对的胸部影像文字资料。",
        confidence="unknown",
        reasoning_summary="测试输入不可评价。",
        decision_impact="需要补充正式胸部影像报告。",
    )
    return ThoracicRadiologyInitialAssessment(
        case_id=case.case_id,
        reconstruction=InitialCaseReconstruction(
            orientation=CaseOrientation(
                clinical_trigger="ILD MDT 会诊",
                primary_imaging_question="现有文字资料能回答什么？",
            ),
            task_plan=[
                TaskPlanItem(
                    task=RadiologyTask.SOURCE_RECONCILIATION,
                    priority="primary",
                    activation="active",
                    rationale="先确认资料是否可用。",
                )
            ],
            limitations=["未提供可评价的胸部影像文字描述。"],
        ),
        task_assessments=[task],
        core_answer=CoreConsultAnswer(
            primary_question="现有文字资料能回答什么？",
            answer="当前不能形成可靠影像结论。",
            confidence="unknown",
            decision_impact="补充资料后再评估。",
        ),
    )


def test_failure_trace_uses_radiology_schema(tmp_path):
    error = StructuredGenerationError(
        "validation failed",
        attempts=[{"attempt": 1, "content": "raw invalid response"}],
        stage="initial_morphologic_assessment",
    )

    path = runner.write_failure_trace(tmp_path, "case_thoracic_radiology", "initial", error)
    trace = runner.read_json(path)

    assert trace["schema_version"] == "thoracic_radiology.v2"
    assert trace["failed_stage"] == "initial_morphologic_assessment"


def test_main_uses_shared_input_builder_with_radiology_target(monkeypatch, tmp_path):
    case = build_specialty_case_input(RUN_DIR, MdtSpecialty.THORACIC_RADIOLOGY)
    result = minimal_assessment(case)
    calls = []

    def fake_build(run_dir, specialty):
        calls.append((run_dir, specialty))
        return case

    class FakeAgent:
        @classmethod
        def from_config(cls, config_path, llm, **kwargs):
            return cls()

        def initial_assessment(self, case_input):
            assert case_input == case
            return result, {"schema_version": "thoracic_radiology.v2", "stages": []}

    monkeypatch.setattr(runner, "choose_phase", lambda: "initial")
    monkeypatch.setattr(runner, "discover_semantic_run_dirs", lambda: [tmp_path])
    monkeypatch.setattr(runner, "choose_file", lambda paths, title, optional=False: paths[0])
    monkeypatch.setattr(runner, "build_specialty_case_input", fake_build)
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "ThoracicRadiologyAgent", FakeAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    assert runner.main() == 0
    assert calls == [(tmp_path, MdtSpecialty.THORACIC_RADIOLOGY)]
    assert (tmp_path / "76-IPF_thoracic_radiology_input.json").exists()
    assert (tmp_path / "76-IPF_thoracic_radiology_working_input.json").exists()
    assert (tmp_path / "76-IPF_thoracic_radiology_initial.json").exists()
    assert (tmp_path / "76-IPF_thoracic_radiology_initial_trace.json").exists()
    report = tmp_path / "76-IPF_thoracic_radiology_initial.html"
    assert report.exists()
    html = report.read_text(encoding="utf-8")
    assert "当前影像问题" in html
    assert "七问处理状态" not in html
