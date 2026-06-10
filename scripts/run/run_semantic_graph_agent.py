#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
except ImportError:  # pragma: no cover - fallback for minimal environments
    Console = None
    Progress = None
    SpinnerColumn = None
    TextColumn = None
    TimeElapsedColumn = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.semantic_graphing.agent import SemanticGraphingAgent  # noqa: E402
from src.llm.chatanywhere_client import ChatAnywhereClient  # noqa: E402
from src.llm.structured import StructuredGenerationError  # noqa: E402
from src.reporting.html_report import render_report  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


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


def choose_input_file(data_dir: str = "data/raw_cases") -> Path:
    candidates = sorted(path for path in Path(data_dir).glob("*.txt") if not path.name.startswith("."))
    if not candidates:
        raise FileNotFoundError(f"No .txt files found under {Path(data_dir).resolve()}")

    print("\n可用的原始病例文件：")
    for index, path in enumerate(candidates, start=1):
        print(f"  [{index}] {path.name}")
    print()

    while True:
        try:
            choice = input(f"请输入序号 [1-{len(candidates)}]：").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\n已中止。") from exc
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(candidates):
                return candidates[index - 1]
        print("  ✗ 无效输入，请输入列表中的序号。\n")


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exception_diagnostics(exc: BaseException) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    current: BaseException | None = exc
    while current is not None:
        item: dict[str, Any] = {
            "error": str(current),
            "error_type": type(current).__name__,
        }
        if isinstance(current, StructuredGenerationError):
            item["attempts"] = current.attempts
        chain.append(item)
        current = current.__cause__ or current.__context__
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "exception_chain": chain,
        "traceback": "".join(traceback.format_exception(exc)),
    }


class ProgressReporter:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.active_steps: dict[str, float] = {}
        self.records: list[dict[str, Any]] = []
        self.console = Console() if Console is not None else None
        self.progress: Any | None = None

    def _elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m {remainder:.1f}s"

    def log(self, message: str) -> None:
        text = f"[{self._format_seconds(self._elapsed())}] {message}"
        if self.console is not None:
            self.console.print(text)
        else:
            print(text, flush=True)

    def event(self, event: str, payload: dict[str, Any]) -> None:
        now = time.perf_counter()
        if event.endswith("_started"):
            step = event.removesuffix("_started")
            self.active_steps[step] = now
            self.records.append(
                {
                    "event": event,
                    "elapsed_seconds": round(now - self.started_at, 3),
                    "payload": payload,
                }
            )
            self.log(self._started_message(step, payload))
            self._start_live_timer(step, payload)
            return

        if event.endswith("_completed"):
            step = event.removesuffix("_completed")
            self._stop_live_timer()
            step_started = self.active_steps.pop(step, None)
            duration = None if step_started is None else now - step_started
            record = {
                "event": event,
                "elapsed_seconds": round(now - self.started_at, 3),
                "duration_seconds": None if duration is None else round(duration, 3),
                "payload": payload,
            }
            self.records.append(record)
            self.log(self._completed_message(step, payload, duration))
            return

        if event == "run_failed":
            self._stop_live_timer()
        self.records.append(
            {
                "event": event,
                "elapsed_seconds": round(now - self.started_at, 3),
                "payload": payload,
            }
        )
        self.log(f"{event}: {payload}")

    def _start_live_timer(self, step: str, payload: dict[str, Any]) -> None:
        self._stop_live_timer()
        if Progress is None or self.console is None:
            return
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )
        self.progress.start()
        self.progress.add_task(self._timer_label(step, payload), total=None)

    def _stop_live_timer(self) -> None:
        if self.progress is not None:
            self.progress.stop()
        self.progress = None

    def _timer_label(self, step: str, payload: dict[str, Any]) -> str:
        if step == "classification":
            return "正在进行：clinical discourse segmentation"
        if step == "graph_unit_extraction":
            return (
                f"正在并行切分 graph units："
                f"{payload.get('segment_count')} 个 segment，"
                f"{payload.get('concurrent_tasks')} 个任务全部并发"
            )
        if step == "primary_frame_selection":
            return (
                f"正在并行选择 primary frame："
                f"{payload.get('unit_count')} 个 graph unit，"
                f"{payload.get('concurrent_tasks')} 个任务全部并发"
            )
        if step == "write_outputs":
            return "正在进行：写入结果文件和 HTML 报告"
        return f"正在进行：{step}"

    def _started_message(self, step: str, payload: dict[str, Any]) -> str:
        if step == "classification":
            return "开始 clinical discourse segmentation..."
        if step == "graph_unit_extraction":
            return (
                f"开始并行 graph-unit extraction：{payload.get('segment_count')} 个 segment，"
                f"{payload.get('concurrent_tasks')} 个任务全部并发..."
            )
        if step == "primary_frame_selection":
            return (
                f"开始并行 primary frame selection：{payload.get('unit_count')} 个 graph unit，"
                f"{payload.get('concurrent_tasks')} 个任务全部并发..."
            )
        if step == "write_outputs":
            return "开始写入结果文件和 HTML 报告..."
        return f"开始 {step}..."

    def _completed_message(
        self,
        step: str,
        payload: dict[str, Any],
        duration: float | None,
    ) -> str:
        duration_text = "耗时未知" if duration is None else f"耗时 {self._format_seconds(duration)}"
        if step == "classification":
            return (
                f"完成 discourse segmentation：{payload.get('segment_count')} 个片段，"
                f"contained={payload.get('contained_source_types')}，{duration_text}"
            )
        if step == "graph_unit_extraction":
            return (
                f"完成 graph-unit extraction：{payload.get('segment_count')} 个 segment，"
                f"{payload.get('graph_unit_count')} 个 graph units，{duration_text}"
            )
        if step == "primary_frame_selection":
            return (
                f"完成 primary frame selection：{payload.get('unit_count')} 个 graph unit，"
                f"{payload.get('boundary_warning_count')} 个边界复核提示，{duration_text}"
            )
        if step == "write_outputs":
            return f"完成结果写入，{duration_text}"
        return f"完成 {step}，{duration_text}"

    def summary(self) -> dict[str, Any]:
        return {
            "total_elapsed_seconds": round(self._elapsed(), 3),
            "events": self.records,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Step 2 and Step 3 of the ILD semantic graphing workflow: "
            "clinical discourse segmentation, graph-unit extraction, and primary-frame selection."
        )
    )
    parser.add_argument(
        "--input",
        default=None,
        help=(
            "Path to a free-text ILD case file. If omitted, choose from txt files under "
            "data/raw_cases/."
        ),
    )
    parser.add_argument("--case-id", default=None, help="Case identifier for outputs.")
    parser.add_argument(
        "--config",
        default="configs/agents/semantic_graphing/agent.yaml",
        help="Agent config YAML.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/runs",
        help="Directory where timestamped run folders are created.",
    )
    args = parser.parse_args()
    load_env_file()

    input_path = Path(args.input) if args.input else choose_input_file()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    config = load_yaml(args.config)
    case_id = args.case_id or input_path.stem
    llm = ChatAnywhereClient.from_config(config)
    agent = SemanticGraphingAgent.from_config(args.config, llm)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / f"{timestamp}_{case_id}_step2_step3"
    run_dir.mkdir(parents=True, exist_ok=False)

    input_text = input_path.read_text(encoding="utf-8")
    reporter = ProgressReporter()
    reporter.log(f"输入文件：{input_path}")
    reporter.log(f"输出目录：{run_dir}")
    reporter.log(f"模型：{llm.model}")
    reporter.log(
        "当前阶段：Step 2 discourse segmentation + Step 3 Stage 1 primary frame selection；"
        "产出 discourse segments + graph units + primary frames。"
    )

    try:
        result = agent.classify(input_text, case_id=case_id, progress=reporter.event)
        graph_units, graph_unit_trace = agent.extract_graph_units(
            result.classification,
            progress=reporter.event,
        )
        primary_frames, primary_frame_trace = agent.select_primary_frames(
            graph_units,
            progress=reporter.event,
        )
    except Exception as exc:
        diagnostics = exception_diagnostics(exc)
        reporter.event(
            "run_failed",
            {
                "error": diagnostics["error"],
                "error_type": diagnostics["error_type"],
            },
        )
        (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
        write_json(
            run_dir / "error.json",
            {
                "case_id": case_id,
                "input_path": str(input_path),
                "model": llm.model,
                **diagnostics,
            },
        )
        write_json(run_dir / "timing.json", reporter.summary())
        print(f"Run directory: {run_dir.resolve()}")
        print(f"Error: {exc}")
        print(f"Detailed diagnostics: {(run_dir / 'error.json').resolve()}")
        return 1

    reporter.event("write_outputs_started", {})
    write_json(run_dir / "discourse_segments.json", result.classification.model_dump())
    write_json(run_dir / "graph_units.json", graph_units.model_dump())
    write_json(run_dir / "primary_frames.json", primary_frames.model_dump())
    write_json(
        run_dir / "trace.json",
        {
            **result.trace,
            "graph_unit_extraction": graph_unit_trace,
            "primary_frame_selection": primary_frame_trace,
        },
    )
    (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
    timing_before_report = reporter.summary()
    report_path = render_report(
        result,
        graph_units,
        source_filename=input_path.name,
        raw_text=input_text,
        timing=timing_before_report,
        output_path=run_dir / "report.html",
        primary_frames=primary_frames,
    )
    reporter.event(
        "write_outputs_completed",
        {
            "report_path": str(report_path),
        },
    )
    timing_summary = reporter.summary()
    write_json(run_dir / "timing.json", timing_summary)

    print(f"Run directory: {run_dir.resolve()}")
    print(f"HTML report: {report_path.resolve()}")
    print(f"Segments: {len(result.classification.segments)}")
    print(f"Graph units: {sum(len(item.graph_units) for item in graph_units.segments)}")
    print(f"Primary frames: {sum(len(item.units) for item in primary_frames.segments)}")
    print(
        "Boundary warnings: "
        f"{sum(unit.boundary_warning is not None for item in primary_frames.segments for unit in item.units)}"
    )
    print(
        "Contained source types: "
        f"{[str(item) for item in result.classification.detected_contained_source_types]}"
    )
    print(
        "Total elapsed: "
        f"{ProgressReporter._format_seconds(timing_summary['total_elapsed_seconds'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
