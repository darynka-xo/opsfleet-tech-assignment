"""Score the agent against the held-out regression set (requirement 6).

Runs the real graph against real Gemini + BigQuery + Qdrant for every case in
`data/eval_regression_set.yaml` -- this is an eval, not a unit test, so it's a script you
run by hand, documented here rather than wired into `pytest -m "not integration"`:
    .venv/Scripts/python.exe scripts/run_eval.py

Scores three things per case, matching docs/architecture.md section 6:
- SQL execution accuracy: did the turn reach a synthesized report at all (vs. falling
  back after exhausting self-correction retries)?
- Result correctness: for a case with `expected_value`, does any number in the final
  answer fall within `tolerance_pct` of it? For `expect_answer_contains`, are all the
  listed substrings present (case-insensitive)?
- Report quality: an LLM-as-judge rubric (faithfulness, relevance, clarity) scored
  1-5 by Gemini Pro, given the question, the SQL that ran, and the report text -- the
  judge never sees `expected_value`, so this score is independent of the correctness check.

Writes a dated markdown report to docs/ and prints a summary table.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import yaml
from dotenv import load_dotenv

from retail_agent.data.bq_client import BigQueryRunner
from retail_agent.golden_bucket.qdrant_store import QdrantGoldenBucketStore
from retail_agent.graph import AgentDependencies, build_graph
from retail_agent.llm.client import GeminiClient

RETAIL_ANALYTICS_DATASET = "opsfleet-tech-assignment.retail_analytics"
REGRESSION_SET_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_regression_set.yaml"
QDRANT_PATH = Path(__file__).resolve().parent.parent / ".local" / "qdrant"
APP_DB_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"
# A separate database from the CLI's `.local/app_state.sqlite3` -- eval runs aren't real
# conversations, and shouldn't dilute the metrics rollup (scripts/metrics_rollup.py) or
# the Golden Bucket curation pool (scripts/curate_golden_bucket_candidates.py) with them.
EVAL_APP_DB_PATH = Path(__file__).resolve().parent.parent / ".local" / "eval_app_state.sqlite3"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "docs"

_JUDGE_SYSTEM = """You are grading a retail data-analysis report for an internal eval.
Score it from 1 (poor) to 5 (excellent) on three criteria:
- faithfulness: does the report's narrative match what the SQL result would actually show,
  with no invented numbers or claims the data doesn't support?
- relevance: does it actually answer the question asked, not a different one?
- clarity: is it a clear, direct, well-organized answer a busy executive could act on?
Reply with ONLY a JSON object, no markdown fences, no other text:
{"faithfulness": <1-5>, "relevance": <1-5>, "clarity": <1-5>, "reasoning": "<one sentence>"}"""

_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class CaseResult:
    """The scored outcome of running one regression case."""

    case_id: str
    question: str
    sql_success: bool
    result_correct: bool | None  # None when the case has nothing to check against
    judge_scores: dict[str, object] | None
    final_answer: str
    error: str | None = None


def _load_cases(path: Path) -> list[dict[str, object]]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _check_result_correctness(case: dict[str, object], final_answer: str) -> bool | None:
    """Return True/False if the case specifies something checkable, else None."""
    if "expected_value" in case:
        # YAML-sourced values are `object` to mypy; routing through `str()` first keeps
        # the conversion exact for the ints/floats this field actually holds, without
        # reaching for `cast`/`Any`.
        expected = float(str(case["expected_value"]))
        tolerance_pct = float(str(case.get("tolerance_pct", 0)))
        tolerance = abs(expected) * (tolerance_pct / 100)
        for match in _NUMBER_PATTERN.findall(final_answer):
            try:
                candidate = float(match.replace(",", ""))
            except ValueError:
                continue
            if abs(candidate - expected) <= tolerance:
                return True
        return False
    if "expect_answer_contains" in case:
        lowered = final_answer.lower()
        substrings = cast(list[str], case["expect_answer_contains"])
        return all(substring.lower() in lowered for substring in substrings)
    return None


def _judge_report(llm: GeminiClient, *, question: str, sql: str, report: str) -> dict[str, object]:
    prompt = f"Question: {question}\nSQL executed: {sql}\nReport:\n{report}"
    reply = llm.generate(
        prompt, model="gemini-pro-latest", system_instruction=_JUDGE_SYSTEM
    ).strip()
    reply = reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(reply)  # type: ignore[no-any-return]


def _new_turn_input(*, app_user_id: str, session_id: str, question: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "app_user_id": app_user_id,
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


def run_case(graph: object, llm: GeminiClient, case: dict[str, object]) -> CaseResult:
    """Run one regression case through the real graph and score it."""
    from langgraph.checkpoint.memory import MemorySaver  # local: eval-only dependency

    app = graph.compile(checkpointer=MemorySaver())  # type: ignore[attr-defined]
    session_id = f"eval-{case['case_id']}"
    config = {"configurable": {"thread_id": session_id}}
    turn_input = _new_turn_input(
        app_user_id=str(case["app_user_id"]), session_id=session_id, question=str(case["question"])
    )
    try:
        result = app.invoke(turn_input, config=config)
    except Exception as error:  # noqa: BLE001 - a crashed case is a scored failure, not a halt
        return CaseResult(
            case_id=str(case["case_id"]),
            question=str(case["question"]),
            sql_success=False,
            result_correct=None,
            judge_scores=None,
            final_answer="",
            error=str(error),
        )

    final_answer = result.get("final_answer") or ""
    sql_success = bool(result.get("needs_more_data_reason") is None and result.get("sql"))
    correctness = _check_result_correctness(case, final_answer)
    judge_scores = None
    if sql_success and final_answer:
        judge_scores = _judge_report(
            llm, question=str(case["question"]), sql=result.get("sql") or "", report=final_answer
        )
    return CaseResult(
        case_id=str(case["case_id"]),
        question=str(case["question"]),
        sql_success=sql_success,
        result_correct=correctness,
        judge_scores=judge_scores,
        final_answer=final_answer,
    )


_LIST_ITEM_PATTERN = re.compile(r"^\s*([-*]|\d+\.)\s")


def _markdown_safe(text: str) -> str:
    """Insert a blank line before a list that starts right after a paragraph.

    The LLM-authored report text is free-form -- it sometimes writes "Action items:"
    directly followed by a `-`/`*` list with no blank line, which markdownlint's
    MD032 (blanks-around-lists) correctly flags once this text lands in a committed
    `.md` report. Fixing it here, once, is simpler than asking every synthesis call to
    get Markdown spacing right.
    """
    lines = text.splitlines()
    fixed: list[str] = []
    for i, line in enumerate(lines):
        is_list_item = bool(_LIST_ITEM_PATTERN.match(line))
        previous_is_blank_or_list = (
            i == 0 or not lines[i - 1].strip() or _LIST_ITEM_PATTERN.match(lines[i - 1])
        )
        if is_list_item and not previous_is_blank_or_list:
            fixed.append("")
        fixed.append(line)
    return "\n".join(fixed)


def _write_report(results: list[CaseResult], path: Path) -> None:
    lines = [f"# Eval report -- {datetime.now(UTC).date().isoformat()}", ""]
    sql_success_rate = sum(1 for r in results if r.sql_success) / len(results)
    checkable = [r for r in results if r.result_correct is not None]
    correctness_rate = (
        (sum(1 for r in checkable if r.result_correct) / len(checkable)) if checkable else None
    )
    judge_scores = [r.judge_scores for r in results if r.judge_scores is not None]
    lines.append(f"- SQL execution accuracy: {sql_success_rate:.0%} ({len(results)} cases)")
    if correctness_rate is not None:
        lines.append(
            f"- Result correctness: {correctness_rate:.0%} ({len(checkable)} checkable cases)"
        )
    if judge_scores:
        for criterion in ("faithfulness", "relevance", "clarity"):
            avg = sum(float(str(js[criterion])) for js in judge_scores) / len(judge_scores)
            lines.append(f"- Average judge {criterion}: {avg:.1f}/5")
    lines.append("")
    lines.append("| case | SQL ok | correct | faithfulness | relevance | clarity |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in results:
        js = r.judge_scores or {}
        correct_cell = "-" if r.result_correct is None else ("yes" if r.result_correct else "NO")
        sql_cell = "yes" if r.sql_success else "no"
        faithfulness, relevance, clarity = (
            js.get("faithfulness", "-"),
            js.get("relevance", "-"),
            js.get("clarity", "-"),
        )
        row_cells = [
            r.case_id,
            sql_cell,
            correct_cell,
            str(faithfulness),
            str(relevance),
            str(clarity),
        ]
        lines.append("| " + " | ".join(row_cells) + " |")
    lines.append("")
    for r in results:
        lines.append(f"## {r.case_id}: {r.question}")
        if r.error:
            lines.append(f"**Error:** {r.error}")
        else:
            lines.append(_markdown_safe(r.final_answer))
            if r.judge_scores:
                lines.append(f"\n*Judge reasoning:* {r.judge_scores.get('reasoning', '')}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Run every regression case and write a scored eval report."""
    load_dotenv()
    cases = _load_cases(REGRESSION_SET_PATH)
    print(f"Loaded {len(cases)} regression case(s) from {REGRESSION_SET_PATH}")

    EVAL_APP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    app_db = sqlite3.connect(EVAL_APP_DB_PATH)
    app_db.executescript(APP_DB_SCHEMA_PATH.read_text(encoding="utf-8"))

    llm = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    deps = AgentDependencies(
        llm=llm,
        bq=BigQueryRunner(
            project_id="opsfleet-tech-assignment", dataset_id=RETAIL_ANALYTICS_DATASET
        ),
        golden_bucket=QdrantGoldenBucketStore(QDRANT_PATH),
        app_db=app_db,
    )
    graph = build_graph(deps)

    results = [run_case(graph, llm, case) for case in cases]

    for r in results:
        status = "OK" if r.sql_success else "FAIL"
        correct = "" if r.result_correct is None else (" correct" if r.result_correct else " WRONG")
        print(f"[{status}{correct}] {r.case_id}: {r.question}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{datetime.now(UTC).date().isoformat()}_eval_report.md"
    _write_report(results, report_path)
    print(f"\nWrote eval report to {report_path}")


if __name__ == "__main__":
    main()
