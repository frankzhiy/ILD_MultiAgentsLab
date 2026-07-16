#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input  # noqa: E402
from src.agents.common.evidence_projection import (  # noqa: E402
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.pathology.agent import PathologyAgent  # noqa: E402
from src.agents.pathology.models import (  # noqa: E402
    PathologyDiscussionInput,
    PathologyInitialAssessment,
    SpecialistOpinion,
)
from src.llm.factory import build_llm_client  # noqa: E402
from src.llm.prompting import llm_value  # noqa: E402
from src.llm.structured import StructuredGenerationError  # noqa: E402
from src.reporting.pathology_report import render_pathology_report  # noqa: E402
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


CONFIG_PATH = ROOT / "configs/agents/pathology/agent.yaml"
RUNS_DIR = ROOT / "outputs/runs"

STAGE_LABELS = {
    "initial_specimen_reconstruction": "首轮 1/3：标本与来源重建",
    "initial_morphologic_assessment": "首轮 2/3：组织形态评估",
    "initial_consult_formulation": "首轮 3/3：病理会诊综合",
    "discussion_evidence_mapping": "会中 1/3：专科证据映射",
    "discussion_state_update": "会中 2/3：病理状态更新",
    "discussion_consult_response": "会中 3/3：会诊响应",
}


class ProgressReporter:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()

    @staticmethod
    def format_seconds(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m {remainder:.1f}s"

    def log(self, message: str) -> None:
        elapsed = self.format_seconds(time.perf_counter() - self.started_at)
        print(f"[{elapsed}] {message}", flush=True)

    def generation_event(self, event: str, payload: dict) -> None:
        stage = STAGE_LABELS.get(payload.get("stage"), payload.get("stage", "未知阶段"))
        duration = self.format_seconds(float(payload.get("duration_seconds", 0.0)))
        if event == "stage_started":
            self.log(f"开始：{stage}")
        elif event == "llm_attempt_completed":
            self.log(f"{stage} · LLM 响应完成，耗时 {duration}")
        elif event == "validation_failed":
            retry = "，将重新请求" if payload.get("will_retry") else ""
            self.log(f"{stage} · 本地校验未通过{retry}")
        elif event == "stage_completed":
            self.log(
                f"完成：{stage}；LLM "
                f"{self.format_seconds(float(payload.get('llm_duration_seconds', 0.0)))}，"
                f"校验 {self.format_seconds(float(payload.get('validation_duration_seconds', 0.0)))}"
            )


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip("\"'")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_semantic_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        {path.parent for path in RUNS_DIR.rglob("*_discourse_segments.json")},
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def choose(options: list[Path], title: str, optional: bool = False) -> Path | None:
    if not options:
        if optional:
            return None
        raise FileNotFoundError(f"未找到{title}")
    print(f"\n请选择{title}：")
    if optional:
        print("  [0] 不加载")
    for index, option in enumerate(options, 1):
        print(f"  [{index}] {option}")
    while True:
        choice = input("请输入序号：").strip()
        if optional and choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("无效输入。")


def write_failure_trace(
    run_dir: Path, stem: str, phase: str, error: StructuredGenerationError
) -> Path:
    path = run_dir / f"{stem}_{phase}_failure_trace.json"
    write_json(
        path,
        {
            "schema_version": "pathology.v1",
            "phase": phase,
            "failed_stage": error.stage,
            "error": str(error),
            "attempts": error.attempts,
        },
    )
    return path


def main() -> int:
    load_env_file()
    phase = input("运行阶段 [1] 首轮评估 [2] 会中响应：").strip()
    if phase not in {"1", "2"}:
        raise ValueError("阶段必须为 1 或 2")
    run_dir = choose(discover_semantic_run_dirs(), "semantic_graphing 运行目录")
    assert run_dir is not None
    progress = ProgressReporter()

    case = build_specialty_case_input(run_dir, MdtSpecialty.PATHOLOGY)
    stem = f"{case.case_id}_pathology"
    input_path = run_dir / f"{stem}_input.json"
    working_path = run_dir / f"{stem}_working_input.json"
    evidence_path = run_dir / f"{stem}_evidence_input.json"
    working = build_specialty_working_input(case)
    write_json(input_path, case.model_dump(mode="json"))
    write_json(working_path, working.model_dump(mode="json"))
    write_json(evidence_path, llm_value(build_specialty_evidence_prompt_input(case)))
    progress.log(
        f"病理科输入完成：units={case.summary.unit_count} "
        f"owned={case.summary.owned_unit_count} shared={case.summary.shared_context_unit_count} "
        f"collaborative={case.summary.collaborative_context_unit_count} "
        f"reference={case.summary.reference_only_unit_count}"
    )

    config = load_yaml(CONFIG_PATH)
    agent = PathologyAgent.from_config(
        CONFIG_PATH,
        build_llm_client(config),
        event_callback=progress.generation_event,
    )
    if phase == "1":
        try:
            result, trace = agent.initial_assessment(case)
        except StructuredGenerationError as error:
            progress.log(f"失败 trace：{write_failure_trace(run_dir, stem, 'initial', error)}")
            raise
        suffix = "initial"
    else:
        initial_path = choose(
            sorted(run_dir.glob(f"{stem}_initial.json")), "病理科首轮评估文件"
        )
        assert initial_path is not None
        initial = PathologyInitialAssessment.model_validate_json(
            initial_path.read_text(encoding="utf-8")
        )
        opinions_path = choose(
            sorted(run_dir.glob("*specialist_opinions*.json")),
            "其他专科意见文件",
            optional=True,
        )
        questions_path = choose(
            sorted(run_dir.glob("*chair_questions*.json")),
            "主席问题文件",
            optional=True,
        )
        opinions = [
            SpecialistOpinion.model_validate(item)
            for item in (read_json(opinions_path) if opinions_path else [])
        ]
        questions = read_json(questions_path) if questions_path else []
        if not isinstance(questions, list):
            raise ValueError("chair questions JSON must be a list")
        try:
            result, trace = agent.discussion_response(
                PathologyDiscussionInput(
                    case_input=case,
                    initial_assessment=initial,
                    specialist_opinions=opinions,
                    chair_questions=questions,
                )
            )
        except StructuredGenerationError as error:
            progress.log(
                f"失败 trace：{write_failure_trace(run_dir, stem, 'discussion', error)}"
            )
            raise
        suffix = "discussion"

    output = run_dir / f"{stem}_{suffix}.json"
    trace_output = run_dir / f"{stem}_{suffix}_trace.json"
    report_output = run_dir / f"{stem}_{suffix}.html"
    write_json(output, result.model_dump(mode="json"))
    write_json(trace_output, trace)
    render_pathology_report(result, case, report_output)
    progress.log(f"JSON：{output.resolve()}")
    progress.log(f"Trace：{trace_output.resolve()}")
    progress.log(f"HTML：{report_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
