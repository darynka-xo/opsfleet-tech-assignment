"""Embed data/golden_bucket_trios.yaml and load it into the local Qdrant store.

Idempotent (upsert by trio_id). Run with:
    .venv/Scripts/python.exe scripts/embed_golden_bucket.py
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from retail_agent.golden_bucket.models import GoldenBucketTrio
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.llm.client import GeminiClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TRIOS_FILE = Path(__file__).resolve().parent.parent / "data" / "golden_bucket_trios.yaml"
QDRANT_PATH = Path(__file__).resolve().parent.parent / ".local" / "qdrant"


def load_trios(path: Path) -> list[GoldenBucketTrio]:
    """Parse the Golden Bucket YAML seed file into trio objects.

    Args:
        path: Path to the YAML file (a list of trio records).
    """
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        GoldenBucketTrio(
            trio_id=r["trio_id"],
            question=r["question"],
            sql_query=r["sql_query"],
            report_summary=r["report_summary"],
            capability_area=r["capability_area"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in records
    ]


def main() -> None:
    """Embed every trio's question and upsert it into the local Qdrant store."""
    load_dotenv()
    trios = load_trios(TRIOS_FILE)
    logger.info("Loaded %d trios from %s", len(trios), TRIOS_FILE)

    llm = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    embeddings = llm.embed([t.question for t in trios], model="gemini-embedding-001")
    logger.info("Embedded %d questions (dim=%d)", len(embeddings), len(embeddings[0]))

    store = QdrantGoldenBucketStore(QDRANT_PATH)
    for trio, embedding in zip(trios, embeddings, strict=True):
        store.upsert(trio, embedding)
    logger.info("Upserted %d trios into Qdrant at %s", len(trios), QDRANT_PATH)


if __name__ == "__main__":
    main()
