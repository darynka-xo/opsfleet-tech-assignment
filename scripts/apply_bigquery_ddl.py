"""Apply sql/bigquery_views.sql against the live project.

Admin-only: run under the developer's own gcloud ADC (project owner), never under the
agent's runtime service account, which only ever gets SELECT on the result. Idempotent --
every statement in sql/bigquery_views.sql is CREATE OR REPLACE / CREATE IF NOT EXISTS, so
re-running this is safe. Run with:
    .venv/Scripts/python.exe scripts/apply_bigquery_ddl.py

The statement splitter below is a naive line-based one, not a real SQL tokenizer: a `--`
inside a string literal (not just a comment) would be mistaken for a comment and truncate
the literal. Keep every OPTIONS(description = "...") string in sql/bigquery_views.sql free
of `--`; this bit twice for real before that rule was written down.
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DDL_FILE = Path(__file__).resolve().parent.parent / "sql" / "bigquery_views.sql"


def _strip_line_comments(sql_text: str) -> str:
    """Drop everything from `--` to end of line, on every line.

    Comments must be stripped *before* splitting on `;` -- a prose `--` comment can
    contain a literal semicolon (this file's comments do), which would otherwise be
    mistaken for a statement boundary and cut a real statement in half. Confirmed by
    this exact failure mode when the script first ran for real: BigQuery rejected a
    statement that began mid-sentence, inside what used to be a comment.
    """
    lines = []
    for line in sql_text.splitlines():
        idx = line.find("--")
        lines.append(line[:idx] if idx != -1 else line)
    return "\n".join(lines)


def split_statements(sql_text: str) -> list[str]:
    """Split a .sql file into individual statements on top-level semicolons.

    Args:
        sql_text: The full contents of a .sql file with one statement per `;`.

    Returns:
        Non-empty, comment-free statement strings, in file order.
    """
    code_only = _strip_line_comments(sql_text)
    statements = [s.strip() for s in code_only.split(";")]
    return [s for s in statements if s]


def main() -> None:
    """Apply every statement in sql/bigquery_views.sql against BigQuery, in order."""
    client = bigquery.Client(project="opsfleet-tech-assignment")
    sql_text = DDL_FILE.read_text(encoding="utf-8")
    statements = split_statements(sql_text)
    logger.info("Applying %d statements from %s", len(statements), DDL_FILE)
    for i, statement in enumerate(statements, start=1):
        first_line = statement.splitlines()[-1] if statement.splitlines() else statement
        logger.info("[%d/%d] %s", i, len(statements), first_line[:90])
        client.query(statement).result()
    logger.info("Done.")


if __name__ == "__main__":
    main()
