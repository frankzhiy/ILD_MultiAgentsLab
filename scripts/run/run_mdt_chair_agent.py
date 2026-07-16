#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.mdt_chair.agent import (  # noqa: E402
    MDTChairAgent,
    SPECIALTIES,
    build_chair_prompt_bundle,
    build_semantic_evidence_catalog,
)
from src.llm.factory import build_llm_client  # noqa: E402
from src.llm.structured import StructuredGenerationError  # noqa: E402
from src.reporting.mdt_chair_report import render_mdt_chair_report  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


CONFIG_PATH = ROOT / "configs/agents/mdt_chair/agent.yaml"
RUNS_DIR = ROOT / "outputs/runs"


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        {
            path.parent
            for path in RUNS_DIR.rglob("*_pulmonology_initial.json")
            if all(
                any(path.parent.glob(f"*_{specialty}_initial.json"))
                for specialty in SPECIALTIES
            )
        },
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def choose_run_dir(paths: list[Path]) -> Path:
    if not paths:
        raise FileNotFoundError("未找到同时包含四个专科首轮输出的运行目录。")
    print("\n请选择包含四个专科首轮输出的运行目录：")
    for index, path in enumerate(paths, start=1):
        print(f"  [{index}] {path.relative_to(ROOT)}")
    while True:
        choice = input(f"请输入序号 [1-{len(paths)}]：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(paths):
            return paths[int(choice) - 1]


def _single(path: Path, pattern: str) -> Path:
    matches = sorted(path.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {path}; found {len(matches)}")
    return matches[0]


def _event(event: str, payload: dict) -> None:
    if event == "llm_attempt_started":
        print(f"开始主持人 LLM 请求，第 {payload.get('attempt')} 次尝试……", flush=True)
    elif event == "llm_attempt_completed":
        tokens = (
            f"，输入 {payload['prompt_tokens']} tokens，输出 {payload.get('completion_tokens', 0)} tokens"
            if "prompt_tokens" in payload
            else ""
        )
        print(f"主持人 LLM 响应完成{tokens}", flush=True)
    elif event == "validation_failed":
        print("结构化输出校验未通过，准备重试……", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the first-pass ILD MDT chair report.")
    parser.add_argument("--run-dir", help="Directory containing four specialty initial JSON files.")
    args = parser.parse_args()
    load_env_file()
    run_dir = (
        Path(args.run_dir).resolve()
        if args.run_dir
        else choose_run_dir(discover_run_dirs())
    )
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    outputs = {
        specialty: read_json(_single(run_dir, f"*_{specialty}_initial.json"))
        for specialty in SPECIALTIES
    }
    case_ids = {str(value.get("case_id") or "") for value in outputs.values()}
    case_ids.discard("")
    if len(case_ids) != 1:
        raise ValueError(f"Specialty outputs do not share one case_id: {sorted(case_ids)}")
    case_id = case_ids.pop()
    input_summaries = {}
    for specialty in SPECIALTIES:
        candidates = sorted(run_dir.glob(f"{case_id}_{specialty}_input.json"))
        if candidates:
            input_summaries[specialty] = read_json(candidates[0]).get("summary", {})

    started = time.perf_counter()
    semantic_evidence = build_semantic_evidence_catalog(
        read_json(_single(run_dir, f"{case_id}_clinical_propositions.json")),
        read_json(_single(run_dir, f"{case_id}_local_graphs.json")),
    )
    bundle = build_chair_prompt_bundle(
        case_id,
        outputs,
        input_summaries,
        semantic_evidence,
    )
    compact_path = run_dir / f"{case_id}_mdt_chair_prompt_input.json"
    write_json(compact_path, bundle.prompt_input)
    raw_chars = sum(len(json.dumps(value, ensure_ascii=False)) for value in outputs.values())
    compact_chars = len(json.dumps(bundle.prompt_input, ensure_ascii=False))
    print(
        f"主持人输入压缩完成：{raw_chars:,} → {compact_chars:,} 字符 "
        f"({compact_chars / raw_chars:.1%})",
        flush=True,
    )

    config = load_yaml(CONFIG_PATH)
    agent = MDTChairAgent.from_config(
        CONFIG_PATH,
        build_llm_client(config),
        event_callback=_event,
    )
    try:
        result, trace = agent.synthesize(bundle)
    except StructuredGenerationError as error:
        failure = run_dir / f"{case_id}_mdt_chair_failure_trace.json"
        write_json(
            failure,
            {
                "schema_version": "mdt_chair.v1",
                "failed_stage": error.stage,
                "error": str(error),
                "attempts": error.attempts,
            },
        )
        print(f"失败 trace：{failure}", flush=True)
        raise

    json_path = run_dir / f"{case_id}_mdt_chair_initial.json"
    trace_path = run_dir / f"{case_id}_mdt_chair_initial_trace.json"
    html_path = run_dir / f"{case_id}_mdt_chair_initial.html"
    write_json(json_path, result.model_dump(mode="json"))
    write_json(trace_path, trace)
    render_mdt_chair_report(result, html_path)
    print(f"JSON：{json_path}")
    print(f"Trace：{trace_path}")
    print(f"HTML：{html_path}")
    print(f"总耗时：{time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
