"""Print the system-level metrics rollup (requirement 7) from the local app-state database.

Reads `agent_events` (one row per graph-node execution, written by
`retail_agent.telemetry`) and `feedback`. No network access, no Gemini/BigQuery calls --
this is a report over data the agent already collected during normal use. Run with:
    .venv/Scripts/python.exe scripts/metrics_rollup.py [path/to/app_state.sqlite3]

Metrics match docs/architecture.md section 7's spec: SQL success rate, average
self-correction retries, guardrail block rate, latency per node, destructive-ops
attempted/confirmed/cancelled, and the 👍/👎 rate. Token/cost-per-session is a documented
Day 6 scope cut (see PLANNING.md) -- `LLMClient.generate()` doesn't expose usage metadata
yet, so there's nothing to roll up.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / ".local" / "app_state.sqlite3"


def _print_header(title: str) -> None:
    print(f"\n== {title} ==")


def report_sql_success_rate(conn: sqlite3.Connection) -> None:
    """Of the turns that attempted SQL (reached synthesize or fallback), how many succeeded."""
    row = conn.execute(
        "SELECT "
        "  COUNT(DISTINCT CASE WHEN node_name = 'synthesize' THEN turn_id END) AS succeeded, "
        "  COUNT(DISTINCT CASE WHEN node_name IN ('synthesize', 'fallback') THEN turn_id END) "
        "    AS attempted "
        "FROM agent_events"
    ).fetchone()
    succeeded, attempted = row
    _print_header("SQL success rate")
    if attempted == 0:
        print("No analysis turns recorded yet.")
        return
    rate = succeeded / attempted
    print(f"{succeeded}/{attempted} turns reached a synthesized report ({rate:.0%}).")


def report_self_correction_retries(conn: sqlite3.Connection) -> None:
    """Average number of generate_sql attempts per turn (1 = no retry needed)."""
    rows = conn.execute(
        "SELECT turn_id, "
        "  MAX(CAST(json_extract(attributes, '$.sql_attempts') AS INTEGER)) AS attempts "
        "FROM agent_events WHERE node_name = 'generate_sql' GROUP BY turn_id"
    ).fetchall()
    _print_header("Self-correction retries")
    if not rows:
        print("No SQL-generation turns recorded yet.")
        return
    avg_attempts = sum(r[1] for r in rows) / len(rows)
    print(f"Average {avg_attempts:.2f} generate_sql attempts per turn, across {len(rows)} turns.")


def report_guardrail_block_rate(conn: sqlite3.Connection) -> None:
    """Fraction of guardrail checks that blocked the message."""
    row = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN json_extract(attributes, '$.blocked') THEN 1 ELSE 0 END) AS blocked, "
        "  COUNT(*) AS total "
        "FROM agent_events WHERE node_name = 'guardrail'"
    ).fetchone()
    blocked, total = row
    _print_header("Guardrail block rate")
    if not total:
        print("No guardrail checks recorded yet.")
        return
    print(f"{blocked}/{total} analysis questions blocked ({blocked / total:.0%}).")


def report_latency_per_node(conn: sqlite3.Connection) -> None:
    """Average and max latency per graph node, in milliseconds."""
    rows = conn.execute(
        "SELECT node_name, AVG(duration_ms), MAX(duration_ms), COUNT(*) "
        "FROM agent_events GROUP BY node_name ORDER BY AVG(duration_ms) DESC"
    ).fetchall()
    _print_header("Latency per node (ms)")
    if not rows:
        print("No events recorded yet.")
        return
    for node_name, avg_ms, max_ms, count in rows:
        print(f"{node_name:<22} avg {avg_ms:7.0f}  max {max_ms:7.0f}  n={count}")


def report_destructive_ops(conn: sqlite3.Connection) -> None:
    """How many delete confirmations were attempted, confirmed, and cancelled."""
    row = conn.execute(
        "SELECT "
        "  COUNT(*) AS attempted, "
        "  SUM(CASE WHEN json_extract(attributes, '$.confirmed') THEN 1 ELSE 0 END) AS confirmed "
        "FROM agent_events WHERE node_name = 'confirm_delete'"
    ).fetchone()
    attempted, confirmed = row
    _print_header("Destructive ops (delete confirmations)")
    if not attempted:
        print("No delete confirmations recorded yet.")
        return
    print(f"{attempted} attempted, {confirmed} confirmed, {attempted - confirmed} cancelled.")


def report_feedback_rate(conn: sqlite3.Connection) -> None:
    """👍/👎 rate among the feedback this database has collected."""
    row = conn.execute(
        "SELECT SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END), COUNT(*) FROM feedback"
    ).fetchone()
    up, total = row
    _print_header("Feedback (up/down)")
    if not total:
        print("No feedback recorded yet.")
        return
    print(f"{up}/{total} rated positively ({up / total:.0%}).")


def main(argv: list[str] | None = None) -> None:
    """Print every rollup metric against the given (or default) app-state database."""
    argv = sys.argv[1:] if argv is None else argv
    db_path = Path(argv[0]) if argv else DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"No database at {db_path} -- have any conversations been run yet?")
        return

    conn = sqlite3.connect(db_path)
    report_sql_success_rate(conn)
    report_self_correction_retries(conn)
    report_guardrail_block_rate(conn)
    report_latency_per_node(conn)
    report_destructive_ops(conn)
    report_feedback_rate(conn)


if __name__ == "__main__":
    main()
