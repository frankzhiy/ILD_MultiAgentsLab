"""Canonical guideline registry."""

from pathlib import Path

from src.guidelines.models import GuidelineDocument
from src.utils.config import load_yaml


def load_catalog(path: str | Path) -> dict[str, GuidelineDocument]:
    path = Path(path)
    documents = [GuidelineDocument.model_validate(item) for item in load_yaml(path).get("guidelines", [])]
    indexed = {item.guideline_id: item for item in documents}
    if len(indexed) != len(documents):
        raise ValueError("Guideline catalog contains duplicate guideline_id values")
    for document in documents:
        source = path.parent / document.file
        if not source.is_file():
            raise FileNotFoundError(f"Guideline PDF is missing: {source}")
    return indexed
