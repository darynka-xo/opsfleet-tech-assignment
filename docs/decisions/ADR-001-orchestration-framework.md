# ADR-001: Orchestration framework — LangGraph

**Status:** Accepted (validated with a working spike, 2026-11-18)

## Context

The agent needs explicit, stateful control flow: a router/guardrail stage, a retrieval →
generate → validate → execute → self-correct loop for SQL, multi-turn conversation memory,
and — critically — a hard pause-and-wait-for-explicit-confirmation step before any
destructive action (report deletion). The candidate frameworks considered were LangGraph,
Google ADK, and a hand-rolled orchestration loop.

## Decision

Use **LangGraph**.

- Its `interrupt()` / `Command` resume primitive, backed by a checkpointer, is close to a
  direct implementation of the destructive-ops confirmation requirement: pause graph
  execution, show the user the pending action, resume only on explicit approval.
- The same checkpointer gives multi-turn conversation state for free.
- It's framework-agnostic about the LLM client — nodes are plain Python functions over a
  typed state object, so there's no requirement to route every model call through a
  LangChain `ChatModel` wrapper. The Day 1 spike calls the `google-genai` SDK directly from
  inside a LangGraph node, which is the pattern the real agent will follow (see ADR-002).
- More production-hardened and better documented today than Google ADK, which is newer,
  thinner on community/docs, and leans toward a Vertex AI Agent Engine deployment target —
  a reasonable choice if this were a Google Cloud-only commitment, but LangGraph keeps the
  option open without meaningfully costing anything given Gemini is already the required LLM.

## Consequences

- Adds `langgraph` (+ `langgraph-checkpoint`, `langgraph-prebuilt`) as a dependency — already
  installed and validated against Python 3.14 (see `requirements.txt`).
- `langchain-core` / `langchain-google-genai` are also installed in case a prebuilt LangGraph
  agent pattern (e.g. `create_react_agent`) turns out useful later; they are not required by
  the current design and can be dropped if unused by Day 7.

## Validation

`scripts/day1_spike.py` — a 3-node graph (generate SQL → execute against BigQuery → print)
ran successfully end to end against the live `thelook_ecommerce.orders` table. Throwaway
code; the real graph is designed in `docs/architecture.md` (Day 2).
