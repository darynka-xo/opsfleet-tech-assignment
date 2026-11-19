"""Tests for the destructive-ops graph flow (requirement 5): delete-parse, candidate
resolution, the `interrupt()` confirmation gate, and soft-delete persistence.

Offline and deterministic: a `FakeLLM` stands in for Gemini (no network, no model
nondeterminism) while the graph, the checkpointer, and the app-state SQLite database are
all real -- this exercises the actual LangGraph pause/resume mechanics, not a mock of them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from retail_agent import reports
from retail_agent.graph import AgentDependencies, build_graph
from retail_agent.prompts import DELETE_PARSE_SYSTEM, GUARDRAIL_SYSTEM, ROUTER_SYSTEM

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"


class FakeLLM:
    """A scripted `LLMClient`: routes by `system_instruction`, no network involved."""

    def __init__(self, *, delete_reply: str = "SESSION") -> None:
        self.delete_reply = delete_reply

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise AssertionError("embed() should not be called on the delete-only path")

    def generate(self, prompt: str, *, model: str, system_instruction: str | None = None) -> str:
        if system_instruction == ROUTER_SYSTEM:
            return "DELETE"
        if system_instruction == GUARDRAIL_SYSTEM:
            return "SAFE"
        if system_instruction == DELETE_PARSE_SYSTEM:
            return self.delete_reply
        raise AssertionError(f"unexpected generate() call: {system_instruction!r}")


@pytest.fixture
def app_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return conn


def _base_turn_input(
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


def _build_app(app_db: sqlite3.Connection, *, delete_reply: str = "SESSION"):
    deps = AgentDependencies(
        llm=FakeLLM(delete_reply=delete_reply),
        bq=object(),  # type: ignore[arg-type]  # never touched on the delete-only path
        golden_bucket=object(),  # type: ignore[arg-type]
        app_db=app_db,
    )
    graph = build_graph(deps)
    return graph.compile(checkpointer=MemorySaver())


def test_delete_with_no_matching_reports_skips_confirmation(app_db: sqlite3.Connection) -> None:
    app = _build_app(app_db)
    config = {"configurable": {"thread_id": "s1"}}

    result = app.invoke(
        _base_turn_input(
            app_user_id="u1", session_id="s1", turn_id="t1", question="delete my reports"
        ),
        config=config,
    )

    assert "__interrupt__" not in result
    assert "didn't find" in (result["final_answer"] or "")


def test_delete_pauses_for_confirmation_and_deletes_on_yes(app_db: sqlite3.Connection) -> None:
    reports.create_report(
        app_db, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t0"
    )
    app = _build_app(app_db)
    config = {"configurable": {"thread_id": "s1"}}

    result = app.invoke(
        _base_turn_input(
            app_user_id="u1",
            session_id="s1",
            turn_id="t1",
            question="delete all reports from this chat",
        ),
        config=config,
    )

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["count"] == 1
    assert payload["candidates"][0]["title"] == "A"

    result = app.invoke(Command(resume=True), config=config)

    assert "__interrupt__" not in result
    assert "Deleted 1" in (result["final_answer"] or "")
    assert reports.list_active_reports(app_db, app_user_id="u1") == []


def test_delete_pauses_for_confirmation_and_cancels_on_no(app_db: sqlite3.Connection) -> None:
    reports.create_report(
        app_db, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t0"
    )
    app = _build_app(app_db)
    config = {"configurable": {"thread_id": "s1"}}

    app.invoke(
        _base_turn_input(
            app_user_id="u1",
            session_id="s1",
            turn_id="t1",
            question="delete all reports from this chat",
        ),
        config=config,
    )
    result = app.invoke(Command(resume=False), config=config)

    assert result["final_answer"] == "Okay, I didn't delete anything."
    assert len(reports.list_active_reports(app_db, app_user_id="u1")) == 1


def test_delete_by_mention_only_resolves_matching_reports(app_db: sqlite3.Connection) -> None:
    reports.create_report(
        app_db, session_id="s1", app_user_id="u1", title="Nike report", body="...", turn_id="t0"
    )
    reports.create_report(
        app_db, session_id="s1", app_user_id="u1", title="Adidas report", body="...", turn_id="t0"
    )
    app = _build_app(app_db, delete_reply="MENTION: Nike")
    config = {"configurable": {"thread_id": "s1"}}

    result = app.invoke(
        _base_turn_input(
            app_user_id="u1",
            session_id="s1",
            turn_id="t1",
            question="delete the reports mentioning Nike",
        ),
        config=config,
    )

    payload = result["__interrupt__"][0].value
    assert payload["count"] == 1
    assert payload["candidates"][0]["title"] == "Nike report"

    result = app.invoke(Command(resume=True), config=config)
    remaining = reports.list_active_reports(app_db, app_user_id="u1")
    assert [r["title"] for r in remaining] == ["Adidas report"]


def test_a_user_cannot_delete_another_users_reports(app_db: sqlite3.Connection) -> None:
    reports.create_report(
        app_db, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t0"
    )
    app = _build_app(app_db)
    config = {"configurable": {"thread_id": "s2"}}

    result = app.invoke(
        _base_turn_input(
            app_user_id="u2",
            session_id="s1",
            turn_id="t1",
            question="delete all reports from this chat",
        ),
        config=config,
    )

    assert "__interrupt__" not in result
    assert "didn't find" in (result["final_answer"] or "")
    assert len(reports.list_active_reports(app_db, app_user_id="u1")) == 1
