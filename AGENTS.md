# Contribution conventions

This is the single canonical source of contribution conventions for this repository,
written for both human contributors and AI coding assistants. Tool-specific assistant
files (`CLAUDE.md`) are thin pointers to this file — do not copy rules into them.

Mechanical rules are enforced by tooling and are **not** restated here:

- Formatting, lint, import order, docstring, annotation, `print`, blind-except and
  `pathlib` rules — Ruff (`pyproject.toml`'s `[tool.ruff]`, run via pre-commit and CI).
- Spelling (US English) — typos.
- Markdown style — Markdownlint (`.markdownlint.json`).
- Commit message format — conventional-pre-commit.
- JSON/TOML formatting — pre-commit hooks.
- Secrets — gitleaks.

This document covers only the judgment-level conventions a linter cannot express.
Environment and setup instructions live in the [README](README.md); the project
roadmap and architecture rationale live in [PLANNING.md](PLANNING.md).

If a rule here ever conflicts with an established convention already in this repo, keep
the repo's convention and raise the divergence rather than silently overriding it.

## Current stage

Day-by-day build per [PLANNING.md](PLANNING.md)'s 7-day roadmap. Work strictly in the
current day's scope unless the user redirects — don't jump ahead to a later day's
component just because it's adjacent or easy.

## Locked architecture decisions (don't re-litigate without the user)

- Orchestration: LangGraph. LLM: Gemini, behind an `LLMClient` abstraction. Vector store:
  Qdrant (embedded/local mode for the prototype, same engine as the production path),
  behind a `VectorStore` interface. See PLANNING.md's decision table and the ADRs in
  `docs/decisions/` for the "why" on each.
- BigQuery is read-only and stays read-only. The agent's service account must only ever
  be able to query masked authorized views, never the raw `thelook_ecommerce` tables —
  enforced at the BigQuery IAM/policy level, not by prompting the model to behave.
- Any destructive action (report deletion) goes through a LangGraph `interrupt()` +
  explicit confirmation, no exceptions, even for "delete all my reports."
- Persona/system-prompt text lives in an external config file, never hardcoded in
  Python — it must be editable by a non-engineer without a redeploy.
- Dependency/environment tooling: **pip + venv + pinned `requirements.txt`**, not `uv`.
  A generic repo-bootstrap guide consulted on 2026-11-18 defaults to `uv`; we deliberately
  kept pip since the full stack (LangGraph, Qdrant, BigQuery, Gemini SDKs) was already
  verified working end-to-end on Python 3.14.3 on Day 1, and migrating tooling mid-build
  trades schedule risk for a cosmetic improvement with no effect on grading. Documented as
  a deliberate deviation, not an oversight.

## Language

Use US English throughout — code symbols, docstrings, log messages, comments, commit
messages, and documentation. Third-party identifiers keep their upstream spelling
(`yaml.SafeLoader`, BigQuery/Gemini API field names, and so on).

## File names

`snake_case` for Python files. `kebab-case` for Markdown and media; preserve
ecosystem-standard names such as `README.md` and `pyproject.toml`. Dated documents use
`YYYY-MM-DD_description.md`.

Exception already in this repo: `src/retail_agent/data/bq_client.py` keeps the client's
original name and contents — see "Third-party code" below — rather than being renamed or
restyled to fit house conventions.

## Cross-platform support

The CLI and any documented workflow must work on Windows and macOS/Linux.

- Use `pathlib` for local filesystem paths; keep any URI/BigQuery path POSIX-style on
  every OS.
- Give a `bash` and a `powershell` command block wherever they differ (README).
- Keep tests deterministic and independent of network/model access; anything that calls
  BigQuery or Gemini for real is opt-in (`@pytest.mark.integration`) and documented.

## Third-party code

`src/retail_agent/data/bq_client.py` is provided as-is by the client (OpsFleet) — build on
top of it, don't restyle or replace it, unless a real limitation is found, and then
document the limitation before working around it. It is excluded from Ruff
(`pyproject.toml`'s `[tool.ruff] exclude`) for this reason, not because its style is the
house style.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`,
imperative mood ("add", not "added"/"adds"). Types: `feat`, `fix`, `docs`, `refactor`,
`test`, `build`, `ci`, `chore`. Subject at or under 72 characters; the *why* goes in the
body.

## Docstrings (Python)

Public modules, classes, functions, and methods get a Google-style docstring explaining
*what* and *why*, not *how*. Document raised exceptions and visible side effects. Update
the docstring in the same change as any signature or behavior change. No
reStructuredText/NumPy style, and no multi-paragraph docstring where a sentence suffices.

Prefer self-explanatory names over inline comments. Add an inline comment only when the
*why* is non-obvious — a hidden constraint, an invariant, a deliberate workaround. Don't
narrate *what* the code plainly does, and don't reference the current task or caller.

## Type annotations (Python)

Annotate every public function — all parameters and the return type, including `-> None`.
Use `X | None` (not `Optional[X]`), precise generics (`list[str]`, `dict[str, int]`), and
avoid bare `Any`. New modules start with `from __future__ import annotations`.

## Logging (Python)

Standard `logging` module, a module-level `logger = logging.getLogger(__name__)`. No
`print()` for diagnostics in `src/` — the Day 1 `scripts/` spike is exempt (see
`pyproject.toml`'s per-file-ignores) since one-off scripts print results by design.

## Exception handling (Python)

1. Catch specific exceptions. A broad `except Exception`/`except:` is allowed only at a
   library or batch boundary, with an inline comment saying why.
2. Never swallow silently — log before suppressing or re-raising.
3. Wrap with context at layer boundaries: `raise <YourError>("…") from e`.

## Security

- **No raw query results in logs or traces.** A log line or OTel span may record a SQL
  query's text, its timing, and its row/column count — never the row *content*. Requirement
  2's PII ban applies to anything the system writes down, not only the chat response.
- **Local SQLite writes are always parameterized** (`?` placeholders), never an f-string
  or `.format()`-built SQL string — `saved_reports`/`feedback` store user-influenced free
  text (titles, comments), so this isn't optional hygiene.
- **A destructive-op confirmation is a raw, code-checked input**, never an LLM's judgment
  that the conversation "sounds like" agreement — see `docs/architecture.md`'s
  "Confirmation integrity."
- The agent's BigQuery service account gets `roles/bigquery.dataViewer` scoped to the
  `retail_analytics` dataset only — never project-wide, never on `bigquery-public-data`
  directly.

## No premature abstraction

Build what the current day's task needs, not a hypothetical future one. The
`VectorStore`/`LLMClient` interfaces are the deliberate exception — they exist because the
brief explicitly asks for provider/infra swappability, not because of a general preference
for interfaces.

## Dependency constraints (Python)

`requirements.txt` holds exact pinned versions (captured via `pip freeze`) as this
project's reproducibility guarantee, in place of a `uv.lock`. When `pyproject.toml`
declares a dependency range, follow the two-rule default:

1. `>=x.y,<(x+1)` for libraries that follow semver.
2. `==x.y.z` for libraries that document that minor versions may break compatibility, with
   a comment explaining why.

## Suppression comments

Fix the underlying issue rather than silence a linter. Any `# noqa`, `# type: ignore`, or
equivalent needs an inline explanation. The same applies to gitleaks: never allow-list a
**real** secret to make a commit pass — remove it, move it to `.env`, and rotate it.
Suppress only a confirmed false positive, as narrowly as possible (README documents the
how-to).

## Decision records

Record any significant design decision in `docs/decisions/ADR-NNN-kebab-title.md`
(sequential numbering, status, context, decision, consequences — see the existing
ADR-001/ADR-002 for the shape, and `docs/decisions/0000-template.md` for a blank one). Keep
it to about one screen. This repo's `docs/decisions/` folder, not `docs/decision-records/`,
is the one location for these — don't start a second one.

## Tests

Deterministic and offline by default. Name test files/functions for the behavior they
protect. Every security or robustness fix ships with a regression test. Mark tests that
need real external resources (`@pytest.mark.integration`) and skip them unless the
documented environment variable is set.

## Open assumptions to keep visible (carry into the final submission doc)

- `thelook_ecommerce` has no real "this product belongs to this user" relationship, so the
  per-user product ACL (requirement 2) is a synthetic demo mapping, documented as such.
- "Delete all the reports we made in this conversation" is resolved by `session_id`
  tracked on each saved report, not by re-matching content — documented in PLANNING.md
  Day 2.

## Useful references

- `src/retail_agent/data/bq_client.py` (provided by the client) — the only BigQuery access
  pattern to build on top of, not replace, unless a limitation is found and documented.
- Dataset: `bigquery-public-data.thelook_ecommerce` — `orders`, `order_items`, `products`,
  `users`. Full schema + PII column inventory recorded in PLANNING.md's Day 1 results.
- GCP project: `opsfleet-tech-assignment`, billing linked, BigQuery API enabled. `.env` has
  `GOOGLE_CLOUD_PROJECT` and `GEMINI_API_KEY` set — never read or print `.env` contents
  directly (it's gitignored and contains a live key); use `dotenv.load_dotenv()` in code
  instead.
- Confirmed Gemini models (don't re-guess from web search — query `client.models.list()` if
  this ever needs re-checking): `gemini-2.5-flash` (SQL-gen/routing), `gemini-2.5-pro`
  (report synthesis), `gemini-embedding-001` (Golden Bucket embeddings). Rationale in
  `docs/decisions/ADR-002-llm-provider.md`.
- Local environment: Python 3.14.3, deps in `.venv/` (gitignored), installed from
  `requirements-dev.txt`. `gcloud` CLI is installed via winget at
  `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd` and on the user-level
  PATH for new terminals. **Bash-tool quirk:** `/c/Users/...` does not resolve `AppData` in
  this sandbox; use `/cygdrive/c/Users/...` instead when invoking gcloud or touching anything
  under AppData from the Bash tool.
