"""Doctor-facing wording for workbench pages; clinical artifacts remain untouched."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.llm.factory import build_llm_client
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml


_VERSION = 1
_LOCK = (
    Lock()
)  # ponytail: one presentation at a time; use per-run locks if this becomes a bottleneck.
_TERMS = re.compile(r"[A-Za-z][A-Za-z0-9/-]*|\d+(?:\.\d+)?")
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts/common/clinical_presentation.md"


def _load_prompts() -> dict[str, str]:
    wording, separator, meaning_check = load_text(_PROMPT_PATH).partition(
        "\n## 医学含义与口吻核对\n"
    )
    if (
        not wording.startswith("## 润色\n")
        or not wording.removeprefix("## 润色\n").strip()
        or not separator
        or not meaning_check.strip()
    ):
        raise ValueError("clinical presentation prompts must contain both sections")
    return {
        "wording": wording.removeprefix("## 润色\n").strip(),
        "meaning_check": meaning_check.strip(),
    }


class Wording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    text: str = Field(min_length=1)


class WordingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Wording]


class MeaningCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    equivalent: bool
    clinical_voice: bool
    issue: str = ""


class MeaningChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MeaningCheck]


def _records(scope: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def add(path: str, item: dict[str, Any], fields: tuple[str, ...]) -> None:
        context = {
            key: item[key]
            for key in ("status", "role", "specific_disease", "confidence")
            if item.get(key)
        }
        for field in fields:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                records.append({"path": f"{path}/{field}", "text": value, "context": context})

    def chair(path: str, result: dict[str, Any] | None) -> None:
        for index, item in enumerate((result or {}).get("integrated_conclusions", [])):
            add(
                f"{path}/integrated_conclusions/{index}",
                item,
                ("statement", "medical_basis", "decision_impact"),
            )
        for index, item in enumerate((result or {}).get("assessment_boundaries", [])):
            add(f"{path}/assessment_boundaries/{index}", item, ("statement", "reason"))

    if scope == "specialties":
        for index, result in enumerate(payload.get("results", [])):
            assessments = ((result.get("output") or {}).get("specialty_assessments") or {}).get(
                "assessments", []
            )
            for assessment_index, item in enumerate(assessments):
                add(
                    f"/results/{index}/output/specialty_assessments/assessments/{assessment_index}",
                    item,
                    ("statement", "medical_basis", "decision_impact"),
                )
    elif scope == "chair":
        chair("/result", payload.get("result"))
    elif scope == "discussion":
        for round_index, round_data in enumerate(payload.get("rounds", [])):
            round_path = f"/rounds/{round_index}"
            for response_index, response in enumerate(round_data.get("specialty_responses", [])):
                for answer_index, answer in enumerate(response.get("answers", [])):
                    answer_path = (
                        f"{round_path}/specialty_responses/{response_index}/answers/{answer_index}"
                    )
                    add(answer_path, answer, ("answer", "medical_basis", "remaining_limitation"))
                    for claim_index, claim in enumerate(answer.get("answer_claims", [])):
                        add(f"{answer_path}/answer_claims/{claim_index}", claim, ("statement",))
            chair(f"{round_path}/chair_result", round_data.get("chair_result"))
        for index, judgment in enumerate(
            (payload.get("decision_state") or {}).get("judgments", [])
        ):
            for version_index, version in enumerate(judgment.get("versions", [])):
                add(
                    f"/decision_state/judgments/{index}/versions/{version_index}/assessment",
                    version.get("assessment") or {},
                    ("statement", "medical_basis"),
                )
    else:
        report = payload.get("final_report") or {}
        clinical = report.get("clinical_report") or {}
        if clinical:
            if clinical.get("diagnostic_matrix"):
                for index, item in enumerate(clinical["diagnostic_matrix"]):
                    if item.get("dimension") in {
                        "mdt_diagnosis",
                        "radiologic_pattern",
                        "histopathologic_pattern",
                        "disease_behavior",
                    }:
                        add(
                            f"/final_report/clinical_report/diagnostic_matrix/{index}",
                            item,
                            ("statement", "medical_basis"),
                        )
            else:
                add(
                    "/final_report/clinical_report",
                    clinical,
                    ("overall_conclusion", "integrated_summary"),
                )
            for index, item in enumerate(clinical.get("secondary_judgments", [])):
                add(
                    f"/final_report/clinical_report/secondary_judgments/{index}",
                    item,
                    ("statement", "medical_basis"),
                )
        elif report:
            add("/final_report", report, ("primary_conclusion", "integrated_summary"))
    return records


def _checked_paths(items: list[Any], paths: set[str]) -> bool:
    values = [item.path for item in items]
    return len(values) == len(paths) and set(values) == paths


def _require_paths(value: Any, paths: set[str]) -> Any:
    if not _checked_paths(value.items, paths):
        raise ValueError("presentation paths changed")
    return value


def _polish(
    records: list[dict[str, Any]], config: dict[str, Any], prompts: dict[str, str]
) -> dict[str, str]:
    llm = build_llm_client(config)
    generator = StructuredLLMGenerator(
        llm,
        temperature=0.0,
        max_tokens=16000,
        max_attempts=2,
        response_format_mode="json_schema"
        if getattr(llm, "supports_json_schema", False)
        else "json_object",
    )
    original = {item["path"]: item["text"] for item in records}
    contexts = {item["path"]: item["context"] for item in records}
    approved: dict[str, str] = {}
    pending = records
    feedback: dict[str, str] = {}
    for _ in range(2):
        if not pending:
            break
        paths = {item["path"] for item in pending}
        draft, _ = generator.generate(
            schema_model=WordingBatch,
            schema_name="clinical_presentation_wording",
            system_prompt=prompts["wording"],
            user_prompt=json.dumps(
                [{**item, "修改提醒": feedback.get(item["path"], "")} for item in pending],
                ensure_ascii=False,
            ),
            extra_validation=lambda value: _require_paths(value, paths),
        )
        candidates = {}
        retry = {}
        for item in draft.items:
            source = original[item.path]
            context = contexts[item.path]
            disease = context.get("specific_disease", "")
            if Counter(_TERMS.findall(source)) != Counter(_TERMS.findall(item.text)) or (
                disease and disease in source and disease not in item.text
            ):
                retry[item.path] = "保留原文中的疾病名称、医学缩写和数值。"
            elif item.text == source:
                retry[item.path] = "在不改变医学含义的前提下，改为直接、自然的临床陈述。"
            else:
                candidates[item.path] = item.text
        if candidates:
            changed = [
                {"path": path, "original": original[path], "revised": text}
                for path, text in candidates.items()
            ]
            checks, _ = generator.generate(
                schema_model=MeaningChecks,
                schema_name="clinical_presentation_meaning_check",
                system_prompt=prompts["meaning_check"],
                user_prompt=json.dumps(changed, ensure_ascii=False),
                extra_validation=lambda value: _require_paths(value, set(candidates)),
            )
            for check in checks.items:
                if check.equivalent and check.clinical_voice:
                    approved[check.path] = candidates[check.path]
                else:
                    retry[check.path] = check.issue or "保留全部医学含义，并改成自然的临床意见。"
        pending = [item for item in pending if item["path"] in retry]
        feedback = retry
    return approved


def present(
    root: Path,
    run_dir: Path,
    scope: Literal["specialties", "chair", "discussion", "report"],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if scope in {"chair", "discussion"} and payload.get("status") == "running":
        return payload
    records = _records(scope, payload)
    if not records:
        return payload
    config_path = run_dir / "workbench_config/mdt_chair.yaml"
    config = load_yaml(
        config_path if config_path.exists() else root / "configs/agents/mdt_chair/agent.yaml"
    )
    prompts = _load_prompts()
    source_hash = sha256(
        json.dumps(
            [_VERSION, config.get("model"), records, prompts], ensure_ascii=False, sort_keys=True
        ).encode()
    ).hexdigest()
    cache_path = run_dir / f"presentation_{scope}.json"
    with _LOCK:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            cache = {}
        if cache.get("source_sha256") == source_hash:
            wording = cache.get("wording", {})
        else:
            try:
                wording = _polish(records, config, prompts)
            except Exception:
                return {**payload, "presentation_status": "unavailable"}
            temp_path = cache_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(
                    {"source_sha256": source_hash, "wording": wording}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            temp_path.replace(cache_path)
    result = deepcopy(payload)
    for path, value in wording.items():
        node: Any = result
        for part in path.strip("/").split("/")[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[path.rsplit("/", 1)[-1]] = value
    if scope == "report":
        _sync_report_fields(result)
    result["presentation_status"] = "polished" if wording else "original"
    return result


def _sync_report_fields(payload: dict[str, Any]) -> None:
    report = payload.get("final_report") or {}
    clinical = report.get("clinical_report") or {}
    matrix = clinical.get("diagnostic_matrix") or []
    diagnosis = next(
        (item for item in matrix if item.get("dimension") == "mdt_diagnosis"),
        None,
    )
    if diagnosis:
        clinical["overall_conclusion"] = diagnosis.get("statement", "")
        clinical["integrated_summary"] = diagnosis.get("medical_basis", "")

    traces = {item.get("claim_id"): item for item in report.get("reasoning_trace") or []}
    for index, item in enumerate(matrix, 1):
        trace = traces.get(f"DX{index:02d}")
        if trace:
            trace["claim_statement"] = item.get("statement", "")
            trace["medical_basis"] = item.get("medical_basis", "")
    for index, item in enumerate(clinical.get("secondary_judgments") or [], 1):
        trace = traces.get(f"SJ{index:02d}")
        if trace:
            trace["claim_statement"] = item.get("statement", "")
            trace["medical_basis"] = item.get("medical_basis", "")

    if diagnosis:
        context = [
            item.get("statement", "")
            for item in matrix
            if item.get("dimension") in {
                "radiologic_pattern",
                "histopathologic_pattern",
                "disease_behavior",
            }
            and item.get("status") != "not_applicable"
            and not (
                item.get("dimension") == "histopathologic_pattern"
                and item.get("status") == "not_assessable"
            )
        ]
        paragraphs = [[diagnosis.get("statement", ""), diagnosis.get("medical_basis", "")], context]
        paragraphs.extend(
            [item.get("statement", ""), item.get("medical_basis", "")]
            for item in clinical.get("secondary_judgments") or []
        )
        clinical["clinical_narrative"] = "\n\n".join(
            "".join(
                text.strip() if text.strip().endswith(("。", "！", "？")) else text.strip() + "。"
                for text in paragraph
                if text.strip()
            )
            for paragraph in paragraphs
            if any(text.strip() for text in paragraph)
        )
