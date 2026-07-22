"""Prompt injection, source resolution, and citation audit for specialty agents."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import ClassVar, Iterator

from pydantic import BaseModel

from src.guidelines.models import GuidelineChunk, GuidelineEvidencePointer
from src.guidelines.retrieval import GuidelineRetriever


PROMPT_RULES = """
指南使用规则：
- 下方指南片段是外部知识，不是患者病例证据；不得用它证明患者存在某项表现。
- 只有本轮提供的 chunk_id 可以写入 guideline_evidence，禁止编造指南、原文、章节或页码。
- 对诊断阈值、分类定义、检查要求或指南建议作出判断时，应引用直接相关的指南片段。
- guideline_evidence 只填写 chunk_id、relevance 和 application；标题、页码和原文由程序补全。
- 没有适用片段时保持 guideline_evidence 为空，并在限制中说明，不得凭记忆引用。
""".strip()


class GuidelineRuntime:
    _retrievers: ClassVar[dict[Path, GuidelineRetriever]] = {}
    _retrievers_lock: ClassVar[Lock] = Lock()

    def __init__(
        self,
        retriever: GuidelineRetriever,
        scope: list[str],
        queries: dict,
        limit: int = 6,
        limits: dict | None = None,
    ):
        self.retriever = retriever
        self.scope = scope
        self.queries = queries
        self.limit = limit
        self.limits = limits or {}

    @classmethod
    def from_config(cls, config: dict) -> "GuidelineRuntime | None":
        settings = config.get("guideline_retrieval") or {}
        if not settings.get("enabled", False):
            return None
        directory = Path(settings.get("directory", "data/guidelines"))
        if not directory.is_absolute():
            directory = Path(__file__).resolve().parents[2] / directory
        directory = directory.resolve()
        with cls._retrievers_lock:
            retriever = cls._retrievers.get(directory)
            if retriever is None:
                retriever = GuidelineRetriever(directory)
                cls._retrievers[directory] = retriever
        return cls(
            retriever,
            list(settings.get("scope") or []),
            dict(settings.get("queries") or {}),
            int(settings.get("limit", 6)),
            dict(settings.get("limits") or {}),
        )

    def prepare(self, stage: str) -> tuple[str, dict[str, GuidelineChunk], dict]:
        query = str(self.queries.get(stage) or "").strip()
        if not query:
            return "[]", {}, {"query": "", "candidates": [], "used_chunk_ids": []}
        limit = int(self.limits.get(stage, self.limit))
        if limit <= 0:
            return "[]", {}, {"query": query, "candidates": [], "used_chunk_ids": []}
        hits = self.retriever.search(query, guideline_ids=self.scope, limit=limit)
        chunks = {hit.chunk.chunk_id: hit.chunk for hit in hits}
        prompt_items = [
            {
                "chunk_id": hit.chunk.chunk_id,
                "section_path": hit.chunk.section_path,
                "text": hit.chunk.text,
            }
            for hit in hits
        ]
        trace = {
            "query": query,
            "scope": self.scope,
            "candidates": [
                {"chunk_id": hit.chunk.chunk_id, "score": hit.score} for hit in hits
            ],
            "used_chunk_ids": [],
        }
        return json.dumps(
            prompt_items,
            ensure_ascii=False,
            separators=(",", ":"),
        ), chunks, trace


def resolve_guideline_evidence(value: object, allowed: dict[str, GuidelineChunk]) -> list[str]:
    used = []
    for pointer in _iter_pointers(value):
        chunk = allowed.get(pointer.chunk_id)
        if chunk is None:
            raise ValueError(
                f"Guideline citation {pointer.chunk_id!r} was not retrieved for this stage"
            )
        pointer.guideline_id = chunk.guideline_id
        pointer.title = chunk.title
        pointer.organization = chunk.organization
        pointer.year = chunk.year
        pointer.source_file = chunk.source_file
        pointer.page = chunk.page
        pointer.section_path = chunk.section_path
        pointer.quote = chunk.text
        used.append(pointer.chunk_id)
    return list(dict.fromkeys(used))


def guideline_evidence_schema_constraints(
    allowed: dict[str, GuidelineChunk],
) -> dict[str, list[dict[str, set[str]]]]:
    """Restrict every guideline pointer to chunks retrieved for this stage."""

    return {"guideline_evidence": [{"chunk_id": set(allowed)}]}


def _iter_pointers(value: object) -> Iterator[GuidelineEvidencePointer]:
    if isinstance(value, GuidelineEvidencePointer):
        yield value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from _iter_pointers(getattr(value, field_name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_pointers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_pointers(item)
