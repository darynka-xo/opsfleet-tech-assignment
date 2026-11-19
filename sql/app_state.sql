-- Local application state for the retail data-analysis chat agent.
-- SQLite, created at runtime, gitignored (*.sqlite3) -- never BigQuery (AGENTS.md:
-- "BigQuery is read-only and stays read-only"). The agent reads AND writes here during a
-- normal conversation turn: saving/soft-deleting reports, updating learned preferences,
-- recording feedback. The LangGraph checkpointer manages its own tables in the same or an
-- adjacent SQLite file and isn't hand-designed here.
--
-- Design rationale: docs/architecture.md section "Data layer design".

CREATE TABLE IF NOT EXISTS saved_reports (
    report_id       TEXT PRIMARY KEY,  -- uuid4
    session_id      TEXT NOT NULL,     -- requirement 3's "from this conversation" handle
    app_user_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,     -- the generated report/insight text
    client_mentions TEXT,              -- JSON array of extracted entity names; powers "mentioning Client X"
    created_at      TEXT NOT NULL,     -- ISO 8601
    deleted_at      TEXT,              -- NULL = active; soft delete only, never hard-deleted
    deleted_by_turn TEXT                -- turn_id of the confirming delete, for the audit trail
);

CREATE INDEX IF NOT EXISTS idx_saved_reports_user ON saved_reports (app_user_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_saved_reports_session ON saved_reports (session_id);

-- Every state change to a report is recorded here, not just the final soft-delete -- the
-- confirmation flow itself (requested, confirmed, cancelled) is part of the audit trail,
-- not only its outcome.
CREATE TABLE IF NOT EXISTS report_audit_log (
    audit_id    TEXT PRIMARY KEY,
    report_id   TEXT NOT NULL REFERENCES saved_reports (report_id),
    action      TEXT NOT NULL,  -- 'created' | 'delete_requested' | 'delete_confirmed' | 'delete_cancelled'
    app_user_id TEXT NOT NULL,
    turn_id     TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_audit_report ON report_audit_log (report_id);

-- Requirement 4a (user-level learning). explicit_overrides always wins over
-- inferred_signals when the orchestrator builds the per-turn system prompt.
CREATE TABLE IF NOT EXISTS user_profiles (
    app_user_id       TEXT PRIMARY KEY,
    preferred_format  TEXT,     -- 'table' | 'bullet_points' | 'narrative' | NULL (unset)
    preferred_depth   TEXT,     -- 'summary' | 'detailed' | NULL
    prefers_charts    INTEGER,  -- 0 | 1 | NULL (unset)
    explicit_overrides TEXT,    -- JSON: preferences the user stated outright
    inferred_signals  TEXT,     -- JSON: rolling counters behind the behavioral inference
    updated_at        TEXT NOT NULL
);

-- Requirement 4b (system-level learning) + requirement 6 (QA) feed off this table; the
-- Day 6 curation script reads 'up' rows to propose new Golden Bucket candidates.
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    app_user_id TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    turn_id     TEXT NOT NULL,
    rating      TEXT NOT NULL,  -- 'up' | 'down'
    comment     TEXT,
    question    TEXT NOT NULL,  -- denormalized for the curation script, no join needed
    sql_query   TEXT,
    report_body TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback (rating, created_at);

-- Requirement 7 (observability). One row per graph-node execution, the source for every
-- rollup metric in docs/architecture.md section 7 (SQL success rate, self-correction
-- retry rate, latency per node, guardrail block rate). `attributes` is a small, curated
-- JSON object per node (counts, booleans, the SQL attempt number) -- never raw query
-- results or full report/SQL text, per AGENTS.md's "no raw query results in logs or
-- traces." `trace_id` equals `turn_id` in this prototype: a single process handling one
-- turn at a time has no distributed call graph to disambiguate, so a second identifier
-- would track turn_id one-for-one and add nothing real (a genuine multi-service
-- deployment would reintroduce a distinct trace_id).
CREATE TABLE IF NOT EXISTS agent_events (
    event_id    TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    turn_id     TEXT NOT NULL,
    trace_id    TEXT NOT NULL,
    node_name   TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    attributes  TEXT NOT NULL,  -- JSON object, node-specific, never row content
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_events_trace ON agent_events (trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_node ON agent_events (node_name, occurred_at);
