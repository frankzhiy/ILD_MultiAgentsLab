"""Compact MDT discussion inputs without changing stored audit data."""

from __future__ import annotations

from typing import Any


def build_chair_prompt_view(chair_result: dict[str, Any]) -> dict[str, Any]:
    """Keep chair semantics while compacting repeated provenance expansions."""

    return _project_value(chair_result)


def build_specialty_initial_prompt_view(
    initial_output: dict[str, Any],
) -> dict[str, Any]:
    """Expose only the specialty's formal initial conclusions during discussion."""

    professional = initial_output.get("professional_conclusions")
    if not isinstance(professional, dict):
        raise ValueError("Specialty initial output is missing professional_conclusions")
    return professional


def _project_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_project_value(item) for item in value]
    if not isinstance(value, dict):
        return value

    projected = {}
    for key, item in value.items():
        if key == "source_citations":
            projected[key] = [
                _select(citation, "source_ref", "specialty", "source_type")
                for citation in item or []
            ]
        elif key == "evidence":
            projected[key] = {
                role: [
                    _select(
                        citation,
                        "evidence_ref",
                        "graph_unit_id",
                        "evidence_ids",
                        "proposition_ids",
                    )
                    for citation in citations or []
                ]
                for role, citations in (item or {}).items()
            }
        elif key == "guideline_evidence":
            projected[key] = [
                _select(pointer, "chunk_id", "relevance", "application")
                for pointer in item or []
            ]
        else:
            projected[key] = _project_value(item)
    return projected


def _select(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}
