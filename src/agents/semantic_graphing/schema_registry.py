from pathlib import Path

from src.schemas.semantic_graphing import ModalityGraphSchema, SourceType
from src.utils.config import load_yaml


class ModalitySchemaRegistry:
    def __init__(self, schemas: dict[str, ModalityGraphSchema]) -> None:
        self._schemas = schemas

    @classmethod
    def from_dir(cls, schema_dir: str | Path) -> "ModalitySchemaRegistry":
        schemas: dict[str, ModalityGraphSchema] = {}
        for path in sorted(Path(schema_dir).glob("*.yaml")):
            schema = ModalityGraphSchema.model_validate(load_yaml(path))
            schemas[schema.source_type] = schema
        return cls(schemas)

    def get(self, source_type: SourceType | str) -> ModalityGraphSchema:
        key = str(source_type)
        if key not in self._schemas:
            raise KeyError(f"No modality graph schema registered for source_type={key}")
        return self._schemas[key]

    def source_types(self) -> list[str]:
        return sorted(self._schemas)

