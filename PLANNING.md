# Retail Data Analysis Chat Agent — 7-Day Build Plan

Client: OpsFleet.
Dataset: `bigquery-public-data.thelook_ecommerce` (`orders`, `order_items`, `products`, `users`).

This file is the living roadmap. `docs/architecture.md` holds the HLD + Mermaid diagram +
detailed technical explanation. `CLAUDE.md` holds working conventions.
Each session/sprint's section lists goals, concrete tasks, the best-practice basis for the
approach, and the day's exit criteria ("Done when").

---

## Key design decisions made during planning (rationale lives in ADRs, Day 1–2)

| Area | Decision | Why |
| --- | --- | --- |
| Orchestration framework | **LangGraph** | Stateful graph with a durable checkpointer and a native `interrupt()` / `Command` resume primitive — this *is* the destructive-ops confirmation flow (req. 3) and the multi-turn memory (req. 4) almost out of the box. More production-hardened and better documented than Google ADK today; ADK is newer, Google Cloud-native, and thinner on docs/community — noted as the alternative if the client later wants a Vertex-only stack. |
| LLM provider | **Gemini**, via an abstracted `LLMClient` interface | Required by the brief for grading. Abstracted so a second provider can be dropped in without touching graph logic (the brief allows "another provider" but Gemini is what's graded). **Confirmed live on Day 1** against the actual AI Studio catalog (web research on model names turned out stale/unreliable — see ADR-002): `gemini-2.5-flash` for SQL-gen/routing/guardrail, `gemini-2.5-pro` for report synthesis, `gemini-embedding-001` for Golden Bucket embeddings. Direct `google-genai` SDK calls from LangGraph nodes, not the `langchain-google-genai` wrapper — see ADR-002. |
| Golden Bucket vector store | **Qdrant**, embedded/local mode for the prototype, behind a `VectorStore` interface | Same engine locally and in production (Qdrant Cloud or self-hosted) — zero infra for the prototype without a separate "swap to a different engine later" migration. Rationale in ADR-003. |
| PII masking | **BigQuery-native, view-level**: governed views in our own `retail_analytics` dataset simply never select the hard-PII columns — the agent's service account only ever sees these views, never the raw public-dataset tables | Industry consensus is "enforce in the data layer, not the prompt." **Refined Day 2**: Data Catalog policy tags don't apply here — `bigquery-public-data` isn't ours to attach a policy taxonomy to; column omission at the view layer gives the same "never retrievable" guarantee without that machinery. Full design in `docs/architecture.md` §4. |
| Per-user product ACL ("products related to him") | Synthetic `user_product_access` mapping table (demo seed data) + a parameterized BigQuery table function, not a native row access policy | `thelook_ecommerce` has no real owner/rep-to-product relationship, so this is a documented assumption: each demo executive user is assigned a set of product categories they may analyze. **Refined Day 2**: native row access policies filter by the querying IAM principal, but every app user shares one service account, so BigQuery can't tell them apart — see ADR-004. |
| Destructive-op confirmation | LangGraph `interrupt()` gating only the delete tool, resumed via `Command` after explicit "yes" | Matches current LangGraph HITL guidance: gate only risky/irreversible tools, not read-only ones; persist state via checkpointer so the pause survives across turns. |
| Persona/instructions | External `persona.yaml`, hot-reloaded every turn, no redeploy | Satisfies "CEO wants to change tone weekly." Versioned in git (or swappable for a Firestore doc in production) so changes are reviewable and revertible. |
| Observability | Structured JSON logs + OpenTelemetry spans using GenAI semantic-convention-style attribute names (`gen_ai.*`), correlated by `session_id`/`turn_id`/`trace_id` | The OTel GenAI conventions are still stabilizing (confirmed via research — some sources call them stable, others still "Development" status as of mid-2026), so conventions are treated as a versioned contract to align to, not a hard dependency. Exporter is pluggable (console/file for prototype, OTLP → Langfuse or Cloud Trace for production). |
| Golden Bucket updates | Human-curated, not auto-ingested | Positively-rated, novel Q→SQL→Report trios are queued as *candidates*; an analyst approves before they enter the bucket. Protects against prompt-injection or bad feedback poisoning the few-shot set. |

---

## Day 1 — Environment, Access, and a Thin End-to-End Spike

**Goal:** Prove the riskiest parts of the stack actually connect, before designing around them.

- GCP project creation, billing enabled, BigQuery API enabled; confirm free-tier 1TB/month compute is sufficient for the public dataset.
- Auth: Application Default Credentials set up; verify read access to `bigquery-public-data.thelook_ecommerce.*` using the provided `bq_client.py`.
- Gemini API key via Google AI Studio; note current rate limits for the chosen tier.
- Exploratory pass over the 4 required tables: row counts, key relationships (`orders.user_id` → `users.id`, `order_items.order_id/product_id`), and — critically — enumerate which columns are actual PII (`users.email`, `first_name`, `last_name`, `age`, `latitude/longitude`, address fields, etc.) so Day 2's masking design has a concrete column list instead of a guess.
- Repo scaffolding: `src/` package layout (empty, per architecture to be finalized Day 2 — do not design it blind today), `requirements.txt`, `.env.example`, lint/test tooling config.
- Risk-reduction spike (throwaway code, not the real graph): a 3-node LangGraph — Gemini call → BigQuery tool call → print result — to confirm the framework choice holds up before committing to it in the HLD.
- Write ADR-001 (framework choice) and ADR-002 (Gemini via AI Studio vs. Vertex AI, and why AI Studio for the prototype).

**Done when:** a throwaway script asks one hardcoded question, generates SQL via Gemini, executes it against BigQuery, and prints a result — end to end, once.

### Day 1 results (2026-11-18)

- GCP project `opsfleet-tech-assignment` created, billing linked, BigQuery API enabled; `gcloud` CLI + ADC both authenticated as `daryn.moldash@gmail.com`.
- `scripts/day1_spike.py` ran successfully: Gemini-generated SQL → BigQuery execution → printed result (124,780 total orders), validating the LangGraph + Gemini + BigQuery round trip (ADR-001).
- Gemini model catalog confirmed live (ADR-002) — model names from pre-access web research turned out unreliable; direct API introspection (`client.models.list()`) is now the standard way this project resolves "what model/version is current," not web search.
- Full schema pulled for all 4 required tables. **PII inventory for `users`** (16 cols): hard PII to mask entirely — `first_name`, `last_name`, `email`, `street_address`, `postal_code`, `city`, `latitude`, `longitude`, `user_geom`. Demographic/quasi-identifier, keep visible but aggregate-oriented — `age`, `gender`, `state`, `country`, `traffic_source`. `orders`/`order_items`/`products` have no direct PII; `order_items.user_id` and `orders.user_id` are the join keys the row-level ACL (per-user product access) will need to filter through.
- Environment note: Python 3.14.3 + the full dependency set (LangGraph, Qdrant, BigQuery, Gemini SDKs) installed and tested clean — no compatibility issues despite 3.14 being very new. One cosmetic-only quirk: `qdrant-client`'s embedded-mode cleanup (`portalocker`) throws a noisy `ModuleNotFoundError` during Python's own interpreter shutdown on 3.14 — happens in `__del__` after the client has already done its job, doesn't affect any actual read/write.
- **Tooling quirk worth knowing**: in this Bash tool, `/c/Users/...` does not resolve `AppData` (sandboxed), but `/cygdrive/c/Users/...` does — needed to invoke `gcloud` by full path after a winget install. Mirrored into CLAUDE.md.

### Day 1 addendum — repo-bootstrap best-practices audit (2026-11-18)

Before committing Day 1, audited this repo against an external repository-bootstrap guide
(generic Python-service best practices, not specific to this project). Gaps found and
closed, all same-day:

- **Single convention file.** Moved all judgment-level rules out of `CLAUDE.md` into
  [`AGENTS.md`](AGENTS.md) (the emerging cross-tool standard), with `CLAUDE.md` reduced to
  a one-line pointer. No rules duplicated between them.
- **Proper installable package.** `pyproject.toml` gained a real `[project]` table +
  `[build-system]` (hatchling), so `pip install -e .` works instead of the `pythonpath`
  test-only hack and the spike script's manual `sys.path.insert`.
- **Extended lint coverage.** Ruff now also enforces `B`, `SIM`, `D` (Google-style
  docstrings), `ANN` (type annotations), `T20` (no stray `print`), `BLE` (no blind
  `except`), `PTH` (`pathlib` over `os.path`), `LOG`/`G`, and `RUF100` (no stale `noqa`) —
  previously only `E`/`F`/`I`/`UP` were prose-only conventions.
- **Pre-commit + CI.** Added `.pre-commit-config.yaml` (ruff, markdownlint-cli2, taplo,
  **gitleaks** secret scanning, `typos`, Conventional Commits, and a relative-Markdown-
  link checker) and `.github/workflows/` (`pre-commit`, `tests` on ubuntu/windows/macos,
  `mypy` type-check) — all run and verified locally before being committed; the workflows
  activate once Day 7 pushes to a real GitHub remote.
- **Supporting config.** `.gitattributes` (LF normalization on Windows),
  `.markdownlint.json`, `_typos.toml`, `.vscode/{extensions,settings}.json`, and
  `docs/decisions/0000-template.md`.
- **README rewritten** to the guide's structure (repo-structure tree, key resources,
  working principles, dev setup with both `bash` and `powershell` blocks, gitleaks
  false-positive procedure, known limitations).
- **Deliberately not adopted:** migrating `pip`/`venv` to `uv`. The full dependency stack
  (LangGraph, Qdrant, BigQuery, Gemini SDKs) was already verified working end-to-end on
  Python 3.14.3 on Day 1; re-resolving it under a different tool mid-build trades schedule
  risk for a cosmetic change with no effect on grading. Recorded as a locked decision in
  AGENTS.md rather than silently skipped.
- **Deliberately not renamed:** `docs/decisions/ADR-NNN-title.md` kept as-is rather than
  moved to the guide's `docs/decision-records/NNNN-kebab-title.md` — only two ADRs existed
  and renaming would just be churn; the "one location, sequential numbering, one-screen
  size" principles behind it were adopted as-is.
- **Deliberately scaled back:** Dependabot and the Copilot/Cursor pointer files were
  built and verified working, then removed — this is a one-person 7-day take-home, not a
  long-lived team repo with other humans and other AI tools to onboard, and weekly
  dependency-update PRs for a project that ships once add no value here. The GitHub
  Actions CI workflows were kept: they prove the three checks (`pre-commit`, tests on 3
  OSes, mypy) actually pass on a clean machine, not just this one, which matters for a
  grader who clones and runs it.

All of the above re-verified after the change: `ruff check .`, `mypy src`, `pytest -q`
(7 passed), `pre-commit run --all-files` (all hooks pass), and the Markdown link checker
against every `.md` file in the repo — all clean.

---

## Day 2 — HLD, Architecture Diagram, and Schema Design (design-heavy, minimal code)

**Goal:** Produce the actual HLD deliverable and lock the data contracts everything else builds on.

- `docs/architecture.md`: Mermaid component diagram (CLI → Orchestrator/LangGraph → {Golden Bucket retriever, SQL-gen, validator, BigQuery, Report synthesis, Memory/Persona, Observability}) plus the data flow narrative.
- Detailed technical explanation doc, sections a–d drafted now (finalized Day 7): cloud services/framework reasoning, data flow, error/fallback strategy, setup instructions skeleton.
- Requirements-traceability table: each of the 8 numbered requirements mapped to the exact component/section that satisfies it, so nothing gets silently dropped between now and Day 7.
- Schema design (as DDL, not yet applied): masked authorized views over the 4 tables, `user_product_access` ACL table, `golden_bucket_trios` table, `saved_reports` table (with soft-delete + audit columns), `user_profiles` table, `feedback` table.
- Decide and document the destructive-op resolution semantics precisely: "reports mentioning Client X" (content match, scoped to the user's own reports) vs. "reports from this conversation" (tracked via `session_id` in the saved-report record, not re-matched by content) — this ambiguity needs to be nailed down in design, not improvised in code.

**Done when:** the architecture diagram and traceability matrix exist and every requirement in the brief has a named home.

### Day 2 results (2026-11-18)

- [docs/architecture.md](docs/architecture.md) written: Mermaid component diagram, a
  full data-flow walkthrough, sections a–d, and the requirements-traceability matrix
  (all 8 numbered requirements from the brief, each with a named component and section).
  The diagram itself was render-verified for real (pasted into the public Mermaid Live
  Editor, not just assumed from syntax) — renders with zero parse errors.
- Schema design corrected a real gap found while actually designing it, not just listed:
  the original Day 2 scope ("`user_product_access`, `golden_bucket_trios`,
  `saved_reports`, `user_profiles`, `feedback` tables") would have put live-write
  application state in BigQuery, which conflicts with the locked "BigQuery stays
  read-only" decision. Resolved by splitting storage: BigQuery holds only the governed
  views and the (admin-seeded, agent-read-only) `user_product_access` table
  ([sql/bigquery_views.sql](sql/bigquery_views.sql)); `saved_reports`,
  `report_audit_log`, `user_profiles`, and `feedback` live in local SQLite
  ([sql/app_state.sql](sql/app_state.sql)), which the agent can freely read and write
  at runtime; Golden Bucket trios live in Qdrant. Both SQL files were syntax-validated for
  real (`sqlglot`'s BigQuery dialect for the BigQuery DDL; executed against a real
  in-memory SQLite database for the app-state DDL) — not just eyeballed.
- Per-user product ACL mechanism also corrected during design: BigQuery's native row
  access policies can't actually distinguish app users (they all share one service
  account), so the real mechanism is a parameterized table function with a
  trusted-code-injected user id, enforced by the Day 4 SQL validator. Written up as
  [ADR-004](docs/decisions/ADR-004-per-user-product-acl.md).
- Destructive-op resolution semantics nailed down precisely (architecture.md §5):
  content-match delete vs. session-scoped delete are different queries against different
  fields, both always scoped to the requesting user's own reports.
- `PLANNING.md`'s decision table rows for PII masking and the per-user ACL updated to
  match the corrected mechanisms above, rather than left stale against the new design doc.

### Day 2 addendum — security and brief-compliance audit (2026-11-18)

Re-read the original brief in full against `docs/architecture.md` as it stood at the end
of Day 2. Found and fixed, same day:

- **Log/trace safety** wasn't stated anywhere — a span or log line could in principle
  capture a raw query result. Fixed: AGENTS.md's new "Security" section and
  `architecture.md` §7 both now say explicitly that query text/timing/row-counts are
  loggable, row *content* never is.
- **Confirmation-gate integrity** wasn't explicit — nothing said the destructive-op "yes"
  must be a raw, code-checked input rather than an LLM's judgment call, which is the
  obvious prompt-injection angle on the brief's own "high-stakes oversight" language.
  Fixed: `architecture.md` §5 now states this as a hard rule.
- **Local SQLite injection hygiene** wasn't a stated rule, even though `saved_reports`/
  `feedback` store user-influenced free text. Fixed: added to AGENTS.md's Security section.
- **Multi-step/comparative queries** — the brief's own example questions ("why did churn
  spike," "how does state X compare to state Y") and "Expected Agent Capabilities"'s
  explicit "Multi step queries" line weren't served by the original one-SQL-per-turn
  design (retries only covered *errors*, not "I need another angle to answer this").
  Fixed: the existing self-correction loop's trigger was broadened (same graph topology,
  same retry budget) to also fire when the synthesizer flags insufficient data — see
  `architecture.md` §2 step 7 and §c.
- **"Your level of experience with [the framework]"** (brief deliverable 6) and
  **extensibility** (explicit in the task description, not one of the 8 numbered
  requirements) were never written into any doc. Fixed: added to `architecture.md` §a and
  a new §9 respectively.
- **"Action items" in reports** — the brief asks for this twice; the Report Synthesizer
  was described as producing insights only. Fixed: `architecture.md` §2 step 8 now says
  insights *and* action items for a full report.

---

## Day 3 — Data Layer: Masking, ACLs, and the Golden Bucket

**Goal:** Build the layer the agent is structurally forced to go through.

- Apply Day 2's DDL: `sql/bigquery_views.sql` (governed views, `user_product_access`, the two per-user table functions) against BigQuery, and `sql/app_state.sql` against a local SQLite file.
- Grant the agent's service account `roles/bigquery.dataViewer` on the `retail_analytics` dataset only (AGENTS.md's "Security" section) — confirm it cannot query `bigquery-public-data.thelook_ecommerce` directly once this is in place.
- Seed `user_product_access` with a handful of demo executive users and category sets; write a test that a cross-category query through `fn_products_for_user` returns zero rows for a user without that category's access.
- Hand-author 15–30 Golden Bucket trios (question → SQL → analyst-style report) spanning the four required capability areas (customer behavior, product performance, time-based metrics, schema/meta questions).
- Implement `VectorStore` interface + Qdrant adapter; embed trios with the Gemini embedding model; implement top-k retrieval with a diversity constraint (not just raw nearest-neighbor), per current text-to-SQL RAG practice.
- Dynamic schema introspection: wire `BigQueryRunner.get_table_schema` into the prompt pipeline so table/column context is live, not hardcoded.
- Unit tests: retrieval sanity-checks (a handful of queries retrieve topically-correct neighbors), masking tests (a raw-PII column is provably unreachable through the view).

**Done when:** a standalone script embeds a sample question and returns sensible top-k golden examples, and a direct query attempt against a masked column returns no PII.

---

## Day 4 — Core Agent Graph: NL → SQL → Execution → Answer

**Goal:** The main conversational loop, working end to end on real (masked) data.

- LangGraph graph: intent router → safety/guardrail gate → golden-bucket retrieval → SQL generation (Gemini + live schema + few-shot examples) → static SQL validation (`sqlglot`: SELECT-only, view whitelist, single statement) → BigQuery dry-run → execution → report/insight synthesis.
- Self-correction loop: on syntax error, empty result, *or* the synthesizer flagging that the question needs another query (comparative/causal questions — see `docs/architecture.md` §2 step 7), feed back into the SQL-gen node and retry, bounded (e.g. 2 retries total across both triggers) before synthesizing from what's available or a graceful "I couldn't fully answer that" fallback.
- Resilience: `tenacity`-based retry/backoff + circuit breaker around every Gemini and BigQuery call; per-turn token/cost budget guard.
- LangGraph checkpointer (SQLite) wired up for multi-turn state.
- CLI chat loop (`rich` + `prompt_toolkit`): readable tables/markdown, spinners during tool calls.

**Done when:** a multi-turn CLI conversation answers questions in all four required capability categories against masked data, and survives a deliberately broken query without crashing.

---

## Day 5 — Safety Hardening, Destructive Ops, Personalization, Persona

**Goal:** Everything the brief calls out as "design required, prototype required for a–d."

- Guardrail/intent classifier rejecting off-topic and prompt-injection attempts before they ever reach SQL generation; blocked attempts logged.
- Output-side PII scrub as a second defense layer (belt-and-suspenders on top of the Day 3 masked views).
- Saved Reports library (create/list/soft-delete) + the destructive-ops flow: parse delete intent → resolve candidate set (scoped to the requesting user's own reports, per the two semantics nailed down Day 2) → `interrupt()` to show the exact list and count → require explicit confirmation → soft-delete + audit log entry on confirm.
- User-level learning: preference profile (format, depth) with explicit updates (user states a preference) and inferred updates (consistent behavior over several turns), injected into the system prompt per user.
- Persona externalized to `persona.yaml` (tone, default verbosity, formatting rules), hot-reloaded every turn, versioned in git.

**Done when:** "delete all reports mentioning Client X" and "delete all reports from this conversation" both work, both require an explicit confirm step, and a user cannot delete or see another user's reports or PII under adversarial prompting.

---

## Day 6 — Observability, System-Level Learning, Evaluation

**Goal:** Make failures visible, make the agent improvable, and prove it works before calling it done.

- Instrument OTel-style spans per node (`gen_ai.*`-style attributes) correlated by `session_id`/`turn_id`/`trace_id`; structured JSON logs.
- A minimal metrics rollup (even a local `agent_events` table is fine): SQL success rate, self-correction retry rate, latency per node, guardrail block rate, token/cost per session, 👍/👎 rate.
- 👍/👎 feedback capture per answer → `feedback` table; a curation script surfacing candidate new Golden Bucket trios from positively-rated novel Q&As for human approval (the system-level learning loop, deliberately human-gated).
- Eval harness: a held-out golden regression set (distinct from the Golden Bucket few-shot examples) scored on SQL execution accuracy, result correctness, and an LLM-as-judge rubric for report quality (faithfulness, relevance, format-preference adherence); produces a written eval report.
- Run and record the first eval baseline; fix anything that regresses before moving on.

**Done when:** a trace for any given turn can be pulled by `trace_id` and shows every node's input/output, and the eval harness produces a scored report against the regression set.

---

## Day 7 — Hardening, Docs, Demo, Submission

**Goal:** Ship something a stranger can clone and run.

- Manual QA pass across edge cases: ambiguous questions, injected SQL errors, simulated Gemini timeout, off-topic/malicious prompts, destructive-op with zero matches, destructive-op cancel, cross-user ACL probing.
- Finish the technical explanation doc (section e: requirement-by-requirement walkthrough) and the README (setup instructions + a pasted example run transcript).
- Final lint/type-check/test pass; pin `requirements.txt`.
- Full self-review against every bullet in the original brief (the Day 2 traceability matrix makes this a checklist, not a guess).
- Push to a public GitHub repo.

**Done when:** a fresh machine can follow the README alone, install deps, set two env vars, and hold a working conversation with the agent.
