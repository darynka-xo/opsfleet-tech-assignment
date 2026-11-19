"""The core LangGraph agent graph.

NL question -> SQL -> execution -> report, plus the destructive-ops flow (DeleteParse ->
ResolveCand -> Confirm -> Persist in docs/architecture.md's diagram) for deleting saved
reports.

The confirm step uses LangGraph's `interrupt()`: the graph pauses mid-run, the CLI shows
the exact candidate list and count, and only a raw, code-checked yes/no resume answer
(never an LLM's judgment that the conversation "sounds like" agreement) proceeds to the
soft-delete -- see AGENTS.md's Security section and docs/architecture.md's "Confirmation
integrity."
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from google.cloud import bigquery
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from retail_agent import persona as persona_module
from retail_agent import profiles, reports, telemetry
from retail_agent.data.bq_client import BigQueryRunner
from retail_agent.data.schema_context import build_schema_context
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.llm.client import GeminiClient
from retail_agent.pii_scrub import scrub_pii
from retail_agent.prompts import (
    DELETE_PARSE_SYSTEM,
    GUARDRAIL_SYSTEM,
    ROUTER_SYSTEM,
    build_sql_generation_prompt,
    build_synthesis_prompt,
)
from retail_agent.resilience import CircuitBreaker, call_with_resilience
from retail_agent.sql_validator import validate_sql
from retail_agent.state import AgentState

logger = logging.getLogger(__name__)

NodeFunc = Callable[[AgentState], dict[str, object]]

# A curated set of safe fields per node -- counts, booleans, short enums, and (per
# AGENTS.md's Security section) the generated SQL *text* itself, which is explicitly
# allowed in logs/traces ("may record a SQL query's text, its timing, and its row/column
# count -- never the row content"). Never a raw query result or the full report text: the
# report is already recoverable from `saved_reports`/`feedback` for any turn that needs
# deeper inspection, so it isn't duplicated into every trace line. Nodes not listed here
# get no attributes beyond the generic timing every node already records.
_AttributeExtractor = Callable[[AgentState, dict[str, object]], dict[str, object]]
_NODE_ATTRIBUTE_EXTRACTORS: dict[str, _AttributeExtractor] = {
    "route_intent": lambda s, u: {"intent": u.get("intent")},
    "guardrail": lambda s, u: {"blocked": u.get("rejection_reason") == "safety"},
    "retrieve_golden_bucket": lambda s, u: {
        "trio_count": len(u.get("retrieved_trios") or [])  # type: ignore[arg-type]
    },
    "generate_sql": lambda s, u: {"sql_attempts": u.get("sql_attempts"), "sql": u.get("sql")},
    "validate": lambda s, u: {"error": u.get("last_error") is not None, "sql": s.get("sql")},
    "dry_run": lambda s, u: {"error": u.get("last_error") is not None, "sql": s.get("sql")},
    "execute": lambda s, u: {
        "error": u.get("last_error") is not None,
        "row_count": u.get("query_row_count"),
        "sql": s.get("sql"),
    },
    "synthesize": lambda s, u: {"needs_more_data": u.get("needs_more_data_reason") is not None},
    "fallback": lambda s, u: {"triggered": True},
    "parse_delete": lambda s, u: {"filter_kind": u.get("delete_filter_kind")},
    "resolve_candidates": lambda s, u: {
        "candidate_count": len(u.get("delete_candidates") or [])  # type: ignore[arg-type]
    },
    "confirm_delete": lambda s, u: {"confirmed": u.get("delete_confirmed")},
}


def _node_attributes(
    node_name: str, state: AgentState, update: dict[str, object]
) -> dict[str, object]:
    """Extract the curated, safe telemetry payload for one node's execution."""
    extractor = _NODE_ATTRIBUTE_EXTRACTORS.get(node_name)
    return extractor(state, update) if extractor else {}


# A saved report's title is the question that produced it, truncated -- short enough to
# scan in a candidate list, long enough to tell two reports apart.
REPORT_TITLE_MAX_LEN = 120

# The "-latest" alias, not a dated model name: gemini-2.5-pro (Day 1's pin) stopped being
# callable for this account between Day 1 and Day 4 ("no longer available to new users"),
# confirmed by actually hitting a live 404, not a changelog. The alias is Google's own
# moving pointer to its current recommended model per tier -- see ADR-002's Day 4 update.
GEMINI_FLASH = "gemini-flash-latest"
GEMINI_PRO = "gemini-pro-latest"
MAX_SQL_ATTEMPTS = 3  # 1 initial attempt + 2 retries, shared by both self-correction triggers.
SAMPLE_ROWS_FOR_SYNTHESIS = 30


class AgentDependencies:
    """The external clients every node needs, constructed once per process."""

    def __init__(
        self,
        *,
        llm: GeminiClient,
        bq: BigQueryRunner,
        golden_bucket: QdrantGoldenBucketStore,
        app_db: sqlite3.Connection,
    ) -> None:
        """Hold the clients and a per-process schema-context cache for every node."""
        self.llm = llm
        self.bq = bq
        self.golden_bucket = golden_bucket
        self.app_db = app_db
        self.bq_breaker = CircuitBreaker()
        self._schema_context_cache: str | None = None

    def schema_context(self) -> str:
        """Live schema context, built once per process and reused across turns."""
        if self._schema_context_cache is None:
            self._schema_context_cache = build_schema_context(self.bq)
        return self._schema_context_cache


def build_graph(deps: AgentDependencies) -> StateGraph:
    """Construct the (uncompiled) agent graph bound to `deps`."""

    def learn_preferences(state: AgentState) -> dict[str, object]:
        profiles.apply_turn(
            deps.app_db, app_user_id=state["app_user_id"], message=state["question"]
        )
        return {}

    def route_intent(state: AgentState) -> dict[str, object]:
        reply = deps.llm.generate(
            state["question"], model=GEMINI_FLASH, system_instruction=ROUTER_SYSTEM
        )
        choice = reply.strip().upper()
        if choice == "ANALYSIS":
            return {"intent": "analysis"}
        if choice == "DELETE":
            return {"intent": "delete"}
        return {"intent": "off_topic"}

    def guardrail(state: AgentState) -> dict[str, object]:
        reply = deps.llm.generate(
            state["question"], model=GEMINI_FLASH, system_instruction=GUARDRAIL_SYSTEM
        )
        if reply.strip().upper() == "SAFE":
            return {}
        logger.warning("Guardrail blocked a message for session %s", state["session_id"])
        return {"intent": "off_topic", "rejection_reason": "safety"}

    def reject(state: AgentState) -> dict[str, object]:
        if state.get("rejection_reason") == "safety":
            answer = "I can only help with retail data-analysis questions over this dataset."
        else:
            answer = "I'm a retail data-analysis assistant -- ask about orders, products, or sales."
        return {"final_answer": answer}

    def parse_delete(state: AgentState) -> dict[str, object]:
        reply = deps.llm.generate(
            state["question"], model=GEMINI_FLASH, system_instruction=DELETE_PARSE_SYSTEM
        ).strip()
        if reply.upper().startswith("MENTION:"):
            value = reply.split(":", 1)[1].strip()
            if value:
                return {"delete_filter_kind": "mention", "delete_filter_value": value}
        return {"delete_filter_kind": "session", "delete_filter_value": None}

    def resolve_candidates(state: AgentState) -> dict[str, object]:
        if state["delete_filter_kind"] == "mention":
            rows = reports.find_candidates_by_mention(
                deps.app_db,
                app_user_id=state["app_user_id"],
                mention=state["delete_filter_value"] or "",
            )
        else:
            rows = reports.find_candidates_by_session(
                deps.app_db, app_user_id=state["app_user_id"], session_id=state["session_id"]
            )
        candidates = [{"report_id": r["report_id"], "title": r["title"]} for r in rows]
        if not candidates:
            return {
                "delete_candidates": [],
                "final_answer": "I didn't find any matching saved reports to delete.",
            }
        reports.log_delete_requested(
            deps.app_db,
            report_ids=[c["report_id"] for c in candidates],
            app_user_id=state["app_user_id"],
            turn_id=state["turn_id"],
        )
        return {"delete_candidates": candidates}

    def confirm_delete(state: AgentState) -> dict[str, object]:
        candidates = state["delete_candidates"] or []
        confirmed = interrupt(
            {
                "action": "confirm_delete",
                "count": len(candidates),
                "candidates": candidates,
            }
        )
        return {"delete_confirmed": bool(confirmed)}

    def persist_delete(state: AgentState) -> dict[str, object]:
        candidates = state["delete_candidates"] or []
        report_ids = [str(c["report_id"]) for c in candidates]
        if state.get("delete_confirmed"):
            reports.confirm_delete(
                deps.app_db,
                report_ids=report_ids,
                app_user_id=state["app_user_id"],
                turn_id=state["turn_id"],
            )
            answer = f"Deleted {len(report_ids)} report(s)."
        else:
            reports.log_delete_cancelled(
                deps.app_db,
                report_ids=report_ids,
                app_user_id=state["app_user_id"],
                turn_id=state["turn_id"],
            )
            answer = "Okay, I didn't delete anything."
        return {"final_answer": answer}

    def retrieve_golden_bucket(state: AgentState) -> dict[str, object]:
        [embedding] = deps.llm.embed([state["question"]], model="gemini-embedding-001")
        trios = deps.golden_bucket.query(embedding, top_k=5)
        return {"retrieved_trios": [asdict(t) for t in trios]}

    def generate_sql(state: AgentState) -> dict[str, object]:
        prompt = build_sql_generation_prompt(
            question=state["question"],
            schema_context=deps.schema_context(),
            retrieved_trios=state["retrieved_trios"],
            app_user_id=state["app_user_id"],
            history=state["history"],
            last_error=state.get("last_error"),
        )
        raw = deps.llm.generate(prompt, model=GEMINI_FLASH)
        sql = raw.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
        sql = sql.replace("@requesting_user_id", f"'{state['app_user_id']}'")
        return {"sql": sql, "sql_attempts": state["sql_attempts"] + 1}

    def validate(state: AgentState) -> dict[str, object]:
        result = validate_sql(state["sql"] or "")
        if result.valid:
            return {"last_error": None}
        logger.info("SQL validation rejected a query: %s", result.reason)
        return {"last_error": f"validation failed: {result.reason}"}

    def dry_run(state: AgentState) -> dict[str, object]:
        sql = state["sql"]
        if sql is None:
            return {"last_error": "no SQL was generated to dry-run"}
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        try:
            call_with_resilience(
                lambda: deps.bq.client.query(sql, job_config=job_config),
                service_name="bigquery-dry-run",
                breaker=deps.bq_breaker,
            )
        except Exception as error:  # noqa: BLE001 - any dry-run failure feeds self-correction, not a crash
            return {"last_error": f"dry run failed: {error}"}
        return {"last_error": None}

    def execute(state: AgentState) -> dict[str, object]:
        sql = state["sql"]
        if sql is None:
            return {"last_error": "no SQL was generated to execute"}
        try:
            df = call_with_resilience(
                lambda: deps.bq.execute_query(sql),
                service_name="bigquery-execute",
                breaker=deps.bq_breaker,
            )
        except Exception as error:  # noqa: BLE001 - execution failure feeds self-correction, not a crash
            return {"last_error": f"execution failed: {error}"}
        records = df.to_dict(orient="records")
        if not records:
            return {
                "last_error": "query returned no rows",
                "query_result": [],
                "query_row_count": 0,
            }
        return {"query_result": records, "query_row_count": len(records), "last_error": None}

    def synthesize(state: AgentState) -> dict[str, object]:
        persona = persona_module.load_persona()
        profile = profiles.get_or_create_profile(deps.app_db, app_user_id=state["app_user_id"])
        prompt = build_synthesis_prompt(
            question=state["question"],
            sql=state["sql"] or "",
            row_count=state["query_row_count"] or 0,
            sample_rows=(state["query_result"] or [])[:SAMPLE_ROWS_FOR_SYNTHESIS],
            retrieved_trios=state["retrieved_trios"],
            persona_block=persona.as_prompt_block(),
            profile_block=profiles.as_prompt_block(profile),
        )
        reply = deps.llm.generate(prompt, model=GEMINI_PRO).strip()
        if reply.startswith("NEEDS_MORE_DATA:"):
            return {"needs_more_data_reason": reply.removeprefix("NEEDS_MORE_DATA:").strip()}
        reply = scrub_pii(reply)
        reports.create_report(
            deps.app_db,
            session_id=state["session_id"],
            app_user_id=state["app_user_id"],
            title=state["question"][:REPORT_TITLE_MAX_LEN],
            body=reply,
            turn_id=state["turn_id"],
        )
        new_turn = {"question": state["question"], "answer": reply}
        return {"final_answer": reply, "needs_more_data_reason": None, "history": [new_turn]}

    def fallback(state: AgentState) -> dict[str, object]:
        issue = state.get("last_error") or state.get("needs_more_data_reason") or "unknown"
        answer = (
            f"I couldn't fully answer that after a few attempts -- last issue: {issue}. "
            "Try rephrasing, or ask a narrower question."
        )
        return {"final_answer": answer}

    def route_after_intent(state: AgentState) -> str:
        if state["intent"] == "analysis":
            return "guardrail"
        if state["intent"] == "delete":
            return "parse_delete"
        return "reject"

    def route_after_guardrail(state: AgentState) -> str:
        return "retrieve_golden_bucket" if state["intent"] == "analysis" else "reject"

    def route_after_resolve(state: AgentState) -> str:
        return "confirm" if state["delete_candidates"] else END

    def route_after_sql_step(state: AgentState) -> str:
        if state.get("last_error") is None:
            return "next"
        return "generate_sql" if state["sql_attempts"] < MAX_SQL_ATTEMPTS else "fallback"

    def route_after_synthesis(state: AgentState) -> str:
        if state.get("needs_more_data_reason") is None:
            return END
        if state["sql_attempts"] >= MAX_SQL_ATTEMPTS:
            return "fallback"
        return "generate_sql"

    def _instrumented(node_name: str, fn: NodeFunc) -> Any:  # noqa: ANN401 - see docstring
        """Wrap a node so every execution lands in `agent_events`, timed and attributed.

        A single generic wrapper applied at graph-build time, rather than instrumenting
        each node body by hand -- the per-node payload itself still varies (via
        `_node_attributes`), but the timing/persistence plumbing doesn't, so it isn't
        repeated fifteen times.

        Returns `Any`, not `NodeFunc`: `StateGraph.add_node` is heavily `@overload`ed on
        its callable argument's exact shape, resolved via special-cased inference for an
        inline function/lambda literal at the call site -- passing a same-shaped
        `Callable` value through a helper like this one defeats that inference (a known
        mypy limitation with this kind of overload set, not a real type error), so every
        `add_node(...)` call below would otherwise need its own `# type: ignore`.
        """

        def wrapped(state: AgentState) -> dict[str, object]:
            started = time.perf_counter()
            update = fn(state)
            duration_ms = int((time.perf_counter() - started) * 1000)
            telemetry.record_event(
                deps.app_db,
                session_id=state["session_id"],
                turn_id=state["turn_id"],
                trace_id=state["turn_id"],
                node_name=node_name,
                duration_ms=duration_ms,
                attributes=_node_attributes(node_name, state, update),
            )
            return update

        return wrapped

    graph = StateGraph(AgentState)
    graph.add_node("learn_preferences", _instrumented("learn_preferences", learn_preferences))
    graph.add_node("route_intent", _instrumented("route_intent", route_intent))
    graph.add_node("guardrail", _instrumented("guardrail", guardrail))
    graph.add_node("reject", _instrumented("reject", reject))
    graph.add_node("parse_delete", _instrumented("parse_delete", parse_delete))
    graph.add_node("resolve_candidates", _instrumented("resolve_candidates", resolve_candidates))
    graph.add_node("confirm_delete", _instrumented("confirm_delete", confirm_delete))
    graph.add_node("persist_delete", _instrumented("persist_delete", persist_delete))
    graph.add_node(
        "retrieve_golden_bucket", _instrumented("retrieve_golden_bucket", retrieve_golden_bucket)
    )
    graph.add_node("generate_sql", _instrumented("generate_sql", generate_sql))
    graph.add_node("validate", _instrumented("validate", validate))
    graph.add_node("dry_run", _instrumented("dry_run", dry_run))
    graph.add_node("execute", _instrumented("execute", execute))
    graph.add_node("synthesize", _instrumented("synthesize", synthesize))
    graph.add_node("fallback", _instrumented("fallback", fallback))

    graph.add_edge(START, "learn_preferences")
    graph.add_edge("learn_preferences", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        route_after_intent,
        {"guardrail": "guardrail", "reject": "reject", "parse_delete": "parse_delete"},
    )
    graph.add_conditional_edges(
        "guardrail",
        route_after_guardrail,
        {"retrieve_golden_bucket": "retrieve_golden_bucket", "reject": "reject"},
    )
    graph.add_edge("reject", END)
    graph.add_edge("parse_delete", "resolve_candidates")
    graph.add_conditional_edges(
        "resolve_candidates", route_after_resolve, {"confirm": "confirm_delete", END: END}
    )
    graph.add_edge("confirm_delete", "persist_delete")
    graph.add_edge("persist_delete", END)
    graph.add_edge("retrieve_golden_bucket", "generate_sql")
    graph.add_edge("generate_sql", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_sql_step,
        {"next": "dry_run", "generate_sql": "generate_sql", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "dry_run",
        route_after_sql_step,
        {"next": "execute", "generate_sql": "generate_sql", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "execute",
        route_after_sql_step,
        {"next": "synthesize", "generate_sql": "generate_sql", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "synthesize",
        route_after_synthesis,
        {END: END, "generate_sql": "generate_sql", "fallback": "fallback"},
    )
    graph.add_edge("fallback", END)

    return graph
