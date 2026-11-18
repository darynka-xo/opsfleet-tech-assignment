-- BigQuery-side schema for the retail data-analysis chat agent.
-- Project: opsfleet-tech-assignment. Dataset: retail_analytics (ours — not the public
-- bigquery-public-data project, which we don't own and can't attach governance to).
--
-- Applied Day 3 by an admin/seed script, not by the live agent. The agent's own service
-- account only ever SELECTs against this dataset at runtime (see AGENTS.md: "BigQuery is
-- read-only and stays read-only") — nothing here is written to by a conversation turn.
-- Design rationale: docs/architecture.md section "Data layer design".

CREATE SCHEMA IF NOT EXISTS `opsfleet-tech-assignment.retail_analytics`
OPTIONS (
  description = "Governed views + agent-readable ACL over bigquery-public-data.thelook_ecommerce. The agent's service account is granted BigQuery Data Viewer on this dataset only -- never on the public dataset's base tables directly."
);

-- 1. Governed views over the 4 source tables -----------------------------------------
-- orders/order_items/products carry no PII (confirmed Day 1) -- pass-through views exist
-- so the agent's IAM grant and the SQL validator's table whitelist (Day 4) point at one
-- dataset instead of carving out an exception for bigquery-public-data.

CREATE OR REPLACE VIEW `opsfleet-tech-assignment.retail_analytics.vw_orders` AS
SELECT order_id, user_id, status, gender, created_at, returned_at, shipped_at,
       delivered_at, num_of_item
FROM `bigquery-public-data.thelook_ecommerce.orders`;

CREATE OR REPLACE VIEW `opsfleet-tech-assignment.retail_analytics.vw_order_items` AS
SELECT id, order_id, user_id, product_id, inventory_item_id, status, created_at,
       shipped_at, delivered_at, returned_at, sale_price
FROM `bigquery-public-data.thelook_ecommerce.order_items`;

CREATE OR REPLACE VIEW `opsfleet-tech-assignment.retail_analytics.vw_products` AS
SELECT id, cost, category, name, brand, retail_price, department, sku,
       distribution_center_id
FROM `bigquery-public-data.thelook_ecommerce.products`;

-- users carries hard PII (Day 1 inventory). Requirement 2 is "strictly forbidden from
-- displaying", not "displayed to some callers", so this is unconditional for every
-- caller -- the columns are simply never selected, no masking machinery needed.
CREATE OR REPLACE VIEW `opsfleet-tech-assignment.retail_analytics.vw_users_masked` AS
SELECT id, age, gender, state, country, traffic_source, created_at
FROM `bigquery-public-data.thelook_ecommerce.users`;
-- Omitted on purpose, never selectable through this view: first_name, last_name, email,
-- street_address, postal_code, city, latitude, longitude, user_geom.

-- 2. Per-user product ACL (requirement 2's "products related to him") ----------------
-- Seeded once by an admin/seed script (Day 3), not written by the live agent. One row
-- per (app user, product category) they're allowed to analyze.

CREATE TABLE IF NOT EXISTS `opsfleet-tech-assignment.retail_analytics.user_product_access` (
  app_user_id      STRING NOT NULL OPTIONS (description = "Demo executive user id -- not a real GCP/IAM principal, see the table function comment below."),
  product_category STRING NOT NULL,
  granted_at       TIMESTAMP NOT NULL
);

-- Why a parameterized table function, not a native BigQuery row access policy: row
-- access policies filter by the QUERYING IAM PRINCIPAL (SESSION_USER()), but every app
-- user shares one BigQuery service account -- BigQuery itself cannot tell them apart.
-- The agent's orchestration code (not the LLM) knows which app user is authenticated for
-- the current turn and passes that id as an explicit, trusted parameter; the SQL
-- validator (Day 4, sqlglot) only ever allows a FROM clause that calls one of these two
-- functions for product-level analysis, never a raw SELECT against vw_products or
-- vw_order_items, so a generated query cannot bypass the filter by construction.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_products_for_user`(requesting_user_id STRING)
AS (
  SELECT p.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_products` AS p
  WHERE EXISTS (
    SELECT 1
    FROM `opsfleet-tech-assignment.retail_analytics.user_product_access` AS acl
    WHERE acl.app_user_id = requesting_user_id
      AND acl.product_category = p.category
  )
);

-- order_items analysis joins down to product level, so it goes through the same gate --
-- "orders for products I can see" is parameter-scoped too, not just the products table.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_order_items_for_user`(requesting_user_id STRING)
AS (
  SELECT oi.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_order_items` AS oi
  JOIN `opsfleet-tech-assignment.retail_analytics.vw_products` AS p ON p.id = oi.product_id
  WHERE EXISTS (
    SELECT 1
    FROM `opsfleet-tech-assignment.retail_analytics.user_product_access` AS acl
    WHERE acl.app_user_id = requesting_user_id
      AND acl.product_category = p.category
  )
);
