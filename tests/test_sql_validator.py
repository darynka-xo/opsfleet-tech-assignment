"""Tests for the SQL validator (requirement 2's enforcement boundary, requirement 5's
first line of defense). Offline and deterministic -- pure SQL parsing, no network."""

from __future__ import annotations

import pytest

from retail_agent.sql_validator import validate_sql

RA = "opsfleet-tech-assignment.retail_analytics"


def test_allows_the_products_function() -> None:
    sql = f"SELECT * FROM `{RA}.fn_products_for_user`(@requesting_user_id) LIMIT 1"
    assert validate_sql(sql).valid


def test_allows_the_order_items_function() -> None:
    sql = f"""
        SELECT oi.user_id, SUM(oi.sale_price) AS s
        FROM `{RA}.fn_order_items_for_user`(@requesting_user_id) AS oi
        GROUP BY oi.user_id
    """
    assert validate_sql(sql).valid


def test_allows_the_orders_function() -> None:
    sql = f"SELECT COUNT(*) AS n FROM `{RA}.fn_orders_for_user`(@requesting_user_id)"
    assert validate_sql(sql).valid


def test_allows_the_customers_function() -> None:
    sql = f"SELECT COUNT(*) AS n FROM `{RA}.fn_customers_for_user`(@requesting_user_id)"
    assert validate_sql(sql).valid


def test_allows_a_join_across_two_allowed_functions() -> None:
    sql = f"""
        SELECT p.category, SUM(oi.sale_price) AS revenue
        FROM `{RA}.fn_order_items_for_user`(@requesting_user_id) AS oi
        JOIN `{RA}.fn_products_for_user`(@requesting_user_id) AS p
          ON p.id = oi.product_id
        GROUP BY p.category
    """
    assert validate_sql(sql).valid


@pytest.mark.parametrize(
    ("name", "sql"),
    [
        (
            "raw public dataset",
            "SELECT * FROM `bigquery-public-data.thelook_ecommerce.users`",
        ),
        ("bare unqualified name", "SELECT * FROM vw_orders"),
        (
            "a base view, even fully qualified -- only the functions are allowed now",
            f"SELECT * FROM `{RA}.vw_orders`",
        ),
        (
            "the masked users view read directly",
            f"SELECT * FROM `{RA}.vw_users_masked`",
        ),
        (
            "DROP statement",
            f"DROP TABLE `{RA}.vw_orders`",
        ),
        (
            "DELETE statement",
            f"DELETE FROM `{RA}.user_brand_access` WHERE TRUE",
        ),
        (
            "multi-statement injection",
            f"SELECT 1; DROP TABLE `{RA}.vw_orders`",
        ),
        (
            "the ACL table read directly, bypassing the function filter",
            f"SELECT * FROM `{RA}.user_brand_access`",
        ),
        (
            "a different dataset in the same project",
            "SELECT * FROM `opsfleet-tech-assignment.other_dataset.vw_orders`",
        ),
        (
            "an unknown function",
            f"SELECT * FROM `{RA}.fn_evil`(@x)",
        ),
        ("garbage input", "not even sql;;; ("),
    ],
)
def test_rejects_everything_outside_the_whitelist(name: str, sql: str) -> None:
    result = validate_sql(sql)
    assert not result.valid, f"{name} should have been rejected"
    assert result.reason
