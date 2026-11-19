"""Per-node observability (requirement 7): structured logs + a queryable event table.

Every graph node's execution becomes one row in `agent_events` (the source for the
rollup metrics in docs/architecture.md section 7: SQL success rate, self-correction
retry rate, latency per node, guardrail block rate) and one structured JSON log line (for
pulling a full trace by `trace_id` without a database handy). `attributes` is a small,
curated dict per node -- see `graph.py`'s `_node_attributes`. Per AGENTS.md's Security
section, a SQL query's *text* is explicitly fine to log (it can only reference the masked
views/functions, never a raw table); what's never logged is a raw query *result* (row
content) or the full report text, which stays recoverable from `saved_reports`/`feedback`
instead of being duplicated into every trace line.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def record_event(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    turn_id: str,
    trace_id: str,
    node_name: str,
    duration_ms: int,
    attributes: dict[str, object],
) -> None:
    """Persist one node execution and emit it as a structured JSON log line.

    Args:
        conn: An open connection to the app-state SQLite database.
        session_id: The conversation this event belongs to.
        turn_id: The turn this event belongs to.
        trace_id: The trace this event belongs to (equals `turn_id` in this prototype;
            see the module docstring).
        node_name: The graph node that ran, e.g. `"generate_sql"`.
        duration_ms: How long the node took, in milliseconds.
        attributes: A small, JSON-safe, node-specific dict of counts/booleans/strings, and
            (where relevant) the generated SQL text -- never a full query result or the
            report body. See the module docstring.
    """
    now = datetime.now(UTC).isoformat()
    record = {
        "session_id": session_id,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "node_name": node_name,
        "duration_ms": duration_ms,
        "attributes": attributes,
        "occurred_at": now,
    }
    conn.execute(
        "INSERT INTO agent_events "
        "(event_id, session_id, turn_id, trace_id, node_name, duration_ms, attributes, "
        "occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            session_id,
            turn_id,
            trace_id,
            node_name,
            duration_ms,
            json.dumps(attributes),
            now,
        ),
    )
    conn.commit()
    logger.info(json.dumps({"gen_ai.agent.node": node_name, **record}))
