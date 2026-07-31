"""Compact MDT discussion inputs without changing stored audit data."""

from __future__ import annotations

from typing import Any


def build_chair_prompt_view(chair_result: dict[str, Any]) -> dict[str, Any]:
    """Keep chair semantics while compacting repeated provenance expansions."""

    return _project_value(chair_result)


def build_specialty_initial_prompt_view(
    initial_output: dict[str, Any],
) -> dict[str, Any]:
    """Expose only the specialty's two formal initial-output sections."""

    assessments = initial_output.get("specialty_assessments")
    questions = initial_output.get("interspecialty_questions")
    if isinstance(assessments, dict) and isinstance(questions, dict):
        return {
            "specialty_assessments": assessments,
            "interspecialty_questions": questions,
        }
    legacy = initial_output.get("professional_conclusions")
    if not isinstance(legacy, dict):
        raise ValueError("Specialty initial output is missing specialty_assessments")
    return {
        "specialty_assessments": {
            "specialty_question": legacy.get("specialty_question"),
            "assessability": legacy.get("assessability"),
            "assessments": legacy.get("conclusions") or [],
            "evidence_gaps": legacy.get("evidence_gaps") or [],
            "boundaries": legacy.get("boundaries") or [],
        },
        "interspecialty_questions": {
            "questions": legacy.get("interspecialty_questions") or []
        },
    }


def build_specialty_discussion_prompt_view(
    initial_output: dict[str, Any],
) -> dict[str, Any]:
    """Keep only the specialty's compact baseline needed to detect real changes."""

    formal = build_specialty_initial_prompt_view(initial_output)
    assessments = formal["specialty_assessments"]
    items = assessments.get("assessments") or assessments.get("conclusions") or []
    return {
        "specialty_assessments": [
            _select(
                assessment,
                "assessment_id",
                "conclusion_id",
                "statement",
                "status",
                "certainty",
                "medical_basis",
                "limitations",
            )
            for assessment in items
        ],
        "boundaries": assessments.get("boundaries") or [],
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
                _select(
                    citation,
                    "source_ref",
                    "specialty",
                    "source_type",
                    "source_subtype",
                )
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
                        *(
                            (
                                "target_claim_id",
                                "relation",
                                "rationale",
                                "comparison_target",
                            )
                            if role == "links"
                            else ()
                        ),
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
