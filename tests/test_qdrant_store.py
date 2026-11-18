"""Offline, deterministic tests for QdrantGoldenBucketStore (no network, no real Gemini).

Uses synthetic low-dimensional vectors instead of real embeddings -- what's under test
here is the store's own logic (status filtering, the diversity cap), not embedding
quality, which needs the real Gemini model and is covered separately as an integration
test (test_golden_bucket_integration.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retail_agent.golden_bucket.models import GoldenBucketTrio
from retail_agent.golden_bucket.qdrant_store import EMBEDDING_DIM, QdrantGoldenBucketStore


def _trio(trio_id: str, area: str, status: str = "approved") -> GoldenBucketTrio:
    return GoldenBucketTrio(
        trio_id=trio_id,
        question=f"question {trio_id}",
        sql_query=f"SELECT 1 -- {trio_id}",
        report_summary=f"report {trio_id}",
        capability_area=area,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        created_at="2026-11-19",
    )


def _vector(near: float) -> list[float]:
    """A unit-ish vector whose first component controls similarity rank; rest is zero."""
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = near
    vec[1] = (1 - near**2) ** 0.5
    return vec


@pytest.fixture
def store(tmp_path: Path) -> QdrantGoldenBucketStore:
    """A fresh local Qdrant store seeded with 6 trios: 4 in one area, 2 in another."""
    s = QdrantGoldenBucketStore(tmp_path / "qdrant")
    # All near the query vector (near=1.0), but decreasingly so.
    s.upsert(_trio("a1", "customer_behavior"), _vector(0.99))
    s.upsert(_trio("a2", "customer_behavior"), _vector(0.97))
    s.upsert(_trio("a3", "customer_behavior"), _vector(0.95))
    s.upsert(_trio("a4", "customer_behavior"), _vector(0.93))
    s.upsert(_trio("b1", "product_performance"), _vector(0.91))
    s.upsert(_trio("b2", "product_performance"), _vector(0.89))
    return s


def test_query_only_returns_approved_trios(tmp_path: Path) -> None:
    """A candidate (unapproved) trio is never returned, however similar it is."""
    s = QdrantGoldenBucketStore(tmp_path / "qdrant")
    s.upsert(_trio("approved-1", "customer_behavior", status="approved"), _vector(0.5))
    s.upsert(_trio("candidate-1", "customer_behavior", status="candidate"), _vector(0.99))

    results = s.query(_vector(1.0), top_k=5)

    assert [t.trio_id for t in results] == ["approved-1"]


def test_query_applies_a_diversity_cap_per_capability_area(store: QdrantGoldenBucketStore) -> None:
    """The top-4 nearest are all customer_behavior, but the cap keeps results diverse."""
    results = store.query(_vector(1.0), top_k=4)

    areas = [t.capability_area for t in results]
    assert areas.count("customer_behavior") <= 2
    assert "product_performance" in areas


def test_query_filters_by_capability_area_when_given(store: QdrantGoldenBucketStore) -> None:
    """Restricting to one capability area excludes every other area's trios."""
    results = store.query(_vector(1.0), top_k=5, capability_area="product_performance")

    assert results
    assert all(t.capability_area == "product_performance" for t in results)


def test_query_respects_top_k(store: QdrantGoldenBucketStore) -> None:
    """Never more results than requested, even with plenty of matching candidates."""
    results = store.query(_vector(1.0), top_k=2)

    assert len(results) == 2
