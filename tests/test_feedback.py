"""Tests for the feedback repository (requirement 4b / 6)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retail_agent import feedback

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return connection


def test_record_feedback_persists_a_row(conn: sqlite3.Connection) -> None:
    feedback.record_feedback(
        conn,
        app_user_id="u1",
        session_id="s1",
        turn_id="t1",
        rating="up",
        question="Top products?",
        sql_query="SELECT 1",
        report_body="Report text.",
    )

    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM feedback").fetchone()
    assert row["app_user_id"] == "u1"
    assert row["rating"] == "up"
    assert row["question"] == "Top products?"


def test_list_positive_candidates_excludes_down_votes(conn: sqlite3.Connection) -> None:
    feedback.record_feedback(
        conn,
        app_user_id="u1",
        session_id="s1",
        turn_id="t1",
        rating="up",
        question="Q1",
        sql_query="SELECT 1",
        report_body="R1",
    )
    feedback.record_feedback(
        conn,
        app_user_id="u1",
        session_id="s1",
        turn_id="t2",
        rating="down",
        question="Q2",
        sql_query="SELECT 2",
        report_body="R2",
    )

    candidates = feedback.list_positive_candidates(conn)
    assert [c["question"] for c in candidates] == ["Q1"]


def test_list_positive_candidates_excludes_rows_with_no_sql(conn: sqlite3.Connection) -> None:
    feedback.record_feedback(
        conn,
        app_user_id="u1",
        session_id="s1",
        turn_id="t1",
        rating="up",
        question="What's the weather?",
        sql_query=None,
        report_body=None,
    )

    assert feedback.list_positive_candidates(conn) == []
