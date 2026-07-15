"""Offline PDF ingestion and project-local Qdrant index."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from src.guidelines.catalog import load_catalog
from src.guidelines.models import GuidelineChunk


DEFAULT_MODEL = "BAAI/bge-m3"
COLLECTION = "ild_guidelines"
CHUNKER_VERSION = "page-paragraph-v1"


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
        and old_manifest.get("catalog_sha256", catalog_hash) == catalog_hash
        and qdrant_dir.exists()
    ):
        return {**old_manifest, "status": "unchanged"}

    try:
        import pymupdf4llm
        from qdrant_client import QdrantClient, models
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment boundary
        raise RuntimeError(
            "Guideline dependencies are missing; install pymupdf4llm, qdrant-client, "
            "and sentence-transformers in the active environment."
        ) from exc

    compatible = (
        not force
        and old_manifest.get("embedding_model") == model_name
        and old_manifest.get("chunker_version") == CHUNKER_VERSION
        and old_manifest.get("catalog_sha256", catalog_hash) == catalog_hash
        and qdrant_dir.exists()
        and chunks_path.exists()
    )
    old_chunks = _read_chunks(chunks_path) if compatible else []
    old_hashes = old_manifest.get("document_hashes", {}) if compatible else {}
    changed_ids = {
        guideline_id
        for guideline_id in set(old_hashes) | set(hashes)
        if old_hashes.get(guideline_id) != hashes.get(guideline_id)
    }
    target_ids = changed_ids if compatible else set(catalog)
    new_chunks: list[GuidelineChunk] = []
    processed_manifest = (
        _read_json(processed_manifest_path) if processed_manifest_path.exists() else {}
    )
    extraction_cached = (
        not compatible
        and chunks_path.exists()
        and processed_manifest.get("document_hashes") == hashes
        and processed_manifest.get("catalog_sha256") == catalog_hash
        and processed_manifest.get("chunker_version") == CHUNKER_VERSION
    )
    if extraction_cached:
        new_chunks = _read_chunks(chunks_path)
    else:
        for document in catalog.values():
            if document.guideline_id not in target_ids:
                continue
            pdf_path = guideline_dir / document.file
            pages = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
            new_chunks.extend(_chunk_document(document, pages, hashes[document.guideline_id]))
    chunks = [item for item in old_chunks if item.guideline_id not in changed_ids] + new_chunks
    if not chunks:
        raise ValueError("No guideline text could be extracted")
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

    if not compatible and qdrant_dir.exists():
        import shutil

        shutil.rmtree(qdrant_dir)
    client = QdrantClient(path=str(qdrant_dir))
    encoder = None
    if compatible:
        vector_size = int(old_manifest["vector_size"])
        for guideline_id in changed_ids:
            client.delete(
                collection_name=COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="guideline_id", match=models.MatchValue(value=guideline_id)
                            )
                        ]
                    )
                ),
                wait=True,
            )
    else:
        encoder = SentenceTransformer(model_name)
        vector_size = encoder.get_embedding_dimension()
        if vector_size is None:
            raise ValueError(f"Embedding model {model_name} does not expose its vector size")
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
    if new_chunks:
        encoder = encoder or SentenceTransformer(model_name)
        vectors = encoder.encode_document(
            [item.text for item in new_chunks],
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
                for item, vector in zip(new_chunks, vectors, strict=True)
            ],
            wait=True,
        )
    client.close()

    manifest = {
        "schema_version": "guideline-index.v1",
        "embedding_model": model_name,
        "vector_size": vector_size,
        "chunker_version": CHUNKER_VERSION,
        "collection": COLLECTION,
        "chunk_count": len(chunks),
        "document_hashes": hashes,
        "catalog_sha256": catalog_hash,
        "updated_guideline_ids": sorted(target_ids),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "status": "rebuilt"}


def _chunk_document(document, pages: list[dict], document_hash: str) -> list[GuidelineChunk]:
    chunks = []
    for page_number, page in enumerate(pages, start=1):
        text = (page.get("text") or "").strip()
        section = []
        part_number = 0
        for part in _split_text(text):
            heading = _heading(part)
            if heading:
                section = [heading]
                continue
            if len(part) < 40:
                continue
            part_number += 1
            chunks.append(
                GuidelineChunk(
                    chunk_id=f"{document.guideline_id}:p{page_number:03d}:c{part_number:03d}",
                    guideline_id=document.guideline_id,
                    title=document.title,
                    organization=document.organization,
                    year=document.year,
                    source_file=document.file,
                    page=page_number,
                    section_path=section,
                    text=part,
                    document_sha256=document_hash,
                )
            )
    return chunks


def _split_text(text: str, max_chars: int = 1600) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", text)]
    parts: list[str] = []
    current = ""
    for paragraph in filter(None, paragraphs):
        if len(current) + len(paragraph) + 1 <= max_chars:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            parts.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            parts.extend(paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars))
            current = ""
    if current:
        parts.append(current)
    return parts


def _heading(text: str) -> str | None:
    first = text.splitlines()[0].strip()
    if first.startswith("#"):
        return first.lstrip("# ")
    return None


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
