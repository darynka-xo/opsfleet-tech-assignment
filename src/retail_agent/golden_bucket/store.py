"""The `VectorStore` interface: the seam a vector-store swap goes through (ADR-003)."""

from __future__ import annotations

from typing import Protocol

from retail_agent.golden_bucket.models import CapabilityArea, GoldenBucketTrio


class VectorStore(Protocol):
    """What any vector-store adapter must implement for the Golden Bucket."""

    def upsert(self, trio: GoldenBucketTrio, embedding: list[float]) -> None:
        """Insert or replace one trio and its embedding."""
        ...

    def query(
        self,
        embedding: list[float],
        *,
        top_k: int,
        capability_area: CapabilityArea | None = None,
    ) -> list[GoldenBucketTrio]:
        """Return the top-k most similar *approved* trios for a query embedding.

        Args:
            embedding: The query's embedding vector.
            top_k: Maximum number of trios to return.
            capability_area: If given, only consider trios from this capability area.

        Returns:
            Trios ordered by similarity, with a diversity constraint applied so the
            result isn't just k near-duplicates of the same trio (ADR-003 / Day 3 task).
        """
        ...
