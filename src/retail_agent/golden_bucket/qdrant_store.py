"""Qdrant adapter for the `VectorStore` interface (ADR-003)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Condition as QdrantCondition
from qdrant_client.models import Distance as QdrantDistance
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from retail_agent.golden_bucket.models import CapabilityArea, GoldenBucketTrio

logger = logging.getLogger(__name__)

COLLECTION_NAME = "golden_bucket"
EMBEDDING_DIM = 3072  # gemini-embedding-001's default output size (ADR-003 validation).

# Over-fetch this many candidates per requested result before applying the diversity
# constraint, so there's enough of a pool to actually diversify from.
_OVERFETCH_FACTOR = 4

# Qdrant's local mode only accepts an integer or a UUID as a point id (confirmed by
# actually running it -- an arbitrary string like "cb-001" raises ValueError). Trio ids
# are human-chosen strings (data/golden_bucket_trios.yaml), so each is mapped to a
# deterministic UUID5: the same trio_id always produces the same point id, which is what
# makes re-running scripts/embed_golden_bucket.py an upsert rather than a duplicate.
_TRIO_ID_NAMESPACE = uuid.UUID("3b1f6f1a-6e2e-4a7e-9f0a-2f7a6c7e1d4b")


def _point_id(trio_id: str) -> str:
    """Map a human-readable trio id to the deterministic UUID Qdrant requires."""
    return str(uuid.uuid5(_TRIO_ID_NAMESPACE, trio_id))


class QdrantGoldenBucketStore:
    """Embedded/local-mode Qdrant store for Golden Bucket trios."""

    def __init__(self, path: str | Path) -> None:
        """Open (creating if needed) a local Qdrant collection at `path`.

        Args:
            path: Directory for Qdrant's local storage (gitignored, e.g. `.local/qdrant`).
        """
        self._client = QdrantClient(path=str(path))
        if not self._client.collection_exists(COLLECTION_NAME):
            self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=QdrantDistance.COSINE),
            )
            logger.info("Created Qdrant collection %s", COLLECTION_NAME)

    def upsert(self, trio: GoldenBucketTrio, embedding: list[float]) -> None:
        """Insert or replace one trio and its embedding."""
        self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=_point_id(trio.trio_id),
                    vector=embedding,
                    payload={
                        "trio_id": trio.trio_id,
                        "question": trio.question,
                        "sql_query": trio.sql_query,
                        "report_summary": trio.report_summary,
                        "capability_area": trio.capability_area,
                        "status": trio.status,
                        "created_at": trio.created_at,
                    },
                )
            ],
        )

    def query(
        self,
        embedding: list[float],
        *,
        top_k: int,
        capability_area: CapabilityArea | None = None,
    ) -> list[GoldenBucketTrio]:
        """Return the top-k most similar *approved* trios, with a diversity constraint.

        Over-fetches by `_OVERFETCH_FACTOR`, then greedily selects by similarity while
        capping how many results may share one `capability_area` -- so a tight cluster of
        near-duplicate trios in one area can't crowd out every other kind of example.
        """
        status_condition = FieldCondition(key="status", match=MatchValue(value="approved"))
        must: list[QdrantCondition] = [status_condition]
        if capability_area is not None:
            must.append(
                FieldCondition(key="capability_area", match=MatchValue(value=capability_area))
            )

        hits = self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=embedding,
            query_filter=Filter(must=must),
            limit=top_k * _OVERFETCH_FACTOR,
        ).points

        per_area_cap = max(1, (top_k + 1) // 2)
        area_counts: dict[str, int] = {}
        selected: list[GoldenBucketTrio] = []
        for hit in hits:
            if len(selected) >= top_k:
                break
            payload = hit.payload or {}
            area = payload["capability_area"]
            if area_counts.get(area, 0) >= per_area_cap:
                continue
            area_counts[area] = area_counts.get(area, 0) + 1
            selected.append(
                GoldenBucketTrio(
                    trio_id=payload["trio_id"],
                    question=payload["question"],
                    sql_query=payload["sql_query"],
                    report_summary=payload["report_summary"],
                    capability_area=area,
                    status=payload["status"],
                    created_at=payload["created_at"],
                )
            )
        return selected

    def best_match_similarity(self, embedding: list[float]) -> float | None:
        """Cosine similarity to the single nearest *approved* trio, or `None` if empty.

        Used by `scripts/curate_golden_bucket_candidates.py` (requirement 4b) as a novelty
        check: a 👍-rated question that's nearly identical to an already-approved trio
        isn't a useful new example, however positively it was rated.
        """
        status_condition = FieldCondition(key="status", match=MatchValue(value="approved"))
        hits = self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=embedding,
            query_filter=Filter(must=[status_condition]),
            limit=1,
        ).points
        return hits[0].score if hits else None
