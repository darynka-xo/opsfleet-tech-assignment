"""The core LangGraph agent graph (Day 4): NL question -> SQL -> execution -> report.

Destructive-ops handling (DeleteParse/ResolveCand/Confirm/Persist in
docs/architecture.md's diagram) is Day 5 scope -- a "delete" intent here gets a graceful
"not available yet" message, not a crash, which is itself requirement 5 in miniature.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from google.cloud import bigquery
from langgraph.graph import END, START, StateGraph

from retail_agent.data.bq_client import BigQueryRunner
from retail_agent.data.schema_context import build_schema_context
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.llm.client import GeminiClient
from retail_agent.prompts import (
    GUARDRAIL_SYSTEM,
    ROUTER_SYSTEM,
    build_sql_generation_prompt,
    build_synthesis_prompt,
)
from retail_agent.resilience import CircuitBreaker, call_with_resilience
from retail_agent.sql_validator import validate_sql
from retail_agent.state import AgentState

logger = logging.getLogger(__name__)

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
    ) -> None:
        """Hold the clients and a per-process schema-context cache for every node."""
        self.llm = llm
        self.bq = bq
        self.golden_bucket = golden_bucket
        self.bq_breaker = CircuitBreaker()
        self._schema_context_cache: str | None = None

    def schema_context(self) -> str:
        """Live schema context, built once per process and reused across turns."""
        if self._schema_context_cache is None:
            self._schema_context_cache = build_schema_context(self.bq)
        return self._schema_context_cache


def build_graph(deps: AgentDependencies) -> StateGraph:
    """Construct the (uncompiled) agent graph bound to `deps`."""

    def route_intent(state: AgentState) -> dict[str, object]:
        reply = deps.llm.generate(
            state["question"], model=GEMINI_FLASH, system_instruction=ROUTER_SYSTEM
        )
        choice = reply.strip().upper()
        if choice == "ANALYSIS":
            return {"intent": "analysis"}
        if choice == "DELETE":
            return {"intent": "delete_not_yet_available"}
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
        if state["intent"] == "delete_not_yet_available":
            answer = (
                "Deleting saved reports isn't available in this build yet -- it's "
                "planned, with a confirmation step before anything is removed."
            )
        elif state.get("rejection_reason") == "safety":
            answer = "I can only help with retail data-analysis questions over this dataset."
        else:
            answer = "I'm a retail data-analysis assistant -- ask about orders, products, or sales."
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
        prompt = build_synthesis_prompt(
            question=state["question"],
            sql=state["sql"] or "",
            row_count=state["query_row_count"] or 0,
            sample_rows=(state["query_result"] or [])[:SAMPLE_ROWS_FOR_SYNTHESIS],
            retrieved_trios=state["retrieved_trios"],
        )
        reply = deps.llm.generate(prompt, model=GEMINI_PRO).strip()
        if reply.startswith("NEEDS_MORE_DATA:"):
            return {"needs_more_data_reason": reply.removeprefix("NEEDS_MORE_DATA:").strip()}
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
        return "guardrail" if state["intent"] == "analysis" else "reject"

    def route_after_guardrail(state: AgentState) -> str:
        return "retrieve_golden_bucket" if state["intent"] == "analysis" else "reject"

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

    graph = StateGraph(AgentState)
    graph.add_node("route_intent", route_intent)
    graph.add_node("guardrail", guardrail)
    graph.add_node("reject", reject)
    graph.add_node("retrieve_golden_bucket", retrieve_golden_bucket)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate", validate)
    graph.add_node("dry_run", dry_run)
    graph.add_node("execute", execute)
    graph.add_node("synthesize", synthesize)
    graph.add_node("fallback", fallback)

    graph.add_edge(START, "route_intent")
    graph.add_conditional_edges(
        "route_intent", route_after_intent, {"guardrail": "guardrail", "reject": "reject"}
    )
    graph.add_conditional_edges(
        "guardrail",
        route_after_guardrail,
        {"retrieve_golden_bucket": "retrieve_golden_bucket", "reject": "reject"},
    )
    graph.add_edge("reject", END)
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
