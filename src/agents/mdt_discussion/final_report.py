from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_discussion.models import DiscussionRound, MDTFinalReport
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/mdt_discussion/final_report.md"


class FinalReportAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        config: dict[str, Any],
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.prompt = load_text(PROMPT_PATH)
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            response_format_mode=(
                "json_schema" if getattr(llm, "supports_json_schema", False) else "json_object"
            ),
            event_callback=event_callback,
        )

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        llm: LLMClient,
        *,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "FinalReportAgent":
        return cls(
            llm,
            config=load_yaml(config_path),
            event_callback=event_callback,
        )

    def generate(
        self,
        *,
        case_id: str,
        chair_result: dict[str, Any],
        rounds: list[DiscussionRound],
        stop_reason: str,
    ) -> tuple[MDTFinalReport, dict[str, Any]]:
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(MDTFinalReport)
        )
        round_summary = [
            {
                "round_number": item.round_number,
                "specialties": [response.specialty for response in item.specialty_responses],
                "answers": [
                    {
                        "specialty": response.specialty,
                        "issue_id": answer.issue_id,
                        "answer": answer.answer,
                        "confidence": answer.confidence,
                        "remaining_limitation": answer.remaining_limitation,
                    }
                    for response in item.specialty_responses
                    for answer in response.answers
                ],
            }
            for item in rounds
        ]
        prompt = render_template(
            self.prompt,
            {
                "stop_reason": stop_reason,
                "chair_result": prompt_json(chair_result),
                "rounds": prompt_json(round_summary),
                "output_schema": output_schema,
            },
        )

        def resolve(result: MDTFinalReport) -> MDTFinalReport:
            result.case_id = case_id
            result.discussion_rounds = len(rounds)
            return result

        return self.generator.generate(
            schema_model=MDTFinalReport,
            schema_name="mdt_final_report",
            system_prompt=(
                "你是以呼吸科为主要背景的 ILD MDT 主持人，负责在讨论结束后形成统一报告。"
                "忠实保留证据边界和未解决分歧，只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=resolve,
        )

