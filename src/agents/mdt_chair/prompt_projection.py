"""Model input uses shared patient text; full source records stay in the bundle."""

from copy import deepcopy


def build_chair_prompt_view(bundle):
    texts, text_keys, references = {}, {}, {}
    for ref, evidence in bundle.evidence_registry.items():
        blocks = ([bundle.evidence_blocks[key] for key in evidence.evidence_ids]
                  if evidence.evidence_ids and all(key in bundle.evidence_blocks
                                                  for key in evidence.evidence_ids)
                  else [evidence.quote])
        references[ref] = []
        for text in blocks:
            if text not in text_keys:
                key = f"T{len(text_keys) + 1}"
                text_keys[text] = key
                texts[key] = text
            references[ref].append(text_keys[text])

    chair = deepcopy(bundle.prompt_input)
    for specialty in chair["specialties"]:
        for name in ("specialty_assessments", "discussion_answers"):
            for item in specialty.get(name, []):
                for key in ("evidence_options", "judgment_id", "version_id", "previous_version_id", "status"):
                    item.pop(key, None)
                item["evidence_relations"] = {
                    role: refs for role, refs in bundle.source_evidence[item["source_ref"]].items()
                    if refs
                }
                conditions = item.get("conditions", {})
                scope = conditions.pop("evidence_scope", {})
                limits = list(dict.fromkeys([
                    *item.get("limitations", []), *scope.get("scope_limitations", []),
                ]))
                item["limitations"] = limits
                conditions["applicability_conditions"] = [
                    value for value in dict.fromkeys(conditions.get("applicability_conditions", []))
                    if value not in limits
                ]
        for name in ("interspecialty_questions", "evidence_needs", "conditional_contributions"):
            specialty[name] = [item["source_ref"] for item in specialty.get(name, [])]

    requests, conditional = [], []
    for ref, source in bundle.source_registry.items():
        if source.source_type not in {
            "interspecialty_question", "assessment_evidence_need", "conditional_contribution",
        }:
            continue
        item = {"source_ref": ref, "specialty": source.specialty,
                "source_type": source.source_type,
                **{key: value for key, value in bundle.source_metadata[ref].items()
                   if value is not None}}
        if source.source_type == "conditional_contribution":
            conditional.append(item)
        else:
            key = "question" if source.source_type == "interspecialty_question" else "missing_information"
            item[key] = source.quote
            item["evidence_refs"] = list(dict.fromkeys(
                evidence_ref for refs in bundle.source_evidence[ref].values()
                for evidence_ref in refs
            ))
            requests.append(item)
    return {"chair_input": chair, "case_evidence": {"texts": texts, "references": references},
            "original_requests": requests, "conditional_contributions": conditional}
