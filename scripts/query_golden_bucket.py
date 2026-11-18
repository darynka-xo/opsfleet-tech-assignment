"""Day 3 "done when" demo: embed one sample question, retrieve top-k golden examples.

Standalone, read-only against the Qdrant store built by scripts/embed_golden_bucket.py.
Run with:
    .venv/Scripts/python.exe scripts/query_golden_bucket.py "your question here"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.llm.client import GeminiClient

QDRANT_PATH = Path(__file__).resolve().parent.parent / ".local" / "qdrant"
DEFAULT_QUESTION = "Why did sales drop for one of our product categories last quarter?"


def main(question: str) -> None:
    """Embed `question` and print the top-5 Golden Bucket matches for it."""
    load_dotenv()
    llm = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    [embedding] = llm.embed([question], model="gemini-embedding-001")

    store = QdrantGoldenBucketStore(QDRANT_PATH)
    results = store.query(embedding, top_k=5)

    print(f"Question: {question}\n")
    for i, trio in enumerate(results, start=1):
        print(f"{i}. [{trio.capability_area}] {trio.question}")
    area_count = len({t.capability_area for t in results})
    print(f"\n{len(results)} matches, spanning {area_count} capability area(s).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION)
