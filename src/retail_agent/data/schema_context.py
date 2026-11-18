"""Build a live schema description for the SQL Generator's prompt (Day 3 wiring, used Day 4).

Table/column context comes from `BigQueryRunner.get_table_schema` at call time, not a
hardcoded string -- if a view's columns ever change, the next prompt reflects it
automatically. The two per-user table functions aren't introspectable the same way
(`get_table` doesn't resolve a table function), so their signature is documented
alongside the live view schemas rather than invented as if it were also live.
"""

from __future__ import annotations

from retail_agent.data.bq_client import BigQueryRunner

PROJECT_ID = "opsfleet-tech-assignment"
RETAIL_ANALYTICS_DATASET = f"{PROJECT_ID}.retail_analytics"

# Table functions: not introspectable via BigQueryRunner.get_table_schema (it calls
# client.get_table, which doesn't resolve a routine). Their signature matches
# sql/bigquery_views.sql's `fn_*_for_user` definitions and changes only when that DDL
# does, which is a deliberate design decision (ADR-004), not live schema drift.
_TABLE_FUNCTIONS = (
    "fn_products_for_user(requesting_user_id STRING) -> same columns as vw_products",
    "fn_order_items_for_user(requesting_user_id STRING) -> same columns as vw_order_items",
)

# Plain views whose schema a generated query may reference directly.
_PLAIN_VIEWS = ("vw_orders", "vw_order_items", "vw_products", "vw_users_masked")


def build_schema_context(runner: BigQueryRunner | None = None) -> str:
    """Return a prompt-ready description of every table/function a query may reference.

    Args:
        runner: A `BigQueryRunner` scoped to the `retail_analytics` dataset. Builds one
            if not given.

    Returns:
        A text block listing each view's live columns, plus the two table functions'
        documented signatures. The SQL Generator's system prompt includes this verbatim.
    """
    runner = runner or BigQueryRunner(project_id=PROJECT_ID, dataset_id=RETAIL_ANALYTICS_DATASET)
    lines = [
        "Tables and functions available in `retail_analytics` (the only dataset you may query):",
        "",
    ]
    for view_name in _PLAIN_VIEWS:
        columns = runner.get_table_schema(view_name)
        column_list = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
        lines.append(f"- {view_name}: {column_list}")
    lines.append("")
    lines.append("Table functions (call these, not a raw SELECT, for product/order-item data):")
    for signature in _TABLE_FUNCTIONS:
        lines.append(f"- {signature}")
    return "\n".join(lines)
