#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.semantic_graphing.agent import SemanticGraphingAgent  # noqa: E402
from src.llm.chatanywhere_client import ChatAnywhereClient  # noqa: E402
from src.reporting.html_report import render_classification_report  # noqa: E402


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


class ProgressReporter:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.active_steps: dict[str, float] = {}
        self.records: list[dict[str, Any]] = []

    def _elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m {remainder:.1f}s"

    def log(self, message: str) -> None:
        print(f"[{self._format_seconds(self._elapsed())}] {message}", flush=True)

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
            return

        if event.endswith("_completed"):
            step = event.removesuffix("_completed")
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

        self.records.append(
            {
                "event": event,
                "elapsed_seconds": round(now - self.started_at, 3),
                "payload": payload,
            }
        )
        self.log(f"{event}: {payload}")

    def _started_message(self, step: str, payload: dict[str, Any]) -> str:
        if step == "classification":
            return "开始文本分段和来源类型分类..."
        if step == "subgraph":
            return (
                f"开始构建子图 {payload.get('index')}/{payload.get('total')} "
                f"({payload.get('segment_id')}, {payload.get('source_type')})..."
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
                f"完成分类：{payload.get('segment_count')} 个片段，"
                f"类型 {payload.get('source_types')}，{duration_text}"
            )
        if step == "subgraph":
            return (
                f"完成子图 {payload.get('segment_id')}："
                f"{payload.get('node_count')} nodes / {payload.get('edge_count')} edges，"
                f"{duration_text}"
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
        description="Run Step 2 of the ILD semantic graphing workflow: segment classification."
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

    case_id = args.case_id or input_path.stem
    llm = ChatAnywhereClient.from_env()
    agent = SemanticGraphingAgent.from_config(args.config, llm)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_dir) / f"{timestamp}_{case_id}_step2_classification"
    run_dir.mkdir(parents=True, exist_ok=False)

    input_text = input_path.read_text(encoding="utf-8")
    reporter = ProgressReporter()
    reporter.log(f"输入文件：{input_path}")
    reporter.log(f"输出目录：{run_dir}")
    reporter.log(f"模型：{llm.model}")
    reporter.log("当前阶段：Step 2 文本分段与来源类型分类；不做建图，不做子图合并。")

    try:
        result = agent.classify(input_text, case_id=case_id, progress=reporter.event)
    except Exception as exc:
        reporter.event("run_failed", {"error": str(exc)})
        (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
        write_json(
            run_dir / "error.json",
            {
                "case_id": case_id,
                "input_path": str(input_path),
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        write_json(run_dir / "timing.json", reporter.summary())
        print(f"Run directory: {run_dir.resolve()}")
        print(f"Error: {exc}")
        return 1

    reporter.event("write_outputs_started", {})
    write_json(run_dir / "classification.json", result.classification.model_dump())
    write_json(run_dir / "trace.json", result.trace)
    (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
    timing_before_report = reporter.summary()
    report_path = render_classification_report(
        result,
        source_filename=input_path.name,
        raw_text=input_text,
        timing=timing_before_report,
        output_path=run_dir / "classification_report.html",
    )
    reporter.event("write_outputs_completed", {"report_path": str(report_path)})
    timing_summary = reporter.summary()
    write_json(run_dir / "timing.json", timing_summary)

    print(f"Run directory: {run_dir.resolve()}")
    print(f"HTML report: {report_path.resolve()}")
    print(f"Segments: {len(result.classification.segments)}")
    print(
        "Source types: "
        f"{[str(item) for item in result.classification.detected_source_types]}"
    )
    print(
        "Total elapsed: "
        f"{ProgressReporter._format_seconds(timing_summary['total_elapsed_seconds'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
