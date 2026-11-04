"""Read-only retrieval from the persistent guideline index."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from src.guidelines.index import COLLECTION, CHUNKER_VERSION, document_hashes
from src.guidelines.models import GuidelineChunk, GuidelineSearchHit


class GuidelineRetriever:
    def __init__(self, guideline_dir: str | Path) -> None:
        try:
            from qdrant_client import QdrantClient
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - environment boundary
            raise RuntimeError("Guideline retrieval dependencies are not installed") from exc
        self.guideline_dir = Path(guideline_dir)
        manifest_path = self.guideline_dir / "index" / CHUNKER_VERSION / "manifest.json"
        qdrant_path = self.guideline_dir / "index" / CHUNKER_VERSION / "qdrant"
        if not manifest_path.is_file() or not qdrant_path.is_dir():
            raise FileNotFoundError(
                "Guideline index is missing. Run scripts/guidelines/build_guideline_index.py first."
            )
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        catalog_hash = sha256((self.guideline_dir / "catalog.yaml").read_bytes()).hexdigest()
        if self.manifest.get("catalog_sha256", catalog_hash) != catalog_hash:
            raise RuntimeError("Guideline catalog changed after indexing. Rebuild the guideline index.")
        if self.manifest.get("document_hashes") != document_hashes(self.guideline_dir):
            raise RuntimeError(
                "Guideline PDFs or catalog changed after indexing. Rebuild the guideline index."
            )
        from rank_bm25 import BM25Okapi
        self.chunks = [GuidelineChunk.model_validate_json(line) for line in
                       (self.guideline_dir / "processed" / CHUNKER_VERSION / "chunks.jsonl").read_text().splitlines()
                       if line.strip()]
        self.lexical = BM25Okapi([_tokens(c.text) for c in self.chunks])
        model_name = self.manifest["embedding_model"]
        model_path = Path(model_name)
        if not model_path.is_absolute():
            local_model = self.guideline_dir.parents[1] / model_path
            model_name = str(local_model) if local_model.exists() else model_name
        self.encoder = SentenceTransformer(model_name, device="cpu")
        self.client = QdrantClient(path=str(qdrant_path))

    def search(
        self,
        query: str,
        *,
        guideline_ids: list[str],
        limit: int = 6,
    ) -> list[GuidelineSearchHit]:
        from qdrant_client import models

        vector = self.encoder.encode_query(query, normalize_embeddings=True).tolist()
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="guideline_id",
                    match=models.MatchAny(any=guideline_ids),
                )
            ]
        )
        response = self.client.query_points(
            collection_name=COLLECTION,
            query=vector,
            query_filter=query_filter,
            limit=max(limit * 3, 20),
            with_payload=True,
        )
        scores, candidates = {}, {}
        for rank, point in enumerate(response.points, 1):
            candidate = GuidelineChunk.model_validate(point.payload)
            candidates[candidate.chunk_id] = candidate
            scores[candidate.chunk_id] = 1 / (60 + rank)
        lexical_scores = self.lexical.get_scores(_tokens(query))
        lexical = sorted(((float(score), c) for score, c in zip(lexical_scores, self.chunks)
                          if c.guideline_id in guideline_ids and score > 0),
                         key=lambda pair: pair[0], reverse=True)[:max(limit * 3, 20)]
        for rank, (_, candidate) in enumerate(lexical, 1):
            key = candidate.chunk_id
            candidates[key] = candidate
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        keys = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [GuidelineSearchHit(chunk=candidates[key], score=scores[key]) for key in keys]


def _tokens(text: str) -> list[str]:
    import re
    tokens = re.findall(r"[a-z][a-z0-9-]*", text.lower())
    for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(sequence[i:i + 2] for i in range(max(1, len(sequence) - 1)))
    return tokens
