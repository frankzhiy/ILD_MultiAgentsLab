"""Compact MDT discussion inputs without changing stored audit data."""

from __future__ import annotations

from typing import Any
from src.agents.common.clinical_concepts import LEGACY_JUDGMENT_NOTE


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
    raise ValueError("Specialty initial output is missing current formal sections")

def build_specialty_discussion_prompt_view(
    initial_output: dict[str, Any],
) -> dict[str, Any]:
    """Keep only the specialty's compact baseline needed to detect real changes."""

    formal = build_specialty_initial_prompt_view(initial_output)
    assessments = formal["specialty_assessments"]
    items = [
        item
        for item in (
            assessments.get("assessments") or []
        )
        if item.get("origin") != "discussion_answer"
    ]
    return {
        "specialty_assessments": [
            {**_select(
                assessment,
                "assessment_id",
                "judgment_id",
                "version_id",
                "statement",
                "assessability",
                "direction",
                "confidence",
                "clinical_role",
                "medical_basis",
                "limitations",
                "conditions",
            ), **({"legacy_semantics": LEGACY_JUDGMENT_NOTE} if assessment.get("status") else {})}
            for assessment in items
        ],
        "boundaries": assessments.get("boundaries") or [],
    }


def build_issue_chair_prompt_view(
    chair_result: dict[str, Any],
    issue_id: str,
) -> dict[str, Any]:
    """Project only the current chair issue and its directly linked evidence needs."""

    team = chair_result.get("team_synthesis")
    if not team:
        raise ValueError("MDT discussion requires the current team synthesis")
    issue = next((i for i in team["issues"] if i["issue_id"] == issue_id), None)
    if issue is None:
        source = team["source_catalog"].get(issue_id)
        if source and source.get("source_type") == "interspecialty_question":
            issue = source
    return {"issue": issue, "judgment_boundaries": team["judgment_boundaries"],
            "disagreements": team["disagreements"], "evidence_needs": team["evidence_needs"]}


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
