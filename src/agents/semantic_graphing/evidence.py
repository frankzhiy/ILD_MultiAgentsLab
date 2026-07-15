"""Deterministic evidence blocks shared by extraction and validation."""

import re

from src.schemas.semantic_graphing.clinical_proposition import EvidenceBlock
from src.schemas.semantic_graphing.graph_unit import GraphUnit


def build_evidence_blocks(unit: GraphUnit) -> list[EvidenceBlock]:
    parts = [
        match.group(0)
        for match in re.finditer(r".*?(?:[。！？；;\n]+|$)", unit.text, flags=re.DOTALL)
        if match.group(0)
    ]
    blocks: list[str] = []
    for part in parts:
        if part.strip() or not blocks:
            blocks.append(part)
        else:
            blocks[-1] += part
    if not blocks:
        raise ValueError(f"Cannot create evidence blocks for empty graph unit {unit.graph_unit_id}")
    return [
        EvidenceBlock(evidence_id=f"{unit.graph_unit_id}_ev_{index:03d}", text=text)
        for index, text in enumerate(blocks, start=1)
    ]
