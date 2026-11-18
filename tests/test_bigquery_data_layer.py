"""Integration tests against the live `retail_analytics` dataset (requirement 2).

Needs real GCP access and the DDL from scripts/apply_bigquery_ddl.py already applied.
Run with:
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
def test_masked_users_view_returns_no_pii_at_query_time(bq_client: bigquery.Client) -> None:
    """A real query against the view can't surface PII, because there's no such column."""
    rows = list(
        bq_client.query(f"SELECT * FROM `{RETAIL_ANALYTICS}.vw_users_masked` LIMIT 1").result()
    )
    assert rows
    row_fields = set(rows[0].keys())
    assert row_fields.isdisjoint(_HARD_PII_COLUMNS)


def _categories_visible_to(bq_client: bigquery.Client, app_user_id: str) -> set[str]:
    """Distinct product categories `fn_products_for_user` returns for one user."""
    sql = (
        f"SELECT DISTINCT category FROM `{RETAIL_ANALYTICS}.fn_products_for_user`('{app_user_id}')"
    )
    return {row.category for row in bq_client.query(sql).result()}


@pytest.mark.integration
def test_per_user_product_acl_scopes_correctly(bq_client: bigquery.Client) -> None:
    """A seeded user sees only their assigned categories; an unknown user sees none."""
    assigned_categories = _categories_visible_to(bq_client, "exec-apparel")
    assert assigned_categories == {"Jeans", "Outerwear & Coats", "Swim", "Sweaters", "Dresses"}

    assert _categories_visible_to(bq_client, "nonexistent-user-xyz") == set()


@pytest.mark.integration
def test_fn_products_for_user_excludes_other_users_categories(bq_client: bigquery.Client) -> None:
    """exec-apparel's result never includes a category only exec-intimates was granted."""
    categories = _categories_visible_to(bq_client, "exec-apparel")
    assert "Underwear" not in categories
    assert "Socks" not in categories
