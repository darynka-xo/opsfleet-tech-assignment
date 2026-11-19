"""CLI chat loop for the retail data-analysis agent (requirement: CLI-based interface).

Run with:
    .venv/Scripts/python.exe -m retail_agent --user mgr-denim
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import RunnableConfig
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markdown import Markdown

from retail_agent.data.bq_client import BigQueryRunner
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.graph import AgentDependencies, build_graph
from retail_agent.llm.client import GeminiClient

RETAIL_ANALYTICS_DATASET = "opsfleet-tech-assignment.retail_analytics"
QDRANT_PATH = Path(__file__).resolve().parent.parent.parent / ".local" / "qdrant"
CHECKPOINT_PATH = Path(__file__).resolve().parent.parent.parent / ".local" / "checkpoints.sqlite3"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _build_line_reader(console: Console) -> Callable[[], str]:
    """Return a `() -> str` line reader, preferring `prompt_toolkit`'s rich editing.

    `PromptSession` needs a real console and raises when stdout isn't one -- piped
    output, a redirected terminal, or some CI/test harnesses (confirmed by actually
    hitting this under one such environment, not a theoretical concern). Falling back
    to plain `input()` keeps the CLI usable there instead of crashing outright
    (requirement 5: don't crash the user interface).
    """
    try:
        session: PromptSession[str] = PromptSession()
    except Exception as error:  # noqa: BLE001 - any construction failure means "no real console"
        logger.info("prompt_toolkit unavailable (%s), falling back to input()", error)
        console.print("[dim](falling back to plain input -- no interactive console detected)[/]")
        return lambda: input("> ")
    return lambda: session.prompt("> ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Retail data-analysis chat agent")
    parser.add_argument(
        "--user",
        default="mgr-denim",
        help="Demo app_user_id (mgr-denim, mgr-outdoor, mgr-basics, or ceo for no restriction).",
    )
    parser.add_argument(
        "--session", default=None, help="Resume a prior session id instead of starting fresh."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the interactive chat loop until the user exits."""
    load_dotenv()
    args = parse_args(argv)
    console = Console()

    deps = AgentDependencies(
        llm=GeminiClient(api_key=os.environ["GEMINI_API_KEY"]),
        bq=BigQueryRunner(
            project_id="opsfleet-tech-assignment", dataset_id=RETAIL_ANALYTICS_DATASET
        ),
        golden_bucket=QdrantGoldenBucketStore(QDRANT_PATH),
    )
    graph = build_graph(deps)

    session_id = args.session or str(uuid.uuid4())
    console.print(
        f"[bold cyan]Retail Data Analysis Agent[/] -- user [bold]{args.user}[/], "
        f"session [dim]{session_id}[/]"
    )
    console.print(
        "Ask a question about orders, products, or customers. Ctrl+D or 'exit' to quit.\n"
    )

    read_line = _build_line_reader(console)
    with SqliteSaver.from_conn_string(str(CHECKPOINT_PATH)) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}

        while True:
            try:
                question = read_line().strip()
            except EOFError, KeyboardInterrupt:
                console.print("\n[dim]Goodbye.[/]")
                return
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                console.print("[dim]Goodbye.[/]")
                return

            turn_input = {
                "session_id": session_id,
                "app_user_id": args.user,
                "question": question,
                "history": [],
                "intent": None,
                "rejection_reason": None,
                "retrieved_trios": [],
                "sql": None,
                "sql_attempts": 0,
                "last_error": None,
                "needs_more_data_reason": None,
                "query_result": None,
                "query_row_count": None,
                "final_answer": None,
            }
            with console.status("[dim]thinking...[/]"):
                try:
                    result = app.invoke(turn_input, config=config)
                except Exception:
                    logger.exception("Unhandled error processing a turn for session %s", session_id)
                    console.print("[red]Something went wrong on my end -- please try again.[/]\n")
                    continue

            console.print(Markdown(result.get("final_answer") or "(no answer produced)"))
            console.print()


if __name__ == "__main__":
    main()
