# Retail Data Analysis Chat Agent

A CLI chat agent that answers natural-language questions about the
`bigquery-public-data.thelook_ecommerce` dataset over Gemini and BigQuery, built for
OpsFleet's AI Technical Assignment.

Status: environment, access, and a risk-reduction spike are done; the real agent graph is
next. See [PLANNING.md](PLANNING.md) for the full roadmap.

## Repository structure

```text
.github/workflows/        # pre-commit, tests (3 OSes), type check
.vscode/                  # shared editor settings (ruff, markdownlint formatters)
docs/
  decisions/              # ADR-NNN-kebab-title.md records, numbered sequentially
  architecture.md          # HLD + Mermaid diagram (added Day 2)
scripts/
  day1_spike.py           # throwaway Gemini -> BigQuery -> print spike (Day 1 only)
  check_markdown_links.py # pre-commit hook: fails on a broken relative Markdown link
src/retail_agent/
  data/bq_client.py       # client-provided BigQueryRunner — build on, don't restyle
tests/                    # tests for src/ and scripts/
.env.example
AGENTS.md                 # single source of truth for contribution conventions
CLAUDE.md                 # pointer to AGENTS.md
PLANNING.md               # the build roadmap, decisions, and rationale
pyproject.toml            # project metadata, Ruff/mypy/pytest config
requirements.txt          # exact pinned versions (this project's lockfile equivalent)
requirements-dev.txt       # requirements.txt + ruff/mypy/pre-commit
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
- `main` stays runnable. No secrets in git — see ".env.example" below and the gitleaks
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
pytest -q                    # unit tests only
pytest -q -m integration     # needs real GCP/Gemini access (see AGENTS.md)
```

### When gitleaks blocks a commit

- **Real secret:** remove it from the change, move it to `.env` (gitignored), and rotate
  the credential at its source. Never `--no-verify` past a real finding.
- **False positive:** scope the suppression as narrowly as possible — an inline
  `gitleaks:allow` comment first, then a `.gitleaks.toml` that extends the defaults, then
  a `.gitleaksignore` as a last resort. Only for a *confirmed* false positive.
