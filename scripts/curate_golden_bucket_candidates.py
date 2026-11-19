"""Surface new Golden Bucket candidates from positively-rated turns (requirement 4b).

The system-level learning loop: a 👍-rated analysis turn is a real (question, SQL, report)
example an analyst actually found useful, but it is never auto-added to the Golden Bucket
-- PLANNING.md's Day 6 scope is explicit that this loop is "deliberately human-gated."
This script only *proposes* candidates, written to `data/golden_bucket_candidates.yaml`
with `status: candidate`; a human reviews them, and only an edit that moves an entry into
`data/golden_bucket_trios.yaml` with `status: approved` (followed by re-running
`scripts/embed_golden_bucket.py`) makes it a real few-shot example.

Novelty check: a 👍-rated question nearly identical to an already-approved trio isn't a
useful new example, however positively it was rated, so each candidate's embedding is
compared against the nearest approved trio (`QdrantGoldenBucketStore.best_match_similarity`)
and dropped above `NOVELTY_THRESHOLD`.

Run with:
    .venv/Scripts/python.exe scripts/curate_golden_bucket_candidates.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from retail_agent import feedback
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.llm.client import GeminiClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

APP_DB_PATH = Path(__file__).resolve().parent.parent / ".local" / "app_state.sqlite3"
QDRANT_PATH = Path(__file__).resolve().parent.parent / ".local" / "qdrant"
CANDIDATES_FILE = Path(__file__).resolve().parent.parent / "data" / "golden_bucket_candidates.yaml"

# Cosine similarity above this to the nearest approved trio means "not novel enough" --
# chosen as a clearly-duplicate cutoff, not tuned against a labeled set (there isn't one
# yet); revisit once Day 6's eval harness has produced enough real similarity scores to
# calibrate against.
NOVELTY_THRESHOLD = 0.90

_CAPABILITY_AREA_SYSTEM = """Classify a retail-analytics question into exactly one
capability area. Reply with exactly one word, nothing else: customer_behavior,
product_performance, time_based, or schema_meta."""


def _classify_capability_area(llm: GeminiClient, question: str) -> str:
    """Best-effort capability-area tag for a candidate -- a human reviews it anyway."""
    reply = llm.generate(
        question, model="gemini-flash-latest", system_instruction=_CAPABILITY_AREA_SYSTEM
    )
    choice = reply.strip().lower()
    valid = {"customer_behavior", "product_performance", "time_based", "schema_meta"}
    return choice if choice in valid else "schema_meta"


def main() -> None:
    """Propose new Golden Bucket candidates from 👍-rated feedback, for human review."""
    load_dotenv()
    if not APP_DB_PATH.exists():
        logger.info("No app-state database at %s -- no feedback to curate yet.", APP_DB_PATH)
        return

    conn = sqlite3.connect(APP_DB_PATH)
    rows = feedback.list_positive_candidates(conn)
    logger.info("Found %d positively-rated turn(s) with both SQL and a report", len(rows))
    if not rows:
        return

    llm = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    store = QdrantGoldenBucketStore(QDRANT_PATH)

    existing: list[dict[str, object]] = []
    if CANDIDATES_FILE.exists():
        existing = yaml.safe_load(CANDIDATES_FILE.read_text(encoding="utf-8")) or []
    already_proposed = {c["question"] for c in existing}

    new_candidates: list[dict[str, object]] = []
    for row in rows:
        question = row["question"]
        if question in already_proposed:
            continue
        [embedding] = llm.embed([question], model="gemini-embedding-001")
        similarity = store.best_match_similarity(embedding)
        if similarity is not None and similarity >= NOVELTY_THRESHOLD:
            logger.info(
                "Skipping %r -- too similar to an existing trio (%.2f)", question, similarity
            )
            continue
        new_candidates.append(
            {
                "trio_id": f"cb-cand-{uuid.uuid4().hex[:8]}",
                "capability_area": _classify_capability_area(llm, question),
                "status": "candidate",
                "created_at": datetime.now(UTC).date().isoformat(),
                "question": question,
                "sql_query": row["sql_query"],
                "report_summary": row["report_body"],
                "source_feedback_id": row["feedback_id"],
            }
        )
        logger.info("Proposed candidate: %r", question)

    if not new_candidates:
        logger.info("No novel candidates to propose.")
        return

    all_candidates = existing + new_candidates
    CANDIDATES_FILE.write_text(
        yaml.safe_dump(all_candidates, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    logger.info(
        "Wrote %d new candidate(s) (%d total) to %s -- review by hand before promoting any "
        "into data/golden_bucket_trios.yaml with status: approved.",
        len(new_candidates),
        len(all_candidates),
        CANDIDATES_FILE,
    )


if __name__ == "__main__":
    main()
