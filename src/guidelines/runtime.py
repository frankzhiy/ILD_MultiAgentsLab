"""Prompt injection, source resolution, and citation audit for specialty agents."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import ClassVar, Iterator

from pydantic import BaseModel

from src.guidelines.models import (
    GuidelineChunk,
    GuidelineEvidencePointer,
    GuidelineQuoteUnit,
)
from src.guidelines.retrieval import GuidelineRetriever


PROMPT_RULES = """
指南使用规则：
- 下方指南片段是外部知识，不是患者病例证据；不得用它证明患者存在某项表现。
- 只有本轮提供的 chunk_id 和 quote_unit_id 可以写入 guideline_evidence，禁止编造指南、原文、章节或页码。
- 对诊断阈值、分类定义、检查要求或指南建议作出判断时，应引用直接相关的指南片段。
- guideline_evidence 必须填写 chunk_id、quote_unit_ids、relevance 和 application；quote、字符偏移、标题和页码由程序补全。
- quote_unit_ids 必须属于对应 chunk；可选择多个直接相关单元，程序会按连续区间分别生成精确引用。
- 所选单元应共同包含直接支持当前判断的完整推荐、定义或阈值，不得只选择疾病名称、数字或短语。
- 没有适用片段时保持 guideline_evidence 为空，并在限制中说明，不得凭记忆引用。
""".strip()


_QUOTE_BOUNDARIES = frozenset(",，;；。！？!?")
_QUOTE_BRACKETS = {
    "(": ")",
    "（": "）",
    "[": "]",
    "【": "】",
}


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
        return self.prepare_query(query, limit=self.limits.get(stage, self.limit))

    def prepare_query(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> tuple[str, dict[str, GuidelineChunk], dict]:
        """Retrieve guideline context for a runtime question instead of a fixed stage."""

        query = str(query or "").strip()
        if not query:
            return "[]", {}, {"query": "", "candidates": [], "used_chunk_ids": []}
        limit = int(self.limit if limit is None else limit)
        if limit <= 0:
            return "[]", {}, {"query": query, "candidates": [], "used_chunk_ids": []}
        hits = self.retriever.search(query, guideline_ids=self.scope, limit=limit)
        chunks = {hit.chunk.chunk_id: hit.chunk for hit in hits}
        prompt_items = [
            {
                "chunk_id": hit.chunk.chunk_id,
                "section_path": hit.chunk.section_path,
                "unit_type": hit.chunk.unit_type,
                "quote_units": [
                    {
                        "quote_unit_id": unit.quote_unit_id,
                        "text": unit.text,
                    }
                    for unit in guideline_quote_units(hit.chunk)
                ],
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
    _split_noncontiguous_guideline_pointers(value, allowed)
    used = []
    for pointer in _iter_pointers(value):
        chunk = allowed.get(pointer.chunk_id)
        if chunk is None:
            raise ValueError(
                f"Guideline citation {pointer.chunk_id!r} was not retrieved for this stage"
            )
        if pointer.quote_unit_ids:
            units = guideline_quote_units(chunk)
            by_id = {unit.quote_unit_id: (index, unit) for index, unit in enumerate(units)}
            if len(pointer.quote_unit_ids) != len(set(pointer.quote_unit_ids)):
                raise ValueError(
                    f"Guideline quote units contain duplicates for {pointer.chunk_id!r}"
                )
            unknown = sorted(set(pointer.quote_unit_ids) - set(by_id))
            if unknown:
                raise ValueError(
                    f"Guideline quote units do not belong to {pointer.chunk_id!r}: {unknown}"
                )
            selected = sorted(
                (by_id[unit_id] for unit_id in pointer.quote_unit_ids),
                key=lambda item: item[0],
            )
            indexes = [index for index, _ in selected]
            if indexes != list(range(indexes[0], indexes[-1] + 1)):
                raise ValueError(
                    "Noncontiguous guideline quote units must be stored as "
                    f"separate pointers within {pointer.chunk_id!r}"
                )
            pointer.quote_unit_ids = [unit.quote_unit_id for _, unit in selected]
            start = selected[0][1].start
            end = selected[-1][1].end
            quote = chunk.text[start:end]
        else:
            quote = pointer.quote.strip()
            if not quote:
                raise ValueError(
                    f"Guideline citation {pointer.chunk_id!r} must select quote_unit_ids"
                )
            start = chunk.text.find(quote)
            if start < 0:
                raise ValueError(
                    f"Guideline quote is not an exact substring of {pointer.chunk_id!r}"
                )
            if chunk.text.find(quote, start + 1) >= 0:
                raise ValueError(f"Guideline quote is ambiguous within {pointer.chunk_id!r}")
            end = start + len(quote)
        if len(quote) < 12:
            raise ValueError(f"Guideline quote for {pointer.chunk_id!r} is too short")
        pointer.quote = quote
        pointer.quote_start = start
        pointer.quote_end = end
        pointer.guideline_id = chunk.guideline_id
        pointer.title = chunk.title
        pointer.organization = chunk.organization
        pointer.year = chunk.year
        pointer.source_file = chunk.source_file
        pointer.page = chunk.page
        pointer.section_path = chunk.section_path
        used.append(pointer.chunk_id)
    return list(dict.fromkeys(used))


def _split_noncontiguous_guideline_pointers(
    value: object,
    allowed: dict[str, GuidelineChunk],
) -> None:
    if isinstance(value, list):
        expanded = []
        for item in value:
            if not isinstance(item, GuidelineEvidencePointer):
                _split_noncontiguous_guideline_pointers(item, allowed)
                expanded.append(item)
                continue
            if not item.quote_unit_ids:
                expanded.append(item)
                continue
            if len(item.quote_unit_ids) != len(set(item.quote_unit_ids)):
                expanded.append(item)
                continue
            chunk = allowed.get(item.chunk_id)
            if chunk is None:
                expanded.append(item)
                continue
            units = guideline_quote_units(chunk)
            positions = {unit.quote_unit_id: index for index, unit in enumerate(units)}
            known_ids = [unit_id for unit_id in item.quote_unit_ids if unit_id in positions]
            if len(known_ids) != len(item.quote_unit_ids):
                expanded.append(item)
                continue
            ordered_ids = sorted(known_ids, key=positions.__getitem__)
            groups: list[list[str]] = []
            for unit_id in ordered_ids:
                if not groups or positions[unit_id] != positions[groups[-1][-1]] + 1:
                    groups.append([])
                groups[-1].append(unit_id)
            if len(groups) == 1:
                item.quote_unit_ids = groups[0]
                expanded.append(item)
            else:
                expanded.extend(
                    item.model_copy(deep=True, update={"quote_unit_ids": group})
                    for group in groups
                )
        value[:] = expanded
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            _split_noncontiguous_guideline_pointers(getattr(value, field_name), allowed)
    elif isinstance(value, dict):
        for item in value.values():
            _split_noncontiguous_guideline_pointers(item, allowed)


def guideline_evidence_schema_constraints(
    allowed: dict[str, GuidelineChunk],
) -> dict[str, list[dict[str, set[str]]]]:
    """Restrict every guideline pointer to retrieved chunk/unit pairs."""

    return {
        "guideline_evidence": [
            {
                "chunk_id": {chunk_id},
                "quote_unit_ids": {
                    unit.quote_unit_id for unit in guideline_quote_units(chunk)
                },
            }
            for chunk_id, chunk in allowed.items()
        ]
    }


def guideline_quote_units(chunk: GuidelineChunk) -> list[GuidelineQuoteUnit]:
    """Split one chunk into stable clause locators without changing source text."""

    units = []
    start = 0
    closing_brackets = []
    for index, character in enumerate(chunk.text):
        if character in _QUOTE_BRACKETS:
            closing_brackets.append(_QUOTE_BRACKETS[character])
            continue
        if closing_brackets and character == closing_brackets[-1]:
            closing_brackets.pop()
            continue
        if closing_brackets or character not in _QUOTE_BOUNDARIES:
            continue
        _append_quote_unit(units, chunk, start, index + 1)
        start = index + 1
    _append_quote_unit(units, chunk, start, len(chunk.text))
    return units


def _append_quote_unit(
    units: list[GuidelineQuoteUnit],
    chunk: GuidelineChunk,
    start: int,
    end: int,
) -> None:
    while start < end and chunk.text[start].isspace():
        start += 1
    while end > start and chunk.text[end - 1].isspace():
        end -= 1
    if start == end:
        return
    units.append(
        GuidelineQuoteUnit(
            quote_unit_id=f"{chunk.chunk_id}:q{len(units) + 1:03d}",
            chunk_id=chunk.chunk_id,
            start=start,
            end=end,
            text=chunk.text[start:end],
        )
    )


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
