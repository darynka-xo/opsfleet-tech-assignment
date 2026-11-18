# ADR-004: Per-user product ACL enforcement mechanism

**Status:** Accepted (design-stage, DDL syntax-validated 2026-11-18, not yet applied)

## Context

Requirement 2 requires "each user should only be able to analyze data on products
related to him." The agent's service account is the single BigQuery identity every app
user's query runs under — there is no real GCP/IAM principal per demo executive user, so
whatever mechanism enforces this has to work without BigQuery itself knowing who the
human on the other end of the conversation is.

## Options considered

1. **Native BigQuery row access policies**, filtering on `SESSION_USER()`. The original
   plan ([PLANNING.md](../../PLANNING.md)'s decision table, written Day 1). Doesn't work:
   `SESSION_USER()` returns the querying IAM principal, which is the same service account
   for every app user — BigQuery has no signal to distinguish them by.
2. **Trust the LLM-generated SQL to add the right `WHERE` filter.** Rejected outright —
   this is exactly the "enforce in the data layer, not the prompt" principle this project
   already commits to for PII masking; applying a weaker standard to the ACL than to PII
   would be inconsistent, and a generated query is adversarially influenceable.
3. **A parameterized table function**, where the orchestration code supplies the
   authenticated `app_user_id` as an explicit argument, combined with the SQL validator
   restricting the generated query's `FROM` clause to that function. The chosen option.

## Decision

Use **`fn_products_for_user(requesting_user_id STRING)`** and
**`fn_order_items_for_user(requesting_user_id STRING)`** (both in
`sql/bigquery_views.sql`) as the only way product- or order-item-level data is reachable.

1. The **id itself is never LLM-controlled** — the orchestration layer (trusted code, not
   a generated-SQL string) reads it from the authenticated session and injects it as a
   query parameter immediately before execution.
2. The **Day 4 SQL validator** (`sqlglot`) enforces that any query touching
   product/order-item data calls one of these two functions — a raw `SELECT` against
   `vw_products` or `vw_order_items` is rejected, not just discouraged.
3. Both functions share one underlying check: `EXISTS (... user_product_access WHERE
   acl.app_user_id = requesting_user_id AND acl.product_category = p.category)` — one ACL
   table, one rule, applied consistently at both grains.

## Consequences

- **Gained.** The filter is enforced the same way regardless of what the LLM generates —
  structurally unbypassable by prompt injection, not merely instructed against.
- **Accepted cost.** Two extra objects to maintain (vs. a single row access policy would
  have been, had it worked) and the validator now has one more thing to check (a
  function-call allowlist, not just a view allowlist).
- **Not decided.** Whether "products related to him" should also scope user-level/
  order-level aggregates that don't name a specific product — see
  [architecture.md](../architecture.md)'s "Open design questions."

## Validation

`sql/bigquery_views.sql` parses cleanly under `sqlglot`'s BigQuery dialect (8/8
statements, including both table functions). Not yet applied to the live
`opsfleet-tech-assignment` project — that's Day 3's "Done when" gate, including a test
that a cross-category query returns zero rows for a user without that category's access.
