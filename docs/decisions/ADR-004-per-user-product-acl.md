# ADR-004: Per-user product ACL enforcement mechanism

**Status:** Accepted (applied live; confirmed and corrected against the client's real
answer on 2026-11-19 -- see "Update" below)

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

Use four parameterized table functions (all in `sql/bigquery_views.sql`) as the *only*
way any analytical data is reachable — no `vw_*` base view is in the SQL validator's
whitelist:

- `fn_products_for_user(requesting_user_id STRING)`
- `fn_order_items_for_user(requesting_user_id STRING)`
- `fn_orders_for_user(requesting_user_id STRING)`
- `fn_customers_for_user(requesting_user_id STRING)`

1. The **id itself is never LLM-controlled** — the orchestration layer (trusted code, not
   a generated-SQL string) reads it from the authenticated session and injects it as a
   query parameter immediately before execution.
2. The **Day 4 SQL validator** (`sqlglot`) enforces that any generated query calls one of
   these four functions — a raw `SELECT` against any `vw_*` view is rejected outright, not
   just discouraged.
3. `fn_products_for_user`/`fn_order_items_for_user` share one underlying check against
   `user_brand_access`; `fn_orders_for_user`/`fn_customers_for_user` are each defined as
   "rows reachable through at least one in-scope order item" (a nested `EXISTS` against
   `fn_order_items_for_user`, confirmed to work in live BigQuery, not assumed).
4. `'ceo'` is a reserved `app_user_id` that bypasses the ACL check in every function
   (`requesting_user_id = 'ceo' OR EXISTS (...)`) — not ~2,800 seeded rows, one per brand.

## Update (2026-11-19) — corrected against the client's real answer

Everything above about the *mechanism* (table function, trusted parameter, validator
whitelist) held up. Two things about the *grain* did not, and were corrected once the
client actually answered (see PLANNING.md's "Day 4 addendum"):

- **ACL grain is brand, not category.** "Each manager can analyze only the brands they
  manage" — confirmed. `user_brand_access` (renamed from `user_product_access`) is keyed
  by `brand`, not `product_category`. thelook_ecommerce has ~2,800 distinct brands vs. 25
  categories — category remains a valid analysis dimension (`GROUP BY category` still
  works fine inside a user's already-brand-scoped products), it's just never the access
  check itself.
- **Order and customer data needed their own scoping, not just products/order-items.**
  "Customer and order data are visible only through purchases of in-scope products" —
  this wasn't in the original design (`vw_orders`/`vw_users_masked` were originally
  planned as freely queryable). `fn_orders_for_user`/`fn_customers_for_user` close that
  gap: an order or a customer is reachable only if at least one of their order items is
  for an in-scope brand.
- **CEO sees everything** — not previously designed for at all; added as the `'ceo'`
  bypass above rather than a brute-force full-brand seed.

## Consequences

- **Gained.** The filter is enforced the same way regardless of what the LLM generates —
  structurally unbypassable by prompt injection, not merely instructed against. Now
  correct against the client's actual access model, confirmed by asking rather than by
  shipping the original (wrong) category-based guess to Day 7.
- **Accepted cost.** Four objects to maintain instead of two; the validator's whitelist is
  function-only now, with zero views exposed directly.
- **Not decided.** Whether "ceo" should be the literal reserved id in a real deployment,
  or a role claim resolved from an actual auth system — fine for a prototype, a real
  gap for production hardening.

## Validation

Applied live to `opsfleet-tech-assignment.retail_analytics`. Verified for real (not
assumed): `mgr-denim` sees exactly `{Calvin Klein, Levi's}` and nothing else; an unknown
user sees zero brands; `'ceo'` sees the same brand set as an unrestricted query;
`fn_orders_for_user('mgr-denim')` returns more than zero and fewer than the total order
count. Full conversation transcripts in PLANNING.md's Day 4 results.
