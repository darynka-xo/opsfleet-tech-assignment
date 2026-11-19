"""Saved Reports repository (requirement 3): create, list, and soft-delete.

Every write is a parameterized query (`?` placeholders), never an f-string-built one --
AGENTS.md's Security section calls this out explicitly, since report titles/bodies carry
user-influenced free text. A `sqlite3.Connection` is passed in, not opened here, so tests
can point this at a throwaway `:memory:` database.

Simplification from the original schema design: `saved_reports.client_mentions` is kept
in the DDL but not populated -- "mentioning Client X" is resolved with a `LIKE` match
against `title`/`body` directly at delete time instead of a separate extraction step.
Simpler, and this dataset has no real "client" entity to extract anyway (brand names are
the closest analog); a real client-tagging feature would re-introduce the column.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

_INSERT_AUDIT_LOG = (
    "INSERT INTO report_audit_log (audit_id, report_id, action, app_user_id, turn_id, occurred_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _audit_rows(
    report_ids: list[str], action: str, *, app_user_id: str, turn_id: str, now: str
) -> list[tuple[str, str, str, str, str, str]]:
    return [(str(uuid.uuid4()), rid, action, app_user_id, turn_id, now) for rid in report_ids]


def create_report(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    app_user_id: str,
    title: str,
    body: str,
    turn_id: str,
) -> str:
    """Save a new report and record its creation in the audit log.

    Args:
        conn: An open connection to the app-state SQLite database.
        session_id: The conversation this report was produced in.
        app_user_id: The authenticated user who owns this report.
        title: A short report title (the originating question works well).
        body: The full synthesized report text.
        turn_id: The turn that produced this report, for the audit trail.

    Returns:
        The new report's id.
    """
    report_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO saved_reports "
        "(report_id, session_id, app_user_id, title, body, client_mentions, created_at, "
        "deleted_at, deleted_by_turn) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, NULL)",
        (report_id, session_id, app_user_id, title, body, now),
    )
    conn.execute(
        _INSERT_AUDIT_LOG, (str(uuid.uuid4()), report_id, "created", app_user_id, turn_id, now)
    )
    conn.commit()
    return report_id


def find_candidates_by_session(
    conn: sqlite3.Connection, *, app_user_id: str, session_id: str
) -> list[sqlite3.Row]:
    """Active (non-deleted) reports for this user, scoped to one session.

    Always filtered by `app_user_id` first -- a user can never resolve, confirm, or see
    another user's reports, by construction, not by a separate permission check.
    """
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM saved_reports "
        "WHERE app_user_id = ? AND session_id = ? AND deleted_at IS NULL ORDER BY created_at",
        (app_user_id, session_id),
    )
    return cursor.fetchall()


def find_candidates_by_mention(
    conn: sqlite3.Connection, *, app_user_id: str, mention: str
) -> list[sqlite3.Row]:
    """Active (non-deleted) reports for this user whose title or body mentions `mention`.

    Always filtered by `app_user_id` first, same as `find_candidates_by_session`.
    """
    conn.row_factory = sqlite3.Row
    like_pattern = f"%{mention}%"
    cursor = conn.execute(
        "SELECT * FROM saved_reports WHERE app_user_id = ? AND deleted_at IS NULL "
        "AND (title LIKE ? OR body LIKE ?) ORDER BY created_at",
        (app_user_id, like_pattern, like_pattern),
    )
    return cursor.fetchall()


def list_active_reports(conn: sqlite3.Connection, *, app_user_id: str) -> list[sqlite3.Row]:
    """Every active (non-deleted) report for this user, most recent first."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM saved_reports "
        "WHERE app_user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
        (app_user_id,),
    )
    return cursor.fetchall()


def log_delete_requested(
    conn: sqlite3.Connection, *, report_ids: list[str], app_user_id: str, turn_id: str
) -> None:
    """Record that a delete was requested for these reports, before confirmation."""
    now = _now()
    conn.executemany(
        _INSERT_AUDIT_LOG,
        _audit_rows(
            report_ids, "delete_requested", app_user_id=app_user_id, turn_id=turn_id, now=now
        ),
    )
    conn.commit()


def confirm_delete(
    conn: sqlite3.Connection, *, report_ids: list[str], app_user_id: str, turn_id: str
) -> None:
    """Soft-delete these reports and record the confirmation in the audit log.

    The `app_user_id` is part of the `UPDATE`'s `WHERE` clause too, not just the initial
    candidate lookup -- a confirmed delete can never touch another user's report even if
    the report id were somehow forged between resolution and confirmation.
    """
    now = _now()
    conn.executemany(
        "UPDATE saved_reports SET deleted_at = ?, deleted_by_turn = ? "
        "WHERE report_id = ? AND app_user_id = ? AND deleted_at IS NULL",
        [(now, turn_id, rid, app_user_id) for rid in report_ids],
    )
    conn.executemany(
        _INSERT_AUDIT_LOG,
        _audit_rows(
            report_ids, "delete_confirmed", app_user_id=app_user_id, turn_id=turn_id, now=now
        ),
    )
    conn.commit()


def log_delete_cancelled(
    conn: sqlite3.Connection, *, report_ids: list[str], app_user_id: str, turn_id: str
) -> None:
    """Record that a pending delete was cancelled -- no row is touched, only the audit trail."""
    now = _now()
    conn.executemany(
        _INSERT_AUDIT_LOG,
        _audit_rows(
            report_ids, "delete_cancelled", app_user_id=app_user_id, turn_id=turn_id, now=now
        ),
    )
    conn.commit()
