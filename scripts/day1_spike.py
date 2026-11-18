"""Day 1 risk-reduction spike: Gemini -> BigQuery round trip through a minimal LangGraph.

Throwaway script, not part of the agent architecture (finalized Day 2). Only exists to
prove the framework choice holds up before committing to it in the HLD. Run with:
    .venv/Scripts/python.exe scripts/day1_spike.py
"""

from __future__ import annotations

import os
from typing import TypedDict

from dotenv import load_dotenv
from google import genai
from langgraph.graph import END, StateGraph

from retail_agent.data.bq_client import BigQueryRunner

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash"


class SpikeState(TypedDict):
    """State threaded through the three spike nodes."""

    question: str
    sql: str
    result: str


gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
bq = BigQueryRunner()


def generate_sql(state: SpikeState) -> SpikeState:
    """Ask Gemini to translate the question into BigQuery Standard SQL."""
    prompt = (
        "You write BigQuery Standard SQL against the table "
        "`bigquery-public-data.thelook_ecommerce.orders`. "
        "Reply with ONLY the SQL query, no markdown fences, no explanation.\n\n"
        f"Question: {state['question']}"
    )
    resp = gemini.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    sql = resp.text.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
    return {**state, "sql": sql}


def execute_sql(state: SpikeState) -> SpikeState:
    """Run the generated SQL against BigQuery and stringify the result."""
    df = bq.execute_query(state["sql"])
    return {**state, "result": df.to_string(index=False)}


def print_result(state: SpikeState) -> SpikeState:
    """Print the question, generated SQL, and result for manual inspection."""
    print(f"Question: {state['question']}")
    print(f"Generated SQL: {state['sql']}")
    print(f"Result:\n{state['result']}")
    return state


graph = StateGraph(SpikeState)
graph.add_node("generate_sql", generate_sql)
graph.add_node("execute_sql", execute_sql)
graph.add_node("print_result", print_result)
graph.set_entry_point("generate_sql")
graph.add_edge("generate_sql", "execute_sql")
graph.add_edge("execute_sql", "print_result")
graph.add_edge("print_result", END)

app = graph.compile()

if __name__ == "__main__":
    app.invoke({"question": "How many total orders are in the dataset?", "sql": "", "result": ""})
