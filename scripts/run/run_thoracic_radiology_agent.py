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
from src.agents.thoracic_radiology.agent import ThoracicRadiologyAgent  # noqa: E402
from src.agents.thoracic_radiology.models import (  # noqa: E402
    SpecialistOpinion,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyInitialAssessment,
)
from src.llm.factory import build_llm_client  # noqa: E402
from src.llm.structured import StructuredGenerationError  # noqa: E402
from src.reporting.thoracic_radiology_report import (  # noqa: E402
    render_thoracic_radiology_report,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


CONFIG_PATH = ROOT / "configs/agents/thoracic_radiology/agent.yaml"
RUNS_DIR = ROOT / "outputs/runs"
STAGE_LABELS = {
    "initial_source_reconstruction": "首轮 1/3：来源与可评价性",
    "initial_morphologic_assessment": "首轮 2/3：描述派生形态评估",
    "initial_imaging_formulation": "首轮 3/3：影像模式与疾病关联",
    "discussion_evidence_mapping": "会中 1/3：专科证据映射",
    "discussion_imaging_update": "会中 2/3：七域影像状态更新",
    "discussion_consult_response": "会中 3/3：影像科会诊响应",
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

    def total_elapsed(self) -> str:
        return self.format_seconds(time.perf_counter() - self.started_at)

    def generation_event(self, event: str, payload: dict) -> None:
        stage = STAGE_LABELS.get(payload.get("stage"), payload.get("stage", "未知阶段"))
        attempt = payload.get("attempt")
        duration = self.format_seconds(float(payload.get("duration_seconds", 0.0)))
        if event == "stage_started":
            self.log(f"开始：{stage}")
        elif event == "llm_attempt_started":
            self.log(f"{stage} · 第 {attempt} 次 LLM 请求开始")
        elif event == "llm_attempt_completed":
            self.log(f"{stage} · 第 {attempt} 次 LLM 响应完成，耗时 {duration}")
        elif event == "validation_failed":
            retry = "，将重新请求 LLM" if payload.get("will_retry") else ""
            self.log(f"{stage} · 本地校验未通过{retry}")
        elif event in {"stage_completed", "stage_failed"}:
            result = "完成" if event == "stage_completed" else "失败"
            self.log(f"{result}：{stage}，总耗时 {duration}")


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_failure_trace(
    run_dir: Path,
    output_stem: str,
    phase: str,
    error: StructuredGenerationError,
) -> Path:
    path = run_dir / f"{output_stem}_{phase}_failure_trace.json"
    write_json(
        path,
        {
            "schema_version": "thoracic_radiology.v1",
            "phase": phase,
            "failed_stage": error.stage,
            "error": str(error),
            "attempts": error.attempts,
        },
    )
    return path


def choose_phase() -> str:
    print("\n请选择胸部影像科 Agent 运行阶段：")
    print("  [1] 首轮评估")
    print("  [2] 会中响应")
    while True:
        try:
            choice = input("请输入序号 [1-2]：").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\n已中止。") from exc
        if choice == "1":
            return "initial"
        if choice == "2":
            return "discussion"
        print("  ✗ 无效输入，请输入 1 或 2。\n")


def choose_file(paths: list[Path], title: str, *, optional: bool = False) -> Path | None:
    if not paths:
        if optional:
            print(f"\n未找到可用的{title}，本次不加载。")
            return None
        raise FileNotFoundError(f"未找到可用的{title}。")
    print(f"\n请选择{title}：")
    if optional:
        print("  [0] 不加载")
    for index, path in enumerate(paths, start=1):
        print(f"  [{index}] {path}")
    lower = 0 if optional else 1
    while True:
        try:
            choice = input(f"请输入序号 [{lower}-{len(paths)}]：").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\n已中止。") from exc
        if choice.isdigit():
            index = int(choice)
            if optional and index == 0:
                return None
            if 1 <= index <= len(paths):
                return paths[index - 1]
        print("  ✗ 无效输入，请输入列表中的序号。\n")


def discover_files(pattern: str, root: Path = RUNS_DIR) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)


def discover_semantic_run_dirs() -> list[Path]:
    run_dirs = {path.parent for path in discover_files("*_discourse_segments.json")}
    return sorted(run_dirs, key=lambda path: path.stat().st_mtime, reverse=True)


def main() -> int:
    load_env_file()
    phase = choose_phase()
    run_dir = choose_file(discover_semantic_run_dirs(), "semantic_graphing 运行目录")
    assert run_dir is not None
    progress = ProgressReporter()
    progress.log("准备并写入胸部影像科输入")
    case_input = build_specialty_case_input(run_dir, MdtSpecialty.THORACIC_RADIOLOGY)
    input_path = run_dir / f"{case_input.case_id}_thoracic_radiology_input.json"
    write_json(input_path, case_input.model_dump(mode="json"))
    progress.log(
        f"输入完成：segments={case_input.summary.segment_count} "
        f"units={case_input.summary.unit_count} owned={case_input.summary.owned_unit_count} "
        f"shared={case_input.summary.shared_context_unit_count} "
        f"collaborative={case_input.summary.collaborative_context_unit_count} "
        f"reference={case_input.summary.reference_only_unit_count}"
    )
    config = load_yaml(CONFIG_PATH)
    agent = ThoracicRadiologyAgent.from_config(
        CONFIG_PATH,
        build_llm_client(config),
        event_callback=progress.generation_event,
    )
    output_stem = f"{case_input.case_id}_thoracic_radiology"

    if phase == "initial":
        try:
            result, trace = agent.initial_assessment(case_input)
        except StructuredGenerationError as exc:
            failure_path = write_failure_trace(run_dir, output_stem, "initial", exc)
            progress.log(f"失败 trace：{failure_path.resolve()}")
            raise
        suffix = "initial"
    else:
        initial_path = choose_file(
            discover_files(f"{case_input.case_id}_thoracic_radiology_initial.json", run_dir),
            "胸部影像科首轮评估文件",
        )
        assert initial_path is not None
        initial = ThoracicRadiologyInitialAssessment.model_validate_json(
            initial_path.read_text(encoding="utf-8")
        )
        opinions_path = choose_file(
            discover_files("*specialist_opinions*.json", run_dir),
            "其他专科意见文件",
            optional=True,
        )
        questions_path = choose_file(
            discover_files("*chair_questions*.json", run_dir),
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
                ThoracicRadiologyDiscussionInput(
                    case_input=case_input,
                    initial_assessment=initial,
                    specialist_opinions=opinions,
                    chair_questions=questions,
                )
            )
        except StructuredGenerationError as exc:
            failure_path = write_failure_trace(run_dir, output_stem, "discussion", exc)
            progress.log(f"失败 trace：{failure_path.resolve()}")
            raise
        suffix = "discussion"

    output = run_dir / f"{output_stem}_{suffix}.json"
    trace_output = run_dir / f"{output_stem}_{suffix}_trace.json"
    report_output = run_dir / f"{output_stem}_{suffix}.html"
    write_json(output, result.model_dump(mode="json"))
    write_json(trace_output, trace)
    render_thoracic_radiology_report(result, case_input, report_output)
    progress.log(f"JSON 结果：{output.resolve()}")
    progress.log(f"Trace：{trace_output.resolve()}")
    progress.log(f"HTML 报告：{report_output.resolve()}")
    progress.log(f"运行完成，总耗时 {progress.total_elapsed()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
