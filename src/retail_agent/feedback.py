"""Feedback capture (requirement 4b / 6): the 👍/👎 signal behind system-level learning.

A parameterized-query repository, same pattern as `reports.py`/`profiles.py` -- a
`sqlite3.Connection` is passed in, never opened here, so tests can point this at a
throwaway `:memory:` database. `question`/`sql_query`/`report_body` are denormalized onto
each feedback row (AGENTS.md's own rationale, already in `sql/app_state.sql`'s comment) so
the Day 6 curation script can read candidate (question, SQL, report) trios straight off
this table with no join back to `saved_reports`.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

Rating = Literal["up", "down"]


def record_feedback(
    conn: sqlite3.Connection,
    *,
    app_user_id: str,
    session_id: str,
    turn_id: str,
    rating: Rating,
    question: str,
    sql_query: str | None,
    report_body: str | None,
    comment: str | None = None,
) -> str:
    """Save one 👍/👎 rating for a turn's answer.

    Args:
        conn: An open connection to the app-state SQLite database.
        app_user_id: The user giving feedback.
        session_id: The conversation this turn belongs to.
        turn_id: The turn being rated.
        rating: `"up"` or `"down"`.
        question: The question that produced the rated answer.
        sql_query: The SQL executed for that turn, if any (off-topic/delete turns have none).
        report_body: The synthesized report text, if any.
        comment: An optional free-text comment the user gave alongside the rating.

    Returns:
        The new feedback row's id.
    """
    feedback_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO feedback "
        "(feedback_id, app_user_id, session_id, turn_id, rating, comment, question, "
        "sql_query, report_body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            feedback_id,
            app_user_id,
            session_id,
            turn_id,
            rating,
            comment,
            question,
            sql_query,
            report_body,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return feedback_id


def list_positive_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every 👍-rated turn that has both a SQL query and a report body.

    Used by `scripts/curate_golden_bucket_candidates.py` (requirement 4b's system-level
    learning loop) as the raw pool of candidate new Golden Bucket trios -- a 👍 with no SQL
    (an off-topic rejection, say) can't become a trio, so those are excluded here rather
    than filtered out downstream.
    """
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM feedback WHERE rating = 'up' AND sql_query IS NOT NULL "
        "AND report_body IS NOT NULL ORDER BY created_at"
    )
    return cursor.fetchall()
