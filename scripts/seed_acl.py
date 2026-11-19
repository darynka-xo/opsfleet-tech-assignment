"""Seed `user_brand_access` with demo brand-manager users.

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

TABLE = "opsfleet-tech-assignment.retail_analytics.user_brand_access"

# 'ceo' is a reserved app_user_id meaning "every brand" -- a bypass condition inside each
# fn_*_for_user function (sql/bigquery_views.sql), never seeded as ~2,800 ACL rows here.
CEO_USER_ID = "ceo"

# Demo brand-manager users and the brands each may analyze (requirement 2, confirmed
# answer 2026-11-19: access is by brand, "each manager can analyze only the brands they
# manage"). thelook_ecommerce has no real manager/brand relationship, so this is a
# documented synthetic mapping (AGENTS.md's open assumptions). Brand values are the real
# distinct `products.brand` values (verified live, 2026-11-19), not guessed.
DEMO_ACCESS = {
    "mgr-denim": ["Calvin Klein", "Levi's"],
    "mgr-outdoor": ["Carhartt", "Volcom", "Nautica", "Quiksilver"],
    "mgr-basics": ["Hanes"],
}


def main() -> None:
    """Replace each demo user's rows in `user_brand_access`."""
    client = bigquery.Client(project="opsfleet-tech-assignment")
    now = datetime.now(UTC).isoformat()

    user_list = ", ".join(f"'{u}'" for u in DEMO_ACCESS)
    client.query(f"DELETE FROM `{TABLE}` WHERE app_user_id IN ({user_list})").result()
    logger.info("Cleared prior rows for %d demo users", len(DEMO_ACCESS))

    rows = [
        {"app_user_id": user, "brand": brand, "granted_at": now}
        for user, brands in DEMO_ACCESS.items()
        for brand in brands
    ]
    errors = client.insert_rows_json(TABLE, rows)
    if errors:
        raise RuntimeError(f"Failed to insert ACL rows: {errors}")
    logger.info("Inserted %d (user, brand) grants for %d users", len(rows), len(DEMO_ACCESS))
    logger.info("%r needs no seeding -- it bypasses the ACL in every fn_*_for_user", CEO_USER_ID)


if __name__ == "__main__":
    main()
