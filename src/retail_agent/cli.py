"""CLI chat loop for the retail data-analysis agent (requirement: CLI-based interface).

Run with:
    .venv/Scripts/python.exe -m retail_agent --user mgr-denim
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, RunnableConfig
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markdown import Markdown

from retail_agent import feedback
from retail_agent.data.bq_client import BigQueryRunner
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.graph import AgentDependencies, build_graph
from retail_agent.llm.client import GeminiClient

RETAIL_ANALYTICS_DATASET = "opsfleet-tech-assignment.retail_analytics"
QDRANT_PATH = Path(__file__).resolve().parent.parent.parent / ".local" / "qdrant"
CHECKPOINT_PATH = Path(__file__).resolve().parent.parent.parent / ".local" / "checkpoints.sqlite3"
APP_DB_PATH = Path(__file__).resolve().parent.parent.parent / ".local" / "app_state.sqlite3"
APP_DB_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "sql" / "app_state.sql"

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


def _open_app_db(path: Path, *, schema_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the local app-state SQLite database.

    Args:
        path: Where the database file lives, e.g. `.local/app_state.sqlite3`.
        schema_path: The DDL script to apply -- every statement is `CREATE ... IF NOT
            EXISTS`, so running it against an already-initialized database is a no-op.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    return conn


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

    app_db = _open_app_db(APP_DB_PATH, schema_path=APP_DB_SCHEMA_PATH)
    deps = AgentDependencies(
        llm=GeminiClient(api_key=os.environ["GEMINI_API_KEY"]),
        bq=BigQueryRunner(
            project_id="opsfleet-tech-assignment", dataset_id=RETAIL_ANALYTICS_DATASET
        ),
        golden_bucket=QdrantGoldenBucketStore(QDRANT_PATH),
        app_db=app_db,
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
                "turn_id": str(uuid.uuid4()),
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
                "delete_filter_kind": None,
                "delete_filter_value": None,
                "delete_candidates": None,
                "delete_confirmed": None,
                "final_answer": None,
            }
            try:
                with console.status("[dim]thinking...[/]"):
                    result = app.invoke(turn_input, config=config)

                # A destructive op (delete) pauses here via interrupt() -- resuming
                # requires a raw, code-checked yes/no from the user, never an LLM's
                # judgment that the conversation "sounds like" agreement (AGENTS.md
                # Security: "Confirmation integrity").
                while "__interrupt__" in result:
                    payload = result["__interrupt__"][0].value
                    console.print(f"[yellow]About to delete {payload['count']} report(s):[/]")
                    for candidate in payload["candidates"]:
                        console.print(f"  - {candidate['title']}")
                    console.print(f"Confirm delete of {payload['count']} report(s)? [y/N]")
                    raw_answer = read_line().strip().lower()
                    confirmed = raw_answer in {"y", "yes"}
                    with console.status("[dim]thinking...[/]"):
                        result = app.invoke(Command(resume=confirmed), config=config)
            except Exception:
                logger.exception("Unhandled error processing a turn for session %s", session_id)
                console.print("[red]Something went wrong on my end -- please try again.[/]\n")
                continue

            console.print(Markdown(result.get("final_answer") or "(no answer produced)"))

            # Only analysis turns produce something worth rating -- an off-topic rejection
            # or a delete confirmation already got its own yes/no exchange above.
            if result.get("intent") == "analysis":
                console.print("[dim]Rate this answer: + (up) / - (down) / Enter to skip[/]")
                rating_input = read_line().strip()
                if rating_input in {"+", "-"}:
                    feedback.record_feedback(
                        app_db,
                        app_user_id=args.user,
                        session_id=session_id,
                        turn_id=turn_input["turn_id"],
                        rating="up" if rating_input == "+" else "down",
                        question=question,
                        sql_query=result.get("sql"),
                        report_body=result.get("final_answer"),
                    )
            console.print()


if __name__ == "__main__":
    main()
