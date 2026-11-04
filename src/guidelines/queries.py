"""Build retrieval questions from clinical content rather than stage instructions."""

import json


def clinical_queries(variables: dict) -> list[str]:
    groups = {"phenotype": [], "diagnosis": []}
    def visit(value, key=""):
        if isinstance(value, str):
            try:
                visit(json.loads(value), key)
                return
            except (ValueError, TypeError):
                pass
            if key in {"text", "quote", "concept_text", "assessment", "statement"}:
                groups["phenotype"].append(value)
            elif key in {"diagnosis", "leading_diagnosis", "retrieval_question"}:
                groups["diagnosis"].append(value)
        elif isinstance(value, dict):
            for name, child in value.items():
                if name not in {"case_id", "source_run_dir", "clinical_rules", "consultation", "guideline_evidence", "rationale", "limitations"}:
                    visit(child, name)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
    visit(variables)
    queries = []
    for group, prefix in [("diagnosis", "具体疾病 判别特征 鉴别诊断 "), ("phenotype", "肺部疾病 影像 生理组合 诊断鉴别 ")]:
        values = list(dict.fromkeys(groups[group]))
        text = " ".join(values)
        # Multiple windows preserve late imaging findings instead of truncating them.
        if text:
            queries.extend(prefix + text[start:start + 700] for start in range(0, len(text), 700))
    return queries
