"""Offline PDF ingestion and project-local Qdrant index."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import unicodedata
from uuid import NAMESPACE_URL, uuid5

from src.guidelines.catalog import load_catalog
from src.guidelines.models import GuidelineChunk


DEFAULT_MODEL = "BAAI/bge-m3"
COLLECTION = "ild_guidelines"
CHUNKER_VERSION = "atomic"
MAX_UNIT_CHARS = 800

_RECOMMENDATION = re.compile(
    r"(?:【\s*推荐意见\s*\d+\s*】|"
    r"推荐意见\s*\d+|建议|应当|应该|"
    r"应(?:由|在|对|根据|进行|考虑|一并|请|按照|尽可能|密切|接受|完善|记录|描述|明确|保持)|"
    r"\bwe\s+(?:recommend|suggest)\b|\bis\s+recommended\b|"
    r"\bpatients?\s+should\b|\bpeople\s+with\b[^.;]{0,80}\bshould\b)",
    re.IGNORECASE,
)
_DEFINITION = re.compile(
    r"(?:定义为|定义[:：]|是指|"
    r"\b(?:is|was)\s+defined\s+as\b|\bwe\s+define\b)",
    re.IGNORECASE,
)
_EXPLICIT_THRESHOLD = re.compile(
    r"(?:阈值|界值|截断值|\bthreshold\b|\bcut-?off\b)",
    re.IGNORECASE,
)
_NUMERIC_THRESHOLD = re.compile(
    r"(?:[<>≤≥]=?|至少|超过|不低于|不超过|\bat\s+least\b)\s*\d+(?:\.\d+)?\s*"
    r"(?:%|min\b|months?\b|years?\b|mg(?:/d)?\b|岁|个月|年)",
    re.IGNORECASE,
)
_THRESHOLD_CONTEXT = re.compile(
    r"(?:定义|诊断标准|分类标准|符合条件|剂量|预计值[^。]{0,40}下降|"
    r"\bdefined\b|\bcriteria\b|\bdiagnostic\s+confidence\b|"
    r"\babsolute\s+decline\b|\brelative\s+decline\b|\bdose\b)",
    re.IGNORECASE,
)
_NOISE = re.compile(
    r"(?:\bcontents\b|\bcorrespondence\b|\brequests?\s+for\s+reprints?\b|"
    r"\bscreening\s+of\s+the\s+identified\s+studies\b|"
    r"\bconsensus\s+required\b|\bguideline\s+committee\b|"
    r"\bfuture\s+research\b|\bresearch\s+question\b|"
    r"\bconditional\s+recommendations?\b|\brecommended\s+course\s+of\s+action\b|"
    r"\boverwh?elming\s+majority\b|\bobservational\s+study\b|"
    r"\bstatistically\s+significant\b|\blinear\s+no-threshold\b|"
    r"\bage\s+\.$|\b(?:table|figure)\.$|"
    r"\b\d+\s+radiological\s+evidence\b|"
    r"基金项目|创新工程|提出建议)",
    re.IGNORECASE,
)
_HEADING = re.compile(
    r"^(?:#{1,6}\s*|[一二三四五六七八九十]+、|"
    r"（[一二三四五六七八九十]+）|\d+(?:\.\d+)*[.)、]\s+)"
)
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[。！？!?])\s*|(?<=[.;])\s+(?=[A-Z0-9*“\"【（])"
)


def build_index(
    guideline_dir: str | Path,
    *,
    model_name: str = DEFAULT_MODEL,
    force: bool = False,
) -> dict:
    guideline_dir = Path(guideline_dir)
    local_bge = guideline_dir / "models/bge-m3"
    if model_name == DEFAULT_MODEL and local_bge.is_dir():
        model_name = os.path.relpath(local_bge, Path.cwd())
    catalog = load_catalog(guideline_dir / "catalog.yaml")
    processed_dir = guideline_dir / "processed"
    index_dir = guideline_dir / "index"
    qdrant_dir = index_dir / "qdrant"
    processed_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)
    hashes = document_hashes(guideline_dir)
    catalog_hash = _file_hash(guideline_dir / "catalog.yaml")
    manifest_path = index_dir / "manifest.json"
    chunks_path = processed_dir / "chunks.jsonl"
    processed_manifest_path = processed_dir / "manifest.json"
    old_manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    if (
        not force
        and old_manifest.get("document_hashes") == hashes
        and old_manifest.get("embedding_model") == model_name
        and old_manifest.get("chunker_version") == CHUNKER_VERSION
        and old_manifest.get("catalog_sha256") == catalog_hash
        and qdrant_dir.exists()
    ):
        return {**old_manifest, "status": "unchanged"}

    try:
        import pymupdf
        from qdrant_client import QdrantClient, models
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment boundary
        raise RuntimeError(
            "Guideline dependencies are missing; install pymupdf, qdrant-client, "
            "and sentence-transformers in the active environment."
        ) from exc

    chunks: list[GuidelineChunk] = []
    for document in catalog.values():
        pdf_path = guideline_dir / document.file
        pages = _extract_pages(pdf_path, pymupdf)
        chunks.extend(_chunk_document(document, pages, hashes[document.guideline_id]))
    if not chunks:
        raise ValueError("No atomic guideline units could be extracted")

    chunks_path.write_text(
        "".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for item in chunks
        ),
        encoding="utf-8",
    )
    processed_manifest_path.write_text(
        json.dumps(
            {
                "document_hashes": hashes,
                "catalog_sha256": catalog_hash,
                "chunker_version": CHUNKER_VERSION,
                "chunk_count": len(chunks),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if qdrant_dir.exists():
        shutil.rmtree(qdrant_dir)
    client = QdrantClient(path=str(qdrant_dir))
    encoder = SentenceTransformer(model_name)
    vector_size = encoder.get_embedding_dimension()
    if vector_size is None:
        raise ValueError(f"Embedding model {model_name} does not expose its vector size")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
    )
    vectors = encoder.encode_document(
        [_embedding_text(item) for item in chunks],
        batch_size=8,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    client.upsert(
        collection_name=COLLECTION,
        points=[
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, item.chunk_id)),
                vector=vector.tolist(),
                payload=item.model_dump(mode="json"),
            )
            for item, vector in zip(chunks, vectors, strict=True)
        ],
        wait=True,
    )
    client.close()

    manifest = {
        "embedding_model": model_name,
        "vector_size": vector_size,
        "chunker_version": CHUNKER_VERSION,
        "collection": COLLECTION,
        "chunk_count": len(chunks),
        "document_hashes": hashes,
        "catalog_sha256": catalog_hash,
        "updated_guideline_ids": sorted(catalog),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "status": "rebuilt"}


def _extract_pages(pdf_path: Path, pymupdf) -> list[dict]:
    pages = []
    with pymupdf.open(pdf_path) as document:
        for page in document:
            blocks = []
            for block in page.get_text("blocks"):
                if len(block) > 6 and block[6] != 0:
                    continue
                text = str(block[4] or "")
                if not text.strip():
                    continue
                blocks.append(
                    {
                        "x0": float(block[0]),
                        "y0": float(block[1]),
                        "x1": float(block[2]),
                        "y1": float(block[3]),
                        "text": text,
                    }
                )
            pages.append(
                {
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "blocks": blocks,
                }
            )
    return pages


def _chunk_document(document, pages: list[dict], document_hash: str) -> list[GuidelineChunk]:
    repeated_margins = _repeated_margin_keys(pages)
    chunks = []
    section = [document.title]
    seen = set()
    for page_number, page in enumerate(pages, start=1):
        passage = []

        def flush() -> None:
            text = _normalize_text(" ".join(passage))
            passage.clear()
            for unit_type, unit_text in _atomic_units(text):
                identity = (page_number, unit_text)
                if identity in seen:
                    continue
                seen.add(identity)
                digest = sha256(
                    f"{document.guideline_id}:{page_number}:{unit_text}".encode("utf-8")
                ).hexdigest()[:12]
                chunks.append(
                    GuidelineChunk(
                        chunk_id=f"{document.guideline_id}:p{page_number:03d}:u{digest}",
                        guideline_id=document.guideline_id,
                        title=document.title,
                        organization=document.organization,
                        year=document.year,
                        source_file=document.file,
                        page=page_number,
                        section_path=list(section),
                        unit_type=unit_type,
                        text=unit_text,
                        document_sha256=document_hash,
                    )
                )

        for block in _ordered_blocks(page, repeated_margins, document.language):
            text = _normalize_text(block["text"])
            heading = _heading(text)
            if heading:
                flush()
                section = [heading]
            else:
                passage.append(text)
        flush()
    return chunks


def _repeated_margin_keys(pages: list[dict]) -> set[str]:
    counts = Counter()
    for page in pages:
        height = float(page["height"])
        keys = {
            _margin_key(block["text"])
            for block in page["blocks"]
            if block["y1"] <= height * 0.11 or block["y0"] >= height * 0.89
        }
        counts.update(key for key in keys if key)
    threshold = max(3, math.ceil(len(pages) * 0.2))
    return {key for key, count in counts.items() if count >= threshold}


def _ordered_blocks(page: dict, repeated_margins: set[str], language: str) -> list[dict]:
    height = float(page["height"])
    width = float(page["width"])
    blocks = [
        block
        for block in page["blocks"]
        if _margin_key(block["text"]) not in repeated_margins
        and not _is_page_number(block["text"])
        and not _looks_corrupted(block["text"], language)
        and block["y1"] > height * 0.04
        and block["y0"] < height * 0.96
    ]
    midpoint = width / 2
    left = [block for block in blocks if block["x1"] <= midpoint + 12]
    right = [block for block in blocks if block["x0"] >= midpoint - 12]
    full = [block for block in blocks if block not in left and block not in right]
    if not left or not right:
        return sorted(blocks, key=lambda item: (item["y0"], item["x0"]))

    ordered = []
    remaining = left + right
    for spanning in sorted(full, key=lambda item: (item["y0"], item["x0"])):
        before = [item for item in remaining if item["y0"] < spanning["y0"]]
        ordered.extend(_column_order(before, midpoint))
        remaining = [item for item in remaining if item not in before]
        ordered.append(spanning)
    ordered.extend(_column_order(remaining, midpoint))
    return ordered


def _column_order(blocks: list[dict], midpoint: float) -> list[dict]:
    left = sorted(
        (item for item in blocks if item["x0"] < midpoint),
        key=lambda item: (item["y0"], item["x0"]),
    )
    right = sorted(
        (item for item in blocks if item["x0"] >= midpoint),
        key=lambda item: (item["y0"], item["x0"]),
    )
    return [*left, *right]


def _atomic_units(text: str) -> list[tuple[str, str]]:
    if not text or _looks_corrupted(text, ""):
        return []
    text = re.sub(
        r"([。.!?])\s*(【\s*(?:强|中等|弱)推荐\s*】)",
        r" \2\1",
        text,
    )
    units = []
    candidates = re.split(
        r"(?=(?:【\s*推荐意见\s*\d+\s*】|We\s+(?:recommend|suggest)\b|"
        r"The\s+committee\s+defined\b))",
        text,
        flags=re.IGNORECASE,
    )
    sentences = (
        sentence
        for candidate in candidates
        for sentence in _SENTENCE_BOUNDARY.split(candidate)
    )
    for sentence in filter(None, (_normalize_text(item) for item in sentences)):
        sentence = re.sub(r"\s+\d+\.$", ".", sentence)
        if (
            _NOISE.search(sentence)
            or not _complete_statement(sentence)
            or sentence.endswith(("?", "？"))
            or re.match(r"^[\u4e00-\u9fff][,，]", sentence)
            or sentence.startswith(("和", "及", "与"))
        ):
            continue
        if len(re.findall(r"[（(]\s*\d+\s*[)）]", sentence)) > 1:
            continue
        unit_type = _unit_type(sentence)
        if unit_type is None or len(sentence) < 12:
            continue
        for part in _split_long_unit(sentence):
            if len(part) >= 12 and not _looks_corrupted(part, ""):
                units.append((unit_type, part))
    return units


def _split_long_unit(text: str) -> list[str]:
    if len(text) <= MAX_UNIT_CHARS:
        return [text]
    clauses = [
        _normalize_text(item)
        for item in re.split(r"(?<=[；;])\s*", text)
        if _normalize_text(item)
    ]
    if any(len(item) > MAX_UNIT_CHARS for item in clauses):
        return []
    parts = []
    current = ""
    for clause in clauses:
        candidate = f"{current} {clause}".strip()
        if current and len(candidate) > MAX_UNIT_CHARS:
            parts.append(current)
            current = clause
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _unit_type(text: str) -> str | None:
    if _RECOMMENDATION.search(text) and _valid_recommendation(text):
        return "recommendation"
    if _DEFINITION.search(text):
        return "definition"
    if _EXPLICIT_THRESHOLD.search(text) or (
        _NUMERIC_THRESHOLD.search(text) and _THRESHOLD_CONTEXT.search(text)
    ):
        return "threshold"
    return None


def _valid_recommendation(text: str) -> bool:
    if re.search(
        r"(?:【\s*推荐意见\s*\d+\s*】|"
        r"\bwe\s+(?:recommend|suggest)\b|\bis\s+recommended\b|"
        r"\bpatients?\s+should\b|\bpeople\s+with\b[^.;]{0,80}\bshould\b)",
        text,
        re.IGNORECASE,
    ):
        return True
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    return bool(
        re.match(
            r"^(?:\d+[.)、]\s*)?(?:建议|推荐|应当|应该|不建议|患者|所有|"
            r"对于|对|如|若|当|在|参照|依据|由于|早期|尽管)",
            text,
        )
        or "不建议" in text
        or "指南建议" in text
        or re.search(
            r"应(?:由|在|对|根据|进行|考虑|一并|请|按照|尽可能|密切|"
            r"接受|完善|记录|描述|明确|保持)",
            text,
        )
    )


def _complete_statement(text: str) -> bool:
    return text.endswith(("。", "！", "？", ".", "!", "?", "】", ")"))


def _heading(text: str) -> str | None:
    text = text.lstrip("# ").strip()
    if len(text) > 120 or not text:
        return None
    if _HEADING.match(text):
        return text
    letters = [character for character in text if character.isalpha()]
    latin_letters = [character for character in letters if character.isascii()]
    if (
        text.isupper()
        and letters
        and len(latin_letters) / len(letters) >= 0.7
    ):
        return text
    return None


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)


def _margin_key(text: str) -> str:
    text = _normalize_text(text).lower()
    return re.sub(r"\d+", "#", text)


def _is_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"[\s·•—-]*\d+[\s·•—-]*", _normalize_text(text)))


def _looks_corrupted(text: str, language: str) -> bool:
    if "�" in text or "|---|" in text:
        return True
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return False
    controls = sum(
        unicodedata.category(character).startswith("C")
        for character in visible
        if character not in "\n\t"
    )
    symbols = sum(unicodedata.category(character).startswith("S") for character in visible)
    if controls / len(visible) > 0.01 or symbols / len(visible) > 0.25:
        return True
    if language.startswith("zh"):
        unexpected = sum(
            any(
                script in unicodedata.name(character, "")
                for script in ("ARABIC", "THAI", "HEBREW", "CYRILLIC", "KHMER")
            )
            for character in visible
        )
        if unexpected > 2:
            return True
    return False


def _embedding_text(chunk: GuidelineChunk) -> str:
    return "\n".join(
        (
            chunk.title,
            " > ".join(chunk.section_path),
            chunk.unit_type,
            chunk.text,
        )
    )


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def document_hashes(guideline_dir: str | Path) -> dict[str, str]:
    guideline_dir = Path(guideline_dir)
    catalog = load_catalog(guideline_dir / "catalog.yaml")
    return {
        item.guideline_id: _file_hash(guideline_dir / item.file) for item in catalog.values()
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_chunks(path: Path) -> list[GuidelineChunk]:
    return [
        GuidelineChunk.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
