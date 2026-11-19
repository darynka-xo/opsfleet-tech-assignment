"""Tests for the eval harness's scoring logic (requirement 6), not the live agent run.

`scripts/run_eval.py` is a script (invoked by hand against real Gemini/BigQuery, like
`scripts/embed_golden_bucket.py`/`scripts/seed_acl.py`), not wired into
`pytest -m "not integration"` -- but `_check_result_correctness` is pure, deterministic
logic worth protecting on its own, the same reasoning that gave
`scripts/check_markdown_links.py` its own test file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_eval import _check_result_correctness, _markdown_safe


def test_expected_value_matches_within_tolerance() -> None:
    case = {"expected_value": 1000.0, "tolerance_pct": 5}
    assert _check_result_correctness(case, "The total revenue was $1,020.00 this year.") is True


def test_expected_value_outside_tolerance_fails() -> None:
    case = {"expected_value": 1000.0, "tolerance_pct": 5}
    assert _check_result_correctness(case, "The total revenue was $1,500.00 this year.") is False


def test_expected_value_matches_any_number_in_the_text() -> None:
    case = {"expected_value": 42.0, "tolerance_pct": 0}
    assert _check_result_correctness(case, "Revenue was $1,000. We shipped 42 orders.") is True


def test_expect_answer_contains_requires_every_substring() -> None:
    case = {"expect_answer_contains": ["Calvin Klein", "Levi's"]}
    assert _check_result_correctness(case, "Calvin Klein outsold Levi's this quarter.") is True
    assert _check_result_correctness(case, "Calvin Klein had a strong quarter.") is False


def test_expect_answer_contains_is_case_insensitive() -> None:
    case = {"expect_answer_contains": ["calvin klein"]}
    assert _check_result_correctness(case, "CALVIN KLEIN led the category.") is True


def test_a_case_with_neither_check_returns_none() -> None:
    assert _check_result_correctness({"capability_area": "schema_meta"}, "Some answer.") is None


def test_markdown_safe_inserts_blank_line_before_a_list_after_a_paragraph() -> None:
    text = "Action items:\n- Do the first thing.\n- Do the second thing."
    assert _markdown_safe(text) == "Action items:\n\n- Do the first thing.\n- Do the second thing."


def test_markdown_safe_leaves_an_already_blank_separated_list_untouched() -> None:
    text = "Intro.\n\n- One.\n- Two."
    assert _markdown_safe(text) == text


def test_markdown_safe_leaves_plain_prose_untouched() -> None:
    text = "Just a plain paragraph with no list at all."
    assert _markdown_safe(text) == text
