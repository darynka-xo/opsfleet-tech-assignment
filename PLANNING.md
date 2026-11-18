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
| PII masking | **BigQuery-native**: Data Catalog policy tags (column-level) + row access policies (row-level), enforced so the agent's service account can only ever see masked authorized views — never raw tables | Industry consensus is "enforce in the data layer, not the prompt." The LLM should be structurally incapable of seeing raw PII, not merely instructed not to show it. Guardrail/output scrub is a second, belt-and-suspenders layer, not the primary control. |
| Per-user product ACL ("products related to him") | Synthetic `user_product_access` mapping table (demo seed data) + row access policy filter | `thelook_ecommerce` has no real owner/rep-to-product relationship, so this is a documented assumption: each demo executive user is assigned a set of product categories/brands they may analyze. |
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

---

## Day 3 — Data Layer: Masking, ACLs, and the Golden Bucket

**Goal:** Build the layer the agent is structurally forced to go through.

- Apply Day 2's DDL: masked authorized views, column policy tags on PII columns, row access policies keyed by `user_product_access`.
- Seed `user_product_access` with a handful of demo executive users and category sets.
- Hand-author 15–30 Golden Bucket trios (question → SQL → analyst-style report) spanning the four required capability areas (customer behavior, product performance, time-based metrics, schema/meta questions).
- Implement `VectorStore` interface + Qdrant adapter; embed trios with the Gemini embedding model; implement top-k retrieval with a diversity constraint (not just raw nearest-neighbor), per current text-to-SQL RAG practice.
- Dynamic schema introspection: wire `BigQueryRunner.get_table_schema` into the prompt pipeline so table/column context is live, not hardcoded.
- Unit tests: retrieval sanity-checks (a handful of queries retrieve topically-correct neighbors), masking tests (a raw-PII column is provably unreachable through the view).

**Done when:** a standalone script embeds a sample question and returns sensible top-k golden examples, and a direct query attempt against a masked column returns no PII.

---

## Day 4 — Core Agent Graph: NL → SQL → Execution → Answer

**Goal:** The main conversational loop, working end to end on real (masked) data.

- LangGraph graph: intent router → safety/guardrail gate → golden-bucket retrieval → SQL generation (Gemini + live schema + few-shot examples) → static SQL validation (`sqlglot`: SELECT-only, view whitelist, single statement) → BigQuery dry-run → execution → report/insight synthesis.
- Self-correction loop: on syntax error or empty result, feed the error back to the SQL-gen node and retry, bounded (e.g. 2 retries) before a graceful "I couldn't answer that" fallback.
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
