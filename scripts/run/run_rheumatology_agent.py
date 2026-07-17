#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
from typing import Iterator

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
except ImportError:  # pragma: no cover - fallback for minimal environments
    Console = Progress = SpinnerColumn = TextColumn = TimeElapsedColumn = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input  # noqa: E402
from src.agents.common.evidence_projection import (  # noqa: E402
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.rheumatology.agent import RheumatologyAgent  # noqa: E402
from src.agents.rheumatology.models import (  # noqa: E402
    RheumatologyDiscussionInput,
    RheumatologyInitialAssessment,
    SpecialistOpinion,
)
from src.llm.factory import build_llm_client  # noqa: E402
from src.llm.prompting import llm_value  # noqa: E402
from src.llm.structured import StructuredGenerationError  # noqa: E402
from src.reporting.rheumatology_report import render_rheumatology_report  # noqa: E402
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


CONFIG_PATH = ROOT / "configs/agents/rheumatology/agent.yaml"
RUNS_DIR = ROOT / "outputs/runs"

STAGE_LABELS = {
    "initial_case_reconstruction": "首轮 1/3：自身免疫病例重建",
    "initial_autoimmune_assessment": "首轮 2/3：自身免疫诊断评估",
    "initial_consult_formulation": "首轮 3/3：风湿科诊断综合",
    "discussion_evidence_mapping": "会中 1/3：专科证据映射",
    "discussion_state_update": "会中 2/3：风湿状态更新",
    "discussion_consult_response": "会中 3/3：会诊响应",
}


class ProgressReporter:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.console = Console() if Console is not None else None

    @staticmethod
    def format_seconds(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m {remainder:.1f}s"

    def log(self, message: str) -> None:
        text = f"[{self.format_seconds(time.perf_counter() - self.started_at)}] {message}"
        if self.console is not None:
            self.console.print(text, markup=False)
        else:
            print(text, flush=True)

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        started = time.perf_counter()
        self.log(f"开始：{label}")
        progress = None
        if Progress is not None and self.console is not None:
            progress = Progress(
                SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(), console=self.console, transient=True,
            )
            progress.start()
            progress.add_task(f"正在进行：{label}", total=None)
        try:
            yield
        except BaseException:
            if progress is not None:
                progress.stop()
            self.log(f"失败：{label}")
            raise
        if progress is not None:
            progress.stop()
        self.log(f"完成：{label}，耗时 {self.format_seconds(time.perf_counter() - started)}")

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
            usage = ""
            if "prompt_tokens" in payload:
                usage = f"，输入 {payload['prompt_tokens']} tokens，输出 {payload.get('completion_tokens', 0)} tokens"
            self.log(f"{stage} · 第 {attempt} 次 LLM 响应完成，耗时 {duration}{usage}")
        elif event == "llm_attempt_failed":
            self.log(f"{stage} · 第 {attempt} 次 LLM 请求失败，耗时 {duration}")
        elif event == "validation_completed":
            self.log(f"{stage} · JSON 解析与本地校验通过，耗时 {duration}")
        elif event == "validation_failed":
            retry = "，将重新请求 LLM" if payload.get("will_retry") else ""
            self.log(f"{stage} · JSON 解析或本地校验未通过，耗时 {duration}{retry}")
        elif event in {"stage_completed", "stage_failed"}:
            result = "完成" if event == "stage_completed" else "失败"
            self.log(
                f"{result}：{stage}，总耗时 {duration}；"
                f"LLM {self.format_seconds(float(payload.get('llm_duration_seconds', 0.0)))}，"
                f"本地校验 {self.format_seconds(float(payload.get('validation_duration_seconds', 0.0)))}"
            )


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip("\"'")


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


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_semantic_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted({path.parent for path in RUNS_DIR.rglob("*_discourse_segments.json")}, key=lambda path: path.stat().st_mtime, reverse=True)


def write_failure_trace(run_dir: Path, stem: str, phase: str, error: StructuredGenerationError) -> Path:
    path = run_dir / f"{stem}_{phase}_failure_trace.json"
    write_json(path, {"schema_version": "rheumatology.v1", "phase": phase, "failed_stage": error.stage, "error": str(error), "attempts": error.attempts})
    return path


def main() -> int:
    load_env_file()
    phase = input("运行阶段 [1] 首轮评估 [2] 会中响应：").strip()
    if phase not in {"1", "2"}:
        raise ValueError("阶段必须为 1 或 2")
    run_dir = choose(discover_semantic_run_dirs(), "semantic_graphing 运行目录")
    assert run_dir is not None
    progress = ProgressReporter()
    with progress.step("准备并写入风湿科输入"):
        case = build_specialty_case_input(run_dir, MdtSpecialty.RHEUMATOLOGY)
        input_path = run_dir / f"{case.case_id}_rheumatology_input.json"
        write_json(input_path, case.model_dump(mode="json"))
        working_input = build_specialty_working_input(case)
        working_input_path = run_dir / f"{case.case_id}_rheumatology_working_input.json"
        write_json(working_input_path, working_input.model_dump(mode="json"))
        evidence_input_path = run_dir / f"{case.case_id}_rheumatology_evidence_input.json"
        write_json(
            evidence_input_path,
            llm_value(build_specialty_evidence_prompt_input(case)),
        )
    progress.log(
        f"风湿科输入：{input_path.resolve()}；segments={case.summary.segment_count} "
        f"units={case.summary.unit_count} owned={case.summary.owned_unit_count} "
        f"shared={case.summary.shared_context_unit_count} "
        f"reference={case.summary.reference_only_unit_count}"
    )
    progress.log(f"首阶段病例输入：{working_input_path.resolve()}")
    progress.log(f"后续阶段证据输入：{evidence_input_path.resolve()}")
    with progress.step("初始化风湿科 Agent"):
        config = load_yaml(CONFIG_PATH)
        agent = RheumatologyAgent.from_config(
            CONFIG_PATH, build_llm_client(config), event_callback=progress.generation_event
        )
    stem = f"{case.case_id}_rheumatology"
    if phase == "1":
        try:
            with progress.step("LLM 按三阶段生成风湿科首轮评估"):
                result, trace = agent.initial_assessment(case)
        except StructuredGenerationError as error:
            failure_path = write_failure_trace(run_dir, stem, "initial", error)
            progress.log(f"失败 trace：{failure_path.resolve()}")
            raise
        suffix = "initial"
    else:
        initial_path = choose(sorted(run_dir.glob(f"{stem}_initial.json")), "风湿科首轮评估文件")
        opinions_path = choose(sorted(run_dir.glob("*specialist_opinions*.json")), "其他专科意见文件", optional=True)
        questions_path = choose(sorted(run_dir.glob("*chair_questions*.json")), "主席问题文件", optional=True)
        initial = RheumatologyInitialAssessment.model_validate_json(initial_path.read_text(encoding="utf-8"))
        opinions = [SpecialistOpinion.model_validate(item) for item in (read_json(opinions_path) if opinions_path else [])]
        questions = read_json(questions_path) if questions_path else []
        if not isinstance(questions, list):
            raise ValueError("chair questions JSON must be a list")
        try:
            with progress.step("LLM 按三阶段生成风湿科会中响应"):
                result, trace = agent.discussion_response(RheumatologyDiscussionInput(case_input=case, initial_assessment=initial, specialist_opinions=opinions, chair_questions=questions))
        except StructuredGenerationError as error:
            failure_path = write_failure_trace(run_dir, stem, "discussion", error)
            progress.log(f"失败 trace：{failure_path.resolve()}")
            raise
        suffix = "discussion"
    output = run_dir / f"{stem}_{suffix}.json"
    trace_output = run_dir / f"{stem}_{suffix}_trace.json"
    report_output = run_dir / f"{stem}_{suffix}.html"
    with progress.step("写入 JSON、trace 和 HTML 报告"):
        write_json(output, result.model_dump(mode="json"))
        write_json(trace_output, trace)
        render_rheumatology_report(result, case, report_output)
    progress.log(f"JSON 结果：{output.resolve()}")
    progress.log(f"Trace：{trace_output.resolve()}")
    progress.log(f"HTML 报告：{report_output.resolve()}")
    progress.log(f"运行完成，总耗时 {progress.total_elapsed()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
