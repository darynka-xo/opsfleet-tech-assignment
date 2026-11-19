"""Integration tests against the live `retail_analytics` dataset (requirement 2).

Needs real GCP access and the DDL from scripts/apply_bigquery_ddl.py already applied,
and scripts/seed_acl.py already run. Run with:
    .venv/Scripts/python.exe -m pytest -m integration tests/test_bigquery_data_layer.py
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

RETAIL_ANALYTICS = "opsfleet-tech-assignment.retail_analytics"

# Columns the Day 1 PII inventory flagged as hard PII -- must never appear in a column
# name the masked view exposes.
_HARD_PII_COLUMNS = {
    "first_name",
    "last_name",
    "email",
    "street_address",
    "postal_code",
    "city",
    "latitude",
    "longitude",
    "user_geom",
}


@pytest.fixture(scope="module")
def bq_client() -> bigquery.Client:
    """A BigQuery client under the developer's own credentials (read-only queries only)."""
    return bigquery.Client(project="opsfleet-tech-assignment")


@pytest.mark.integration
def test_masked_users_view_never_exposes_pii_columns(bq_client: bigquery.Client) -> None:
    """No hard-PII column name is selectable through vw_users_masked, by schema alone."""
    table = bq_client.get_table(f"{RETAIL_ANALYTICS}.vw_users_masked")
    exposed_columns = {field.name for field in table.schema}

    assert exposed_columns.isdisjoint(_HARD_PII_COLUMNS)
    # Confirm the view isn't just empty -- it still has the demographic columns.
    assert "age" in exposed_columns
    assert "state" in exposed_columns


@pytest.mark.integration
def test_customers_function_never_exposes_pii_at_query_time(bq_client: bigquery.Client) -> None:
    """A real query against fn_customers_for_user can't surface PII -- no such column."""
    sql = f"SELECT * FROM `{RETAIL_ANALYTICS}.fn_customers_for_user`('ceo') LIMIT 1"
    rows = list(bq_client.query(sql).result())
    assert rows
    row_fields = set(rows[0].keys())
    assert row_fields.isdisjoint(_HARD_PII_COLUMNS)


def _brands_visible_to(bq_client: bigquery.Client, app_user_id: str) -> set[str]:
    """Distinct brands `fn_products_for_user` returns for one user."""
    sql = f"SELECT DISTINCT brand FROM `{RETAIL_ANALYTICS}.fn_products_for_user`('{app_user_id}')"
    return {row.brand for row in bq_client.query(sql).result()}


@pytest.mark.integration
def test_per_user_brand_acl_scopes_correctly(bq_client: bigquery.Client) -> None:
    """A seeded user sees only their assigned brands; an unknown user sees none."""
    assert _brands_visible_to(bq_client, "mgr-denim") == {"Calvin Klein", "Levi's"}
    assert _brands_visible_to(bq_client, "nonexistent-user-xyz") == set()


@pytest.mark.integration
def test_fn_products_for_user_excludes_other_users_brands(bq_client: bigquery.Client) -> None:
    """mgr-denim's result never includes a brand only mgr-outdoor was granted."""
    brands = _brands_visible_to(bq_client, "mgr-denim")
    assert "Carhartt" not in brands
    assert "Hanes" not in brands


@pytest.mark.integration
def test_ceo_bypasses_the_acl_and_sees_every_brand(bq_client: bigquery.Client) -> None:
    """'ceo' sees the same brand count as an unrestricted query -- the documented bypass."""
    ceo_brands = _brands_visible_to(bq_client, "ceo")
    all_brands_sql = f"SELECT DISTINCT brand FROM `{RETAIL_ANALYTICS}.vw_products`"
    all_brands = {row.brand for row in bq_client.query(all_brands_sql).result()}
    assert ceo_brands == all_brands


@pytest.mark.integration
def test_orders_scoped_through_in_scope_purchases(bq_client: bigquery.Client) -> None:
    """fn_orders_for_user returns fewer rows than the unscoped total, never zero or all.

    Confirms the client's answer that order data is visible "only through purchases of
    in-scope products" is actually enforced, not just documented.
    """
    scoped_sql = f"SELECT COUNT(*) AS n FROM `{RETAIL_ANALYTICS}.fn_orders_for_user`('mgr-denim')"
    scoped_orders = list(bq_client.query(scoped_sql).result())[0].n
    total_sql = f"SELECT COUNT(*) AS n FROM `{RETAIL_ANALYTICS}.vw_orders`"
    total_orders = list(bq_client.query(total_sql).result())[0].n
    assert 0 < scoped_orders < total_orders
