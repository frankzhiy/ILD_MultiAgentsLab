"""Read-only retrieval from the persistent guideline index."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from src.guidelines.index import COLLECTION, document_hashes
from src.guidelines.models import GuidelineChunk, GuidelineSearchHit


class GuidelineRetriever:
    def __init__(self, guideline_dir: str | Path) -> None:
        try:
            from qdrant_client import QdrantClient
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - environment boundary
            raise RuntimeError("Guideline retrieval dependencies are not installed") from exc
        self.guideline_dir = Path(guideline_dir)
        manifest_path = self.guideline_dir / "index/manifest.json"
        qdrant_path = self.guideline_dir / "index/qdrant"
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
        model_name = self.manifest["embedding_model"]
        model_path = Path(model_name)
        if not model_path.is_absolute():
            local_model = self.guideline_dir.parents[1] / model_path
            model_name = str(local_model) if local_model.exists() else model_name
        self.encoder = SentenceTransformer(model_name)
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
            limit=limit,
            with_payload=True,
        )
        return [
            GuidelineSearchHit(
                chunk=GuidelineChunk.model_validate(point.payload),
                score=float(point.score),
            )
            for point in response.points
        ]
