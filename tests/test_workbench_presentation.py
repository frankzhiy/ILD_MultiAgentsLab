import json
from copy import deepcopy

from src.workbench import presentation


def test_wording_only_accepts_medically_equivalent_changes(monkeypatch):
    records = [
        {"path": "/a", "text": "当前首选工作诊断为暂定特发性肺纤维化（IPF）。", "context": {}},
        {"path": "/b", "text": "HRCT提示UIP可能。", "context": {}},
        {"path": "/c", "text": "未见活动性结核证据。", "context": {}},
        {"path": "/d", "text": "本轮工作判断为NSIP可能。", "context": {}},
    ]
    revisions = {
        "/a": "首选考虑特发性肺纤维化（IPF），尚待确认。",
        "/b": "HRCT提示NSIP可能。",
        "/c": "已见活动性结核证据。",
        "/d": "可能考虑NSIP。",
    }
    monkeypatch.setattr(presentation, "build_llm_client", lambda _config: object())
    attempts = 0

    def generate(_self, *, schema_name, user_prompt, **_kwargs):
        nonlocal attempts
        items = json.loads(user_prompt)
        if schema_name == "clinical_presentation_wording":
            attempts += 1
            return presentation.WordingBatch(
                items=[
                    {
                        "path": item["path"],
                        "text": "影像上考虑NSIP可能。"
                        if attempts == 2 and item["path"] == "/d"
                        else revisions[item["path"]],
                    }
                    for item in items
                ]
            ), {}
        assert schema_name == "clinical_presentation_meaning_check"
        return presentation.MeaningChecks(
            items=[
                {
                    "path": item["path"],
                    "equivalent": item["path"] != "/c",
                    "clinical_voice": item["path"] != "/d" or attempts == 2,
                    "issue": "语气仍像流程记录。",
                }
                for item in items
            ]
        ), {}

    monkeypatch.setattr(presentation.StructuredLLMGenerator, "generate", generate)
    assert presentation._polish(records, {}, presentation._load_prompts()) == {
        "/a": revisions["/a"],
        "/d": "影像上考虑NSIP可能。",
    }


def test_presentation_is_cached_and_never_changes_source(monkeypatch, tmp_path):
    prompt_path = tmp_path / "clinical_presentation.md"
    prompt_path.write_text(
        "## 润色\n\ndraft\n\n## 医学含义与口吻核对\n\nreview\n", encoding="utf-8"
    )
    monkeypatch.setattr(presentation, "_PROMPT_PATH", prompt_path)
    config_dir = tmp_path / "configs/agents/mdt_chair"
    config_dir.mkdir(parents=True)
    (config_dir / "agent.yaml").write_text("model: test-model\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = {
        "results": [
            {
                "output": {
                    "specialty_assessments": {
                        "assessments": [
                            {
                                "assessment_id": "a1",
                                "statement": "工作诊断为暂定 IPF。",
                                "medical_basis": "依据 HRCT。",
                                "status": "favored",
                            }
                        ]
                    }
                }
            }
        ],
    }
    original = deepcopy(source)
    calls = []

    def polish(records, _config, _prompts):
        calls.append(records)
        return {records[0]["path"]: "首选考虑 IPF，尚待确认。"}

    monkeypatch.setattr(presentation, "_polish", polish)
    first = presentation.present(tmp_path, run_dir, "specialties", source)
    second = presentation.present(tmp_path, run_dir, "specialties", source)
    assert first == second
    assert (
        first["results"][0]["output"]["specialty_assessments"]["assessments"][0]["statement"]
        == "首选考虑 IPF，尚待确认。"
    )
    assert source == original
    assert len(calls) == 1
    assert json.loads((run_dir / "presentation_specialties.json").read_text())["wording"]

    source["results"][0]["output"]["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "依据新 HRCT。"
    )
    presentation.present(tmp_path, run_dir, "specialties", source)
    assert len(calls) == 2

    prompt_path.write_text(
        "## 润色\n\nrevised draft\n\n## 医学含义与口吻核对\n\nreview\n", encoding="utf-8"
    )
    presentation.present(tmp_path, run_dir, "specialties", source)
    assert len(calls) == 3

    monkeypatch.setattr(
        presentation,
        "_polish",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    source["results"][0]["output"]["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "再次更新的 HRCT。"
    )
    failed = presentation.present(tmp_path, run_dir, "specialties", source)
    assert failed["presentation_status"] == "unavailable"
    assert failed["results"] == source["results"]


def test_report_presentation_keeps_derived_fields_in_sync(monkeypatch, tmp_path):
    prompt_path = tmp_path / "clinical_presentation.md"
    prompt_path.write_text(
        "## 润色\n\ndraft\n\n## 医学含义与口吻核对\n\nreview\n", encoding="utf-8"
    )
    monkeypatch.setattr(presentation, "_PROMPT_PATH", prompt_path)
    config_dir = tmp_path / "configs/agents/mdt_chair"
    config_dir.mkdir(parents=True)
    (config_dir / "agent.yaml").write_text("model: test-model\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = {
        "final_report": {
            "clinical_report": {
                "overall_conclusion": "原主诊断。",
                "integrated_summary": "原依据。",
                "diagnostic_matrix": [{
                    "dimension": "mdt_diagnosis",
                    "statement": "原主诊断。",
                    "medical_basis": "原依据。",
                }],
                "secondary_judgments": [],
                "clinical_narrative": "原主诊断。原依据。",
            },
            "reasoning_trace": [{
                "claim_id": "DX01",
                "claim_statement": "原主诊断。",
                "medical_basis": "原依据。",
            }],
        },
    }
    monkeypatch.setattr(
        presentation,
        "_polish",
        lambda records, *_args: {
            item["path"]: "首选考虑特发性肺纤维化。"
            for item in records
            if item["path"].endswith("/statement")
        },
    )

    result = presentation.present(tmp_path, run_dir, "report", source)
    report = result["final_report"]

    assert report["clinical_report"]["overall_conclusion"] == "首选考虑特发性肺纤维化。"
    assert report["clinical_report"]["clinical_narrative"].startswith("首选考虑特发性肺纤维化。")
    assert report["reasoning_trace"][0]["claim_statement"] == "首选考虑特发性肺纤维化。"
