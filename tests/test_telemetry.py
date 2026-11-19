"""Tests for the observability event log (requirement 7)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from retail_agent import telemetry

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return connection


def test_record_event_persists_a_row(conn: sqlite3.Connection) -> None:
    telemetry.record_event(
        conn,
        session_id="s1",
        turn_id="t1",
        trace_id="t1",
        node_name="generate_sql",
        duration_ms=42,
        attributes={"sql_attempts": 1},
    )

    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM agent_events").fetchone()
    assert row["session_id"] == "s1"
    assert row["turn_id"] == "t1"
    assert row["trace_id"] == "t1"
    assert row["node_name"] == "generate_sql"
    assert row["duration_ms"] == 42
    assert json.loads(row["attributes"]) == {"sql_attempts": 1}


def test_record_event_is_queryable_by_trace_id(conn: sqlite3.Connection) -> None:
    telemetry.record_event(
        conn,
        session_id="s1",
        turn_id="t1",
        trace_id="t1",
        node_name="a",
        duration_ms=1,
        attributes={},
    )
    telemetry.record_event(
        conn,
        session_id="s1",
        turn_id="t1",
        trace_id="t1",
        node_name="b",
        duration_ms=2,
        attributes={},
    )
    telemetry.record_event(
        conn,
        session_id="s1",
        turn_id="t2",
        trace_id="t2",
        node_name="a",
        duration_ms=3,
        attributes={},
    )

    rows = conn.execute(
        "SELECT node_name FROM agent_events WHERE trace_id = ? ORDER BY node_name", ("t1",)
    ).fetchall()
    assert [r[0] for r in rows] == ["a", "b"]


def test_record_event_logs_structured_json(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="retail_agent.telemetry"):
        telemetry.record_event(
            conn,
            session_id="s1",
            turn_id="t1",
            trace_id="t1",
            node_name="execute",
            duration_ms=5,
            attributes={"row_count": 3},
        )

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload["node_name"] == "execute"
    assert payload["attributes"] == {"row_count": 3}
