"""Integration test for the live schema-introspection wiring (Day 3)."""

from __future__ import annotations

import pytest

from retail_agent.data.schema_context import build_schema_context


@pytest.mark.integration
def test_schema_context_reflects_live_columns_and_documents_all_four_functions() -> None:
    """The built context names every function and its real columns -- never a raw view."""
    context = build_schema_context()

    assert "age (INTEGER)" in context
    assert "email" not in context  # never introspected -- masked out at the view itself
    for function_name in (
        "fn_products_for_user",
        "fn_order_items_for_user",
        "fn_orders_for_user",
        "fn_customers_for_user",
    ):
        assert function_name in context
    # The base views exist only as a column source now, never a query target.
    assert "vw_users_masked" not in context
