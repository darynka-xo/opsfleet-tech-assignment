"""Build a live schema description for the SQL Generator's prompt (Day 3 wiring, used Day 4).

Column lists come from `BigQueryRunner.get_table_schema` against the 4 base views at call
time, not a hardcoded string -- if a view's columns ever change, the next prompt reflects
it automatically. The function *names* aren't introspectable the same way
(`get_table` doesn't resolve a routine), so they're documented alongside the live column
lists rather than invented as if they were also live. Only the functions are ever
presented as something the model may query -- the base views exist for their columns,
never as a FROM-clause target (confirmed answer, 2026-11-19: order/customer data is
visible only through a purchase of an in-scope-brand product, so direct view access was
removed from both the SQL validator's whitelist and this prompt).
"""

from __future__ import annotations

from retail_agent.data.bq_client import BigQueryRunner

PROJECT_ID = "opsfleet-tech-assignment"
RETAIL_ANALYTICS_DATASET = f"{PROJECT_ID}.retail_analytics"

# (function name, base view whose columns it returns) -- matches sql/bigquery_views.sql's
# fn_*_for_user definitions; changes only when that DDL does (ADR-004).
_FUNCTIONS = (
    ("fn_products_for_user", "vw_products"),
    ("fn_order_items_for_user", "vw_order_items"),
    ("fn_orders_for_user", "vw_orders"),
    ("fn_customers_for_user", "vw_users_masked"),
)


def build_schema_context(runner: BigQueryRunner | None = None) -> str:
    """Return a prompt-ready description of every function a query may reference.

    Args:
        runner: A `BigQueryRunner` scoped to the `retail_analytics` dataset. Builds one
            if not given.

    Returns:
        A text block listing each brand-scoped function's live columns. The SQL
        Generator's system prompt includes this verbatim.
    """
    runner = runner or BigQueryRunner(project_id=PROJECT_ID, dataset_id=RETAIL_ANALYTICS_DATASET)
    lines = [
        "Functions available in `retail_analytics` -- the only things you may put in a "
        "FROM clause, each taking one argument (requesting_user_id STRING):",
        "",
    ]
    for function_name, base_view in _FUNCTIONS:
        columns = runner.get_table_schema(base_view)
        column_list = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
        lines.append(f"- {function_name}(requesting_user_id): {column_list}")
    return "\n".join(lines)
