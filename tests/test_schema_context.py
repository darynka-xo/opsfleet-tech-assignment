"""Integration test for the live schema-introspection wiring (Day 3)."""

from __future__ import annotations

import pytest

from retail_agent.data.schema_context import build_schema_context


@pytest.mark.integration
def test_schema_context_reflects_live_columns_and_documents_functions() -> None:
    """The built context names every governed view's real columns and both functions."""
    context = build_schema_context()

    assert "vw_users_masked" in context
    assert "age (INTEGER)" in context
    assert "email" not in context  # never introspected -- masked out at the view itself
    assert "fn_products_for_user" in context
    assert "fn_order_items_for_user" in context
