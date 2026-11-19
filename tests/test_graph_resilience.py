"""Graph-level tests for requirement 5 (resilience & graceful error handling).

`sql_validator.py` and `resilience.py` already have their own unit tests for the pieces
in isolation; these exercise the *graph's* self-correction and fallback wiring end to end
with a real compiled graph (only the LLM and BigQuery clients are fakes) -- a gap neither
`test_graph_delete_flow.py` (delete-only) nor the component-level tests covered.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from langgraph.checkpoint.memory import MemorySaver

from retail_agent.graph import AgentDependencies, build_graph
from retail_agent.prompts import GUARDRAIL_SYSTEM, ROUTER_SYSTEM

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"
VALID_SQL = (
    "SELECT COUNT(*) AS n FROM "
    "`opsfleet-tech-assignment.retail_analytics.fn_products_for_user`('mgr-denim')"
)
INVALID_SQL = "SELECT * FROM `some-other-project.some_dataset.products`"


class FakeGoldenBucket:
    """No retrieval needed for these tests -- always an empty Golden Bucket."""

    def query(
        self, embedding: list[float], *, top_k: int, capability_area: object = None
    ) -> list[object]:
        return []


class RecordingLLM:
    """Scripted `LLMClient`: classifies by `system_instruction`, scripts `generate_sql`'s
    SQL across calls, and never produces a `NEEDS_MORE_DATA` synthesis."""

    def __init__(self, *, sql_attempts: list[str]) -> None:
        self._sql_attempts = iter(sql_attempts)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    def generate(self, prompt: str, *, model: str, system_instruction: str | None = None) -> str:
        if system_instruction == ROUTER_SYSTEM:
            return "ANALYSIS"
        if system_instruction == GUARDRAIL_SYSTEM:
            return "SAFE"
        if system_instruction is None:
            if model.endswith("pro-latest"):
                return "Revenue is fine. No action needed."
            return next(self._sql_attempts)
        raise AssertionError(f"unexpected generate() call: {system_instruction!r}")


class FakeBQClient:
    """`deps.bq.client` -- `dry_run()` only needs this to not raise."""

    def query(self, sql: str, job_config: object = None) -> None:
        return None


class FakeBQRunner:
    """`deps.bq` -- scripts `execute_query`'s return/raise across calls."""

    def __init__(self, *, execute_results: list[Exception | pd.DataFrame]) -> None:
        self.client = FakeBQClient()
        self._results = iter(execute_results)

    def execute_query(self, sql: str) -> pd.DataFrame:
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        return result

    def get_table_schema(self, table_name: str) -> list[dict[str, str]]:
        """`schema_context.build_schema_context` needs this; content doesn't matter here."""
        return [{"name": "id", "type": "STRING"}]


@pytest.fixture
def app_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return conn


def _turn_input(
    *, app_user_id: str, session_id: str, turn_id: str, question: str
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "app_user_id": app_user_id,
        "turn_id": turn_id,
        "question": question,
        "history": [],
        "intent": None,
        "rejection_reason": None,
        "retrieved_trios": [],
        "sql": None,
        "sql_attempts": 0,
        "last_error": None,
        "needs_more_data_reason": None,
        "query_result": None,
        "query_row_count": None,
        "delete_filter_kind": None,
        "delete_filter_value": None,
        "delete_candidates": None,
        "delete_confirmed": None,
        "final_answer": None,
    }


def test_self_correction_retries_an_invalid_query_then_succeeds(app_db: sqlite3.Connection) -> None:
    """An invalid-SQL first attempt loops back to generate_sql and succeeds on retry."""
    llm = RecordingLLM(sql_attempts=[INVALID_SQL, VALID_SQL])
    deps = AgentDependencies(
        llm=llm,  # type: ignore[arg-type]
        bq=FakeBQRunner(execute_results=[pd.DataFrame([{"n": 5}])]),  # type: ignore[arg-type]
        golden_bucket=FakeGoldenBucket(),  # type: ignore[arg-type]
        app_db=app_db,
    )
    app = build_graph(deps).compile(checkpointer=MemorySaver())

    result = app.invoke(
        _turn_input(
            app_user_id="mgr-denim", session_id="s1", turn_id="t1", question="How many products?"
        ),
        config={"configurable": {"thread_id": "s1"}},
    )

    assert result["sql_attempts"] == 2
    assert result["final_answer"] == "Revenue is fine. No action needed."
    assert "I couldn't fully answer" not in (result["final_answer"] or "")


def test_persistent_bigquery_failure_degrades_to_a_graceful_fallback(
    app_db: sqlite3.Connection,
) -> None:
    """A BigQuery call that never succeeds ends in the fallback message, never a crash."""
    llm = RecordingLLM(sql_attempts=[VALID_SQL, VALID_SQL, VALID_SQL])
    always_fails = [RuntimeError("503 Service Unavailable") for _ in range(10)]
    deps = AgentDependencies(
        llm=llm,  # type: ignore[arg-type]
        bq=FakeBQRunner(execute_results=always_fails),  # type: ignore[arg-type]
        golden_bucket=FakeGoldenBucket(),  # type: ignore[arg-type]
        app_db=app_db,
    )
    app = build_graph(deps).compile(checkpointer=MemorySaver())

    result = app.invoke(
        _turn_input(
            app_user_id="mgr-denim", session_id="s2", turn_id="t1", question="How many products?"
        ),
        config={"configurable": {"thread_id": "s2"}},
    )

    assert "I couldn't fully answer" in (result["final_answer"] or "")
    assert result["sql_attempts"] == 3
