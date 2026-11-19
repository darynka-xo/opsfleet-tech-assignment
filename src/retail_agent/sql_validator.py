"""Static SQL validation: the structural wall between generated SQL and BigQuery.

Enforces, before any query ever reaches BigQuery: exactly one statement, SELECT only,
and every table/function reference resolves to the `retail_analytics` whitelist --
fully qualified, never a bare name (an unqualified "vw_orders" could resolve to a
different table under a different default dataset; requiring the full
catalog.dataset.name closes that ambiguity rather than trusting a session default).

This is requirement 2's actual enforcement boundary for the public dataset (see
AGENTS.md's "Security" section) and requirement 5's first line of defense against a
malformed or hostile query ever being billed.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_CATALOG = "opsfleet-tech-assignment"
ALLOWED_DATASET = "retail_analytics"
# Confirmed answer from the client, 2026-11-19: order and customer data are visible only
# through a purchase of an in-scope-brand product -- so none of the 4 base vw_* views are
# safe for generated SQL to reference directly anymore, only the brand-scoped functions.
ALLOWED_VIEWS: set[str] = set()
ALLOWED_FUNCTIONS = {
    "fn_products_for_user",
    "fn_order_items_for_user",
    "fn_orders_for_user",
    "fn_customers_for_user",
}


@dataclass(frozen=True)
class ValidationResult:
    """The validator's verdict on one candidate SQL string."""

    valid: bool
    reason: str | None = None


def _validate_function_call(anonymous: exp.Anonymous) -> str | None:
    """Return a rejection reason for a FROM-clause function call, or None if it's allowed."""
    identifier = anonymous.this
    full_name = identifier.this if isinstance(identifier, exp.Identifier) else str(identifier)
    parts = full_name.split(".")
    if len(parts) != 3:
        return f"function reference must be fully qualified catalog.dataset.name, got {full_name!r}"
    catalog, dataset, name = (p.lower() for p in parts)
    if catalog != ALLOWED_CATALOG or dataset != ALLOWED_DATASET:
        return f"function {full_name!r} is outside {ALLOWED_CATALOG}.{ALLOWED_DATASET}"
    if name not in ALLOWED_FUNCTIONS:
        return f"function {name!r} is not one of the allowed functions {sorted(ALLOWED_FUNCTIONS)}"
    return None


def _validate_table_reference(table: exp.Table) -> str | None:
    """Return a rejection reason for a FROM-clause table reference, or None if it's allowed."""
    catalog = (table.catalog or "").lower()
    dataset = (table.db or "").lower()
    name = (table.name or "").lower()
    if not catalog or not dataset:
        return f"table reference must be fully qualified catalog.dataset.name, got {table.sql()!r}"
    if catalog != ALLOWED_CATALOG or dataset != ALLOWED_DATASET:
        return f"table {table.sql()!r} is outside {ALLOWED_CATALOG}.{ALLOWED_DATASET}"
    if name not in ALLOWED_VIEWS:
        return f"table {name!r} is not one of the allowed views {sorted(ALLOWED_VIEWS)}"
    return None


def validate_sql(sql: str) -> ValidationResult:
    """Validate a candidate SQL string before it's ever sent to BigQuery.

    Args:
        sql: The SQL text to validate (typically LLM-generated).

    Returns:
        A `ValidationResult`; `reason` is set whenever `valid` is False.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="bigquery") if s is not None]
    except Exception as error:  # noqa: BLE001 - any parse failure is a rejection, not a crash
        return ValidationResult(False, f"SQL did not parse: {error}")

    if len(statements) != 1:
        return ValidationResult(False, f"expected exactly one statement, found {len(statements)}")

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        kind = type(statement).__name__
        return ValidationResult(False, f"only SELECT statements are allowed, got {kind}")

    for table in statement.find_all(exp.Table):
        if isinstance(table.this, exp.Anonymous):
            reason = _validate_function_call(table.this)
        else:
            reason = _validate_table_reference(table)
        if reason is not None:
            return ValidationResult(False, reason)

    return ValidationResult(True)
