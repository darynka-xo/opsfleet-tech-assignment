"""Seed `user_product_access` with demo executive users and category assignments.

Admin-only, same as scripts/apply_bigquery_ddl.py. Idempotent: deletes each demo user's
prior rows before inserting, so re-running updates the assignment instead of duplicating
it. Run with:
    .venv/Scripts/python.exe scripts/seed_acl.py
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TABLE = "opsfleet-tech-assignment.retail_analytics.user_product_access"

# Demo executive users and the product categories each may analyze (requirement 2,
# "products related to him"). thelook_ecommerce has no real owner/rep relationship, so
# this is a documented synthetic mapping (AGENTS.md's open assumptions). Category values
# are the real distinct `products.category` values (verified live, 2026-11-19) -- not
# guessed; this dataset has no "Shoes"/"Jewelry"/"Sunglasses" category at all.
DEMO_ACCESS = {
    "exec-apparel": ["Jeans", "Outerwear & Coats", "Swim", "Sweaters", "Dresses"],
    "exec-accessories": ["Accessories", "Fashion Hoodies & Sweatshirts", "Tops & Tees", "Leggings"],
    "exec-intimates": ["Socks", "Socks & Hosiery", "Underwear", "Intimates", "Sleep & Lounge"],
}


def main() -> None:
    """Replace each demo user's rows in `user_product_access`."""
    client = bigquery.Client(project="opsfleet-tech-assignment")
    now = datetime.now(UTC).isoformat()

    user_list = ", ".join(f"'{u}'" for u in DEMO_ACCESS)
    client.query(f"DELETE FROM `{TABLE}` WHERE app_user_id IN ({user_list})").result()
    logger.info("Cleared prior rows for %d demo users", len(DEMO_ACCESS))

    rows = [
        {"app_user_id": user, "product_category": category, "granted_at": now}
        for user, categories in DEMO_ACCESS.items()
        for category in categories
    ]
    errors = client.insert_rows_json(TABLE, rows)
    if errors:
        raise RuntimeError(f"Failed to insert ACL rows: {errors}")
    logger.info("Inserted %d (user, category) grants for %d users", len(rows), len(DEMO_ACCESS))


if __name__ == "__main__":
    main()
