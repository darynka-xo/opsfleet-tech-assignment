"""Tests for user-level learning (requirement 4a). Offline, deterministic, no network."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from retail_agent import profiles

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "app_state.sql"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return connection


def test_a_standing_statement_sets_the_preference_immediately(conn: sqlite3.Connection) -> None:
    profiles.apply_turn(conn, app_user_id="u1", message="I prefer bullet points")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profile["preferred_format"] == "bullet_points"


def test_a_bare_mention_does_not_set_the_preference_by_itself(conn: sqlite3.Connection) -> None:
    profiles.apply_turn(conn, app_user_id="u1", message="can you keep this one brief")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profile["preferred_depth"] is None


def test_three_consistent_bare_mentions_promote_the_inference(conn: sqlite3.Connection) -> None:
    for _ in range(3):
        profiles.apply_turn(conn, app_user_id="u1", message="keep it brief please")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profile["preferred_depth"] == "summary"


def test_two_consistent_bare_mentions_do_not_promote_yet(conn: sqlite3.Connection) -> None:
    for _ in range(2):
        profiles.apply_turn(conn, app_user_id="u1", message="be concise")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profile["preferred_depth"] is None


def test_an_explicit_override_is_not_eroded_by_a_later_one_off_mention(
    conn: sqlite3.Connection,
) -> None:
    profiles.apply_turn(conn, app_user_id="u1", message="I prefer bullet points")
    profiles.apply_turn(conn, app_user_id="u1", message="give me a narrative this time")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profile["preferred_format"] == "bullet_points"


def test_profiles_are_isolated_per_user(conn: sqlite3.Connection) -> None:
    profiles.apply_turn(conn, app_user_id="u1", message="I prefer bullet points")

    other = profiles.get_or_create_profile(conn, app_user_id="u2")
    assert other["preferred_format"] is None


def test_as_prompt_block_is_empty_for_a_fresh_profile(conn: sqlite3.Connection) -> None:
    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert profiles.as_prompt_block(profile) == ""


def test_as_prompt_block_reflects_set_preferences(conn: sqlite3.Connection) -> None:
    profiles.apply_turn(conn, app_user_id="u1", message="I prefer bullet points")

    profile = profiles.get_or_create_profile(conn, app_user_id="u1")
    assert "bullet_points" in profiles.as_prompt_block(profile)
