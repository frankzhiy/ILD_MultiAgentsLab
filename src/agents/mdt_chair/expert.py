"""Expert review uses existing evidence registries and the existing discussion loop."""

from hashlib import sha256
import json

from src.agents.common.judgment_protocol import judgment_system_prompt
from src.agents.common.team_synthesis import TeamSynthesis, MDTChairResult, synthesis_acceptance_status
from src.llm.prompting import build_shared_prompt_view, prompt_json, prompt_schema_json
from src.utils.config import render_template
from src.guidelines.runtime import resolve_guideline_evidence, guideline_evidence_schema_constraints, PROMPT_RULES


def synthesis_hash(synthesis):
    data = synthesis.model_dump(mode="json", exclude={"snapshot_hash", "stop_kind", "acceptance", "acceptance_records"})
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def integrate_expert(agent, bundle, previous=None, responses=None, reviews=None):
    current = {
        item["source_ref"]: item
        for specialty in bundle.prompt_input["specialties"]
        for item in specialty["specialty_assessments"]
    }
    valid_refs = set(current)
    for specialty in bundle.prompt_input["specialties"]:
        for key in ("discussion_answers", "interspecialty_questions", "evidence_needs"):
            valid_refs.update(item["source_ref"] for item in specialty.get(key, []) if "source_ref" in item)
    question_refs = {ref for ref, source in bundle.source_registry.items()
                     if source.source_type == "interspecialty_question"}
    need_refs_to_cover = {ref for ref, source in bundle.source_registry.items()
                         if source.source_type == "assessment_evidence_need"}
    valid_refs.update(question_refs | need_refs_to_cover)
    evidence_refs = set(bundle.evidence_registry)
    constraints = {
        name: {"source_refs": valid_refs, "evidence_refs": evidence_refs}
        for name in ("ClinicalProblem", "ClinicalIssue", "DiagnosticFacet", "KeyEvidenceNeed", "JudgmentBoundary", "SpecialtyPosition", "IssueDisposition")
    }
    constraints["JudgmentReview"] = {"source_ref": set(current), "evidence_refs": evidence_refs}
    constraints["DiagnosticCandidate"] = {"source_refs": valid_refs,
        "supporting_evidence_refs": evidence_refs, "opposing_evidence_refs": evidence_refs}
    previous_team = previous.team_synthesis if previous else None
    answers = [answer for response in responses or [] for answer in response.answers]
    answer_ids = {answer.answer_id for answer in answers}
    answer_ids.update(item["assessment_id"] for specialty in bundle.prompt_input["specialties"]
                      for item in specialty.get("discussion_answers", []))
    answer_issues = {bundle.source_metadata[ref]["assessment_id"]: bundle.source_metadata[ref]["answered_question_id"]
                     for ref in valid_refs if bundle.source_registry[ref].source_type == "discussion_answer"}
    answer_issues.update({answer.answer_id: answer.issue_id for answer in answers})
    constraints["IssueDisposition"]["response_refs"] = answer_ids
    context = {
        "previous": previous_team.model_dump(mode="json") if previous_team else None,
        "answers": [a.model_dump(mode="json") for a in answers],
        "requester_reviews": [r.model_dump(mode="json") for r in reviews or []],
        "original_requests": [{**bundle.source_registry[ref].model_dump(mode="json"),
                               **bundle.source_metadata[ref]}
                              for ref in sorted(question_refs | need_refs_to_cover)],
    }
    query = "肺部疾病多学科诊断 鉴别病因 " + " ".join(
        item["statement"][:100] for item in current.values()
    )[:900]
    if previous_team:
        query += " " + " ".join(i.question for i in previous_team.issues)[:500]
    guidelines, allowed_guidelines, guideline_trace = (
        agent.guideline_runtime.prepare_query(query) if agent.guideline_runtime else ("[]", {}, {})
    )
    shared = build_shared_prompt_view({
        "chair_input": bundle.prompt_input,
        "case_evidence": [{"evidence_ref": ref, "quote": e.quote}
                          for ref, e in bundle.evidence_registry.items()],
        "discussion_context": context,
    })
    prompt = render_template(agent.prompt, {
        **{key: prompt_json(value) for key, value in shared["input"].items()},
        "guideline_context": guidelines + "\n" + PROMPT_RULES,
        "output_schema": prompt_schema_json(TeamSynthesis),
    })
    prompt += "\n共享输入值目录与展开规则：\n" + prompt_json({
        key: value for key, value in shared.items() if key != "input"
    })

    def validate(team):
        guideline_trace["used_chunk_ids"] = resolve_guideline_evidence(team, allowed_guidelines)
        coverage_errors = []
        reviewed = {review.source_ref for review in team.judgment_reviews}
        if reviewed != set(current):
            obsolete = [index for index, review in enumerate(team.judgment_reviews)
                        if review.source_ref not in current]
            coverage_errors.append(f"Review every active formal judgment exactly once; missing={sorted(set(current)-reviewed)}, obsolete={sorted(reviewed-set(current))}; "
                             f"/judgment_reviews obsolete indexes={obsolete}; allowed source_ref={sorted(current)}. "
                             "If missing is empty, merge any distinct information into the correct active review "
                             "and remove obsolete entries; do not rename or renumber other reviews.")
        need_refs = [ref for need in team.evidence_needs for ref in need.source_refs
                     if ref in need_refs_to_cover]
        if set(need_refs) != need_refs_to_cover or len(need_refs) != len(set(need_refs)):
            coverage_errors.append("Every specialty evidence need must appear exactly once in evidence_needs, including deferred needs; "
                                   f"missing source_refs={sorted(need_refs_to_cover-set(need_refs))}; "
                                   f"duplicate source_refs={sorted(ref for ref in set(need_refs) if need_refs.count(ref) > 1)}")
        for problem in team.problems:
            if problem.limitations and not any(b.problem_id == problem.problem_id for b in team.judgment_boundaries):
                coverage_errors.append(f"State the decision-relevant judgment boundary for problem {problem.problem_id}")
        if coverage_errors:
            raise ValueError("; ".join(coverage_errors))
        def walk(value):
            if isinstance(value, dict):
                for key, items in value.items():
                    if key == "source_refs" and set(items) - valid_refs:
                        raise ValueError(f"Unknown or obsolete source refs: {set(items)-valid_refs}")
                    if key.endswith("evidence_refs") and set(items) - set(bundle.evidence_registry):
                        raise ValueError(f"Unknown evidence refs: {set(items)-set(bundle.evidence_registry)}")
                    walk(items)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(team.model_dump(mode="json"))
        dispositions = {d.issue_id: d for d in team.issue_dispositions}
        open_ids = {i.issue_id for i in team.issues}
        for review in team.judgment_reviews:
            if review.required_response and not any(
                review.source_ref in i.source_refs
                and bundle.source_registry[review.source_ref].specialty in i.target_specialties
                for i in team.issues
            ):
                raise ValueError(f"Required response needs an issue routed to its author: {review.source_ref}")
        for old in previous_team.issues if previous_team else []:
            disposition = dispositions.get(old.issue_id)
            if disposition is None:
                raise ValueError(f"Missing disposition for previous issue {old.issue_id}")
            if disposition.outcome == "continue" and old.issue_id not in open_ids:
                raise ValueError(f"Continuing issue must retain its ID: {old.issue_id}")
            if disposition.outcome != "continue" and old.issue_id in open_ids:
                raise ValueError(
                    f"Issue {old.issue_id} has outcome={disposition.outcome!r} but remains in issues. "
                    "Remove its open entry if discussion is finished, or change its disposition "
                    "to 'continue' if further discussion is needed. Do not append a duplicate issue."
                )
            if disposition.outcome in {"answered", "bounded"}:
                matching = {a.answer_id for a in answers if a.issue_id == old.issue_id}
                if not matching.intersection(disposition.response_refs):
                    raise ValueError(f"Closure needs an actual response for {old.issue_id}")
        for disposition in team.issue_dispositions:
            if set(disposition.response_refs) - answer_ids:
                raise ValueError("Disposition references an unknown round answer")
        for ref in question_refs:
            if ref not in dispositions:
                raise ValueError(f"Missing disposition for specialty request {ref}")
        for disposition in team.issue_dispositions:
            if disposition.outcome == "covered" and not disposition.source_refs:
                raise ValueError(f"Covered request needs actual specialty sources: {disposition.issue_id}")
            if disposition.outcome in {"continue", "merged"} and not set(disposition.linked_issue_ids).intersection(open_ids):
                raise ValueError(f"Route request to an explicit open issue: {disposition.issue_id}")
            if disposition.issue_id in question_refs and disposition.outcome == "answered":
                if not disposition.response_refs:
                    raise ValueError("Use covered for pre-existing opinions; answered requires actual discussion responses")
        for question_ref in question_refs:
            handled = dispositions[question_ref]
            if handled.outcome in {"continue", "merged"} and not any(
                issue.issue_id in handled.linked_issue_ids and question_ref in issue.source_refs
                for issue in team.issues
            ):
                raise ValueError(f"Linked discussion issue must preserve its original question source: {question_ref}")
            if handled.outcome == "answered":
                related = {question_ref, *handled.linked_issue_ids}
                if previous_team:
                    related.update(i.issue_id for i in previous_team.issues if question_ref in i.source_refs)
                    related.update(link for d in previous_team.issue_dispositions if d.issue_id == question_ref
                                   for link in d.linked_issue_ids)
                    related.update(answer_issues.get(answer) for d in previous_team.issue_dispositions
                                   if d.issue_id == question_ref and d.outcome == "answered" for answer in d.response_refs)
                related.discard(None)
                if not any(answer_issues.get(ref) in related for ref in handled.response_refs):
                    raise ValueError(f"Answered request must cite an answer to its linked issue: {question_ref}")
            if handled.outcome == "covered" and not any(
                ref in current and bundle.source_registry[ref].specialty == bundle.source_metadata[question_ref]["target_specialty"]
                for ref in handled.source_refs
            ):
                raise ValueError(f"Covered request needs the target specialty's actual judgment: {question_ref}")
        if previous_team and {n.need_id for n in previous_team.evidence_needs} - {n.need_id for n in team.evidence_needs}:
            raise ValueError("Keep prior evidence need IDs and update their status/action instead of dropping them")
        for disagreement in team.disagreements:
            if disagreement.status == "open" and not set(disagreement.linked_issue_ids).intersection(open_ids):
                raise ValueError("An open disagreement requires a routed discussion issue")
            for position in disagreement.positions:
                if any(bundle.source_registry[r].specialty != position.specialty for r in position.source_refs):
                    raise ValueError("Disagreement positions must cite their own specialty")
        for issue in team.issues:
            if issue.origin == "specialty":
                requesters = {bundle.source_registry[r].specialty for r in issue.source_refs
                              if r in question_refs}
                if not set(issue.raised_by) <= requesters:
                    raise ValueError("Specialty issue requesters must match the cited original questions")
        # Carry closed history forward so changing an ID cannot erase prior closure.
        if previous_team:
            current_ids = set(dispositions)
            team.issue_dispositions.extend(d.model_copy(deep=True) for d in previous_team.issue_dispositions
                                           if d.issue_id not in current_ids and d.outcome not in {"continue", "merged"})
        team.revision = (previous_team.revision if previous_team else 0) + 1
        team.dependency_versions = {ref: item.get("version_id", "") for ref, item in current.items()}
        team.source_catalog = {**(previous_team.source_catalog if previous_team else {}),
                               **{ref: bundle.source_registry[ref].model_dump(mode="json") for ref in valid_refs}}
        for ref in valid_refs:
            team.source_catalog[ref].update(
                **bundle.source_metadata[ref],
                evidence_relations=bundle.source_evidence[ref],
                guideline_evidence=[p.model_dump(mode="json") for p in bundle.source_guidelines[ref]],
            )
        for ref in question_refs:
            team.source_catalog[ref]["target_specialty"] = bundle.source_metadata[ref]["target_specialty"]
        for ref in valid_refs:
            if bundle.source_registry[ref].source_type == "discussion_answer":
                team.source_catalog[ref]["answer_id"] = bundle.source_metadata[ref]["assessment_id"]
        for specialty in bundle.prompt_input["specialties"]:
            for question in specialty.get("interspecialty_questions", []):
                team.source_catalog[question["source_ref"]].update(
                    quote=question["question"], target_specialty=question["target_specialty"])
        team.evidence_catalog = {ref: item.model_dump(mode="json") for ref, item in bundle.evidence_registry.items()}
        team.acceptance = synthesis_acceptance_status(team)
        team.snapshot_hash = synthesis_hash(team)
        return team

    team, trace = agent.generator.generate(
        schema_model=TeamSynthesis, schema_name="mdt_expert_synthesis",
        system_prompt=judgment_system_prompt("你是ILD多学科会诊的资深呼吸科主持人，负责审查专业意见并形成团队临床综合。只返回JSON。"),
        user_prompt=prompt + "\n当前正式判断审阅编号（每项恰好一次，不连续、不重编号；原始问题与资料需求不进入 judgment_reviews）：\n" + prompt_json(sorted(current)),
        extra_validation=validate, repair_on_validation_error=True,
        string_field_constraints=constraints,
        pointer_field_constraints=guideline_evidence_schema_constraints(allowed_guidelines),
    )
    result = MDTChairResult(case_id=bundle.case_id, team_synthesis=team)
    trace["guideline_retrieval"] = guideline_trace
    trace["synthesis_revision"] = team.revision
    return result, trace
