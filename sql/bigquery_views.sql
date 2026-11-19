-- BigQuery-side schema for the retail data-analysis chat agent.
-- Project: opsfleet-tech-assignment. Dataset: retail_analytics (ours, not the public
-- bigquery-public-data project, which we don't own and can't attach governance to).
--
-- Applied by an admin/seed script, not by the live agent. The agent's own service
-- account only ever SELECTs against this dataset at runtime (see AGENTS.md: "BigQuery is
-- read-only and stays read-only") -- nothing here is written to by a conversation turn.
-- Design rationale: docs/architecture.md section "Data layer design" and ADR-004.

CREATE SCHEMA IF NOT EXISTS `opsfleet-tech-assignment.retail_analytics`
OPTIONS (
  description = "Governed, brand-scoped views over bigquery-public-data.thelook_ecommerce. The agent's service account is only ever granted BigQuery Data Viewer on this dataset, nothing project-wide. bigquery-public-data is independently world-readable, so the real wall against the agent reading raw data is the Day 4 SQL validator's function whitelist, not this grant, see AGENTS.md Security."
);

-- 1. Base views over the 4 source tables (internal building blocks only) -------------
-- Not part of the SQL validator's whitelist for generated queries -- every one of
-- requirement 2's "each user should only be able to analyze data on products related to
-- him" / the client's "customer and order data are visible only through purchases of
-- in-scope products" (confirmed answer, 2026-11-19) means NONE of the 4 base views are
-- safe to expose directly. Only the brand-scoped functions below are.

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

-- 2. Per-user brand ACL (confirmed answer, 2026-11-19: access is by BRAND, not category
-- -- "each manager can analyze only the brands they manage, and the CEO sees everything")
-- ---------------------------------------------------------------------------------------
-- Seeded once by an admin/seed script (scripts/seed_acl.py), not written by the live
-- agent. One row per (app user, brand) they're allowed to analyze. The CEO is not seeded
-- with all ~2,800 brands -- "sees everything" is a bypass condition in each function
-- below (requesting_user_id = CEO_USER_ID), not a few thousand ACL rows to maintain.

CREATE TABLE IF NOT EXISTS `opsfleet-tech-assignment.retail_analytics.user_brand_access` (
  app_user_id STRING NOT NULL OPTIONS (description = "Demo executive user id, not a real GCP/IAM principal, see the table function comments below."),
  brand       STRING NOT NULL,
  granted_at  TIMESTAMP NOT NULL
);

-- Why a parameterized table function, not a native BigQuery row access policy: row
-- access policies filter by the QUERYING IAM PRINCIPAL (SESSION_USER()), but every app
-- user shares one BigQuery service account -- BigQuery itself cannot tell them apart.
-- The agent's orchestration code (not the LLM) knows which app user is authenticated for
-- the current turn and passes that id as an explicit, trusted parameter; the SQL
-- validator (Day 4, sqlglot) only ever allows a FROM clause that calls one of these four
-- functions, never a raw SELECT against any vw_* view, so a generated query cannot
-- bypass the filter by construction.
--
-- 'ceo' is a reserved app_user_id meaning "every brand" -- never seeded as ACL rows.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_products_for_user`(requesting_user_id STRING)
AS (
  SELECT p.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_products` AS p
  WHERE requesting_user_id = 'ceo'
     OR EXISTS (
       SELECT 1
       FROM `opsfleet-tech-assignment.retail_analytics.user_brand_access` AS acl
       WHERE acl.app_user_id = requesting_user_id
         AND acl.brand = p.brand
     )
);

-- order_items analysis joins down to product level, so it goes through the same gate --
-- "order items for brands I can see" is parameter-scoped too, not just the products table.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_order_items_for_user`(requesting_user_id STRING)
AS (
  SELECT oi.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_order_items` AS oi
  JOIN `opsfleet-tech-assignment.retail_analytics.vw_products` AS p ON p.id = oi.product_id
  WHERE requesting_user_id = 'ceo'
     OR EXISTS (
       SELECT 1
       FROM `opsfleet-tech-assignment.retail_analytics.user_brand_access` AS acl
       WHERE acl.app_user_id = requesting_user_id
         AND acl.brand = p.brand
     )
);

-- Orders are visible only through a purchase of an in-scope-brand product (the client's
-- confirmed answer) -- an order with zero in-scope items never appears here, even if it
-- also contains out-of-scope items purchased in the same checkout.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_orders_for_user`(requesting_user_id STRING)
AS (
  SELECT DISTINCT o.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_orders` AS o
  WHERE EXISTS (
    SELECT 1
    FROM `opsfleet-tech-assignment.retail_analytics.fn_order_items_for_user`(requesting_user_id) AS oi
    WHERE oi.order_id = o.order_id
  )
);

-- Same rule for customers: visible only through a purchase of an in-scope-brand product.
CREATE OR REPLACE TABLE FUNCTION
  `opsfleet-tech-assignment.retail_analytics.fn_customers_for_user`(requesting_user_id STRING)
AS (
  SELECT DISTINCT u.*
  FROM `opsfleet-tech-assignment.retail_analytics.vw_users_masked` AS u
  WHERE EXISTS (
    SELECT 1
    FROM `opsfleet-tech-assignment.retail_analytics.fn_order_items_for_user`(requesting_user_id) AS oi
    WHERE oi.user_id = u.id
  )
);
