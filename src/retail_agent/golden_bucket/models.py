"""Data shapes for the Golden Bucket (requirement 1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityArea = Literal["customer_behavior", "product_performance", "time_based", "schema_meta"]
TrioStatus = Literal["approved", "candidate"]


@dataclass(frozen=True)
class GoldenBucketTrio:
    """One human-curated (question, SQL, report) example, or a Day 6 candidate for one."""

    trio_id: str
    question: str
    sql_query: str
    report_summary: str
    capability_area: CapabilityArea
    status: TrioStatus
    created_at: str
