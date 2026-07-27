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


def build_specialty_discussion_prompt_view(
    initial_output: dict[str, Any],
) -> dict[str, Any]:
    """Keep only the specialty's compact baseline needed to detect real changes."""

    professional = build_specialty_initial_prompt_view(initial_output)
    conclusions = professional.get("conclusions") or []
    return {
        "conclusions": [
            _select(
                conclusion,
                "conclusion_id",
                "statement",
                "status",
                "certainty",
                "medical_basis",
                "limitations",
            )
            for conclusion in conclusions
        ],
        "boundaries": professional.get("boundaries") or [],
    }


def build_issue_chair_prompt_view(
    chair_result: dict[str, Any],
    issue_id: str,
) -> dict[str, Any]:
    """Project only the current chair issue and its directly linked evidence needs."""

    issue = next(
        (
            item
            for collection in ("questions", "conflicts")
            for item in chair_result.get(collection, [])
            if item.get("question_id") == issue_id or item.get("conflict_id") == issue_id
        ),
        None,
    )
    if issue is None:
        return {}
    related_ids = set(issue.get("related_evidence_need_ids") or [])
    return {
        "issue": _project_value(issue),
        "related_evidence_needs": [
            _project_value(item)
            for item in chair_result.get("evidence_needs", [])
            if item.get("need_id") in related_ids
        ],
    }


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
