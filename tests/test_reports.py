"""Tests for the Saved Reports repository (requirement 3). Offline, deterministic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retail_agent import reports

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return connection


def test_create_report_is_findable_by_session(conn: sqlite3.Connection) -> None:
    reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="Q3 denim sales", body="...", turn_id="t1"
    )

    found = reports.find_candidates_by_session(conn, app_user_id="u1", session_id="s1")
    assert len(found) == 1
    assert found[0]["title"] == "Q3 denim sales"


def test_find_candidates_by_session_excludes_other_sessions(conn: sqlite3.Connection) -> None:
    reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )
    reports.create_report(
        conn, session_id="s2", app_user_id="u1", title="B", body="...", turn_id="t2"
    )

    found = reports.find_candidates_by_session(conn, app_user_id="u1", session_id="s1")
    assert [r["title"] for r in found] == ["A"]


def test_find_candidates_by_session_excludes_other_users(conn: sqlite3.Connection) -> None:
    reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )

    found = reports.find_candidates_by_session(conn, app_user_id="u2", session_id="s1")
    assert found == []


def test_find_candidates_by_mention_matches_title_or_body(conn: sqlite3.Connection) -> None:
    reports.create_report(
        conn,
        session_id="s1",
        app_user_id="u1",
        title="Calvin Klein report",
        body="n/a",
        turn_id="t1",
    )
    reports.create_report(
        conn,
        session_id="s1",
        app_user_id="u1",
        title="Other",
        body="mentions Calvin Klein here",
        turn_id="t2",
    )
    reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="Unrelated", body="n/a", turn_id="t3"
    )

    found = reports.find_candidates_by_mention(conn, app_user_id="u1", mention="Calvin Klein")
    assert len(found) == 2


def test_find_candidates_by_mention_excludes_other_users(conn: sqlite3.Connection) -> None:
    reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="Calvin Klein", body="n/a", turn_id="t1"
    )

    found = reports.find_candidates_by_mention(conn, app_user_id="u2", mention="Calvin Klein")
    assert found == []


def test_confirm_delete_soft_deletes_and_is_excluded_from_future_lookups(
    conn: sqlite3.Connection,
) -> None:
    report_id = reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )

    reports.confirm_delete(conn, report_ids=[report_id], app_user_id="u1", turn_id="t2")

    assert reports.list_active_reports(conn, app_user_id="u1") == []
    row = conn.execute(
        "SELECT deleted_at FROM saved_reports WHERE report_id = ?", (report_id,)
    ).fetchone()
    assert row[0] is not None


def test_confirm_delete_cannot_delete_another_users_report(conn: sqlite3.Connection) -> None:
    report_id = reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )

    reports.confirm_delete(conn, report_ids=[report_id], app_user_id="u2", turn_id="t2")

    row = conn.execute(
        "SELECT deleted_at FROM saved_reports WHERE report_id = ?", (report_id,)
    ).fetchone()
    assert row[0] is None
    assert len(reports.list_active_reports(conn, app_user_id="u1")) == 1


def test_log_delete_cancelled_does_not_touch_the_report(conn: sqlite3.Connection) -> None:
    report_id = reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )

    reports.log_delete_cancelled(conn, report_ids=[report_id], app_user_id="u1", turn_id="t2")

    assert len(reports.list_active_reports(conn, app_user_id="u1")) == 1


def test_audit_log_records_create_request_and_confirm(conn: sqlite3.Connection) -> None:
    report_id = reports.create_report(
        conn, session_id="s1", app_user_id="u1", title="A", body="...", turn_id="t1"
    )
    reports.log_delete_requested(conn, report_ids=[report_id], app_user_id="u1", turn_id="t2")
    reports.confirm_delete(conn, report_ids=[report_id], app_user_id="u1", turn_id="t2")

    actions = [
        row[0]
        for row in conn.execute(
            "SELECT action FROM report_audit_log WHERE report_id = ? ORDER BY occurred_at",
            (report_id,),
        ).fetchall()
    ]
    assert actions == ["created", "delete_requested", "delete_confirmed"]
