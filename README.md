# Retail Data Analysis Chat Agent

A CLI chat agent that answers natural-language questions about the
`bigquery-public-data.thelook_ecommerce` dataset over Gemini and BigQuery, built for
OpsFleet's AI Technical Assignment.

Status: Day 7 of 7 — the core agent graph, per-user brand ACL, the Golden Bucket,
guardrail hardening, destructive-ops (delete) confirmation flow, user-level learning, an
externalized persona, per-node telemetry, a feedback loop, and an eval harness are all
built and verified live. See [PLANNING.md](PLANNING.md) for the full roadmap and each
day's results.

## Repository structure

```text
.github/workflows/        # pre-commit, tests (3 OSes), type check
.vscode/                  # shared editor settings (ruff, markdownlint formatters)
config/persona.yaml       # the agent's tone/verbosity/formatting (requirement 8), hot-reloaded
data/
  golden_bucket_trios.yaml     # seeded few-shot (question, SQL, report) examples
  eval_regression_set.yaml     # held-out eval questions (requirement 6), distinct from the above
docs/
  decisions/              # ADR-NNN-kebab-title.md records, numbered sequentially
  architecture.md          # HLD + Mermaid diagrams, data layer, destructive-ops design
  YYYY-MM-DD_eval_report.md   # scripts/run_eval.py's output, one per run
scripts/
  day1_spike.py           # throwaway Gemini -> BigQuery -> print spike (Day 1 only)
  apply_bigquery_ddl.py   # applies sql/*.sql against the live BigQuery dataset
  seed_acl.py             # seeds the per-user brand ACL table
  embed_golden_bucket.py  # embeds data/golden_bucket_trios.yaml into Qdrant
  curate_golden_bucket_candidates.py  # Day 6: proposes new trios from 👍 feedback
  metrics_rollup.py       # Day 6: prints the observability metrics rollup
  run_eval.py             # Day 6: scores the agent against the eval regression set
  check_markdown_links.py # pre-commit hook: fails on a broken relative Markdown link
sql/
  bigquery_views.sql      # masked views + brand-scoped TVFs, applied via apply_bigquery_ddl.py
  app_state.sql           # local SQLite schema: saved_reports, user_profiles, feedback, agent_events
src/retail_agent/
  cli.py, graph.py, state.py, prompts.py   # the LangGraph agent and its CLI
  reports.py, profiles.py, persona.py, pii_scrub.py  # saved reports/destructive ops,
    user-level learning, persona config, output PII scrub
  telemetry.py, feedback.py  # Day 6: per-node observability, 👍/👎 capture
  data/bq_client.py       # client-provided BigQueryRunner — build on, don't restyle
  data/schema_context.py, sql_validator.py, resilience.py
  golden_bucket/, llm/    # Qdrant vector store, the LLMClient/Gemini adapter
tests/                    # tests for src/ and scripts/
.env.example
AGENTS.md                 # single source of truth for contribution conventions
CLAUDE.md                 # pointer to AGENTS.md
PLANNING.md               # the build roadmap, decisions, and each day's results
pyproject.toml            # project metadata, Ruff/mypy/pytest config
requirements.txt          # exact pinned versions (this project's lockfile equivalent)
requirements-dev.txt       # requirements.txt + pytest/ruff/mypy/pre-commit
```

## Key resources

- [PLANNING.md](PLANNING.md) — the build roadmap and design-decision table.
- [AGENTS.md](AGENTS.md) — contribution conventions (the rules a linter can't express).
- [docs/decisions/](docs/decisions) — ADRs for every non-obvious choice, numbered
  sequentially.
- Task tracking: none external for this assignment — PLANNING.md's day sections double
  as the task list.

## Team and ownership

Built by Daryn Moldash as a take-home technical assignment for OpsFleet.

## Working principles

- **Stack:** Python 3.14, LangGraph for orchestration, Gemini (Google AI Studio) for the
  LLM, BigQuery for data, Qdrant for the Golden Bucket vector store. See PLANNING.md's
  decision table for the "why" on each.
- `master` stays runnable. No secrets in git — see ".env.example" below and the gitleaks
  guidance further down. Every config surface that needs a secret gets its own
  `.env.example`.
- Mechanical conventions (formatting, lint, docstrings, commit format) are enforced by
  the tools in [AGENTS.md](AGENTS.md), not restated here.

## Development setup

Install Python 3.14 first, then:

```bash
python -m venv .venv
source .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e . --no-deps
pre-commit install
cp .env.example .env   # then fill in GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY
```

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e . --no-deps
pre-commit install
Copy-Item .env.example .env   # then fill in GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY
```

Run all checks, or a single one:

```bash
pre-commit run --all-files
pre-commit run ruff-check --all-files
```

Run the tests:

```bash
pytest -q -m "not integration"   # unit tests only
pytest -q -m integration         # needs real GCP/Gemini access (see AGENTS.md)
```

## Running the agent

```bash
python -m retail_agent --user mgr-denim   # or mgr-outdoor, mgr-basics, ceo (no restriction)
```

**Example run (real output, `mgr-denim` -- scoped to Calvin Klein + Levi's), 2026-11-19:**

```text
> what's going on with our sales?
Sales are on a strong upward trajectory, with monthly revenue more than
doubling from $7,200.25 in June to $15,105.14 in November. Aside from a minor
dip in September ($8,900.03, down from $9,790.43 in August), growth has been
consistent and accelerated sharply over the last two months.

Action items: Run a follow-up analysis breaking down the October and November
revenue spikes by product category and customer type (new vs. returning) to
isolate the exact growth drivers.

> how much revenue did we make from Hanes products?
We generated $0 in revenue from Hanes products. The data shows no non-cancelled
sales on record for this brand.

> who are our top 3 customers by spend?
The top 3 customers by spend are User 71291 ($377.00), User 49508 ($330.00),
and User 59 ($330.00). Spend among our highest-value buyers is tightly
clustered, with only a $47 gap between the number one purchaser and the two
tied for second place.

> and what about the lowest 3?
The three lowest-spending active customers in the 'mgr-denim' segment generated
between $10 and $12 in total revenue. Specifically, users 325 and 27884 spent
exactly $10.00 each, and user 350 spent $12.00.
```

That one transcript is doing double duty: the first question is deliberately vague ("what's
going on with our sales?") and still gets a direct, data-grounded answer; the second asks
about Hanes, a brand `mgr-denim` has no access to, and gets a truthful "$0" rather than
leaked data or a rejected question -- the brand-scoped ACL (requirement 2) holds without
`mgr-denim` ever naming a brand. The last question ("and what about the lowest 3?") only
makes sense against the previous turn, which the agent resolves from session history.

After each analysis answer, rate it (`+`/`-`/Enter to skip) -- this feeds
`scripts/metrics_rollup.py` and `scripts/curate_golden_bucket_candidates.py` (see
[docs/architecture.md](docs/architecture.md) §7). Deleting saved reports ("delete all the
reports we made in this conversation", "delete the reports mentioning Nike") always pauses
for an explicit yes/no before anything is removed.

### When gitleaks blocks a commit

- **Real secret:** remove it from the change, move it to `.env` (gitignored), and rotate
  the credential at its source. Never `--no-verify` past a real finding.
- **False positive:** scope the suppression as narrowly as possible — an inline
  `gitleaks:allow` comment first, then a `.gitleaks.toml` that extends the defaults, then
  a `.gitleaksignore` as a last resort. Only for a *confirmed* false positive.
