"""User-level learning: explicit and inferred preferences (requirement 4a).

Explicit preferences (the user says "give me bullet points, *always*") always win over
inferred ones -- AGENTS.md/sql/app_state.sql's comment on `user_profiles` says so,
enforced here by only ever counting a turn toward inference when no explicit override is
already set for that field.

Detection is a small keyword heuristic, not a full NLU pass -- a deliberate scope cut for
this prototype (consistent with the Day 4 cost-budget simplification): fast,
deterministic, and testable without another Gemini call on every turn. A real system
would likely use the LLM itself to classify preference statements.

A mention of a format/depth word alone ("can you summarize this one") is NOT treated as
explicit -- only a *standing* statement is (one of `_STANDING_MARKERS` co-occurring with
the word: "always", "from now on", "I prefer", "by default", "going forward", "in
general"). A bare mention still counts toward the inference counter. Getting this
distinction right matters: collapsing the two (an earlier version of this module did)
made every one-off request permanently override the user's actual standing preference.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Literal, TypedDict

Format = Literal["table", "bullet_points", "narrative"]
Depth = Literal["summary", "detailed"]

_STANDING_MARKER = re.compile(
    r"\balways\b|\bfrom now on\b|\bi prefer\b|\bby default\b|\bgoing forward\b|\bin general\b",
    re.IGNORECASE,
)

# Each pattern maps a phrase a user might type to the preference it mentions -- whether
# that mention is explicit or merely inferred is decided separately, by _STANDING_MARKER.
_FORMAT_PATTERNS: tuple[tuple[re.Pattern[str], Format], ...] = (
    (re.compile(r"\bbullet\s*points?\b", re.IGNORECASE), "bullet_points"),
    (re.compile(r"\btables?\b", re.IGNORECASE), "table"),
    (re.compile(r"\bnarrative\b|\bprose\b|\bparagraphs?\b", re.IGNORECASE), "narrative"),
)
_DEPTH_PATTERNS: tuple[tuple[re.Pattern[str], Depth], ...] = (
    (
        re.compile(r"\bbrief(ly)?\b|\bshort(er)?\b|\bconcise(ly)?\b|\bsummary\b", re.IGNORECASE),
        "summary",
    ),
    (
        re.compile(
            r"\bin\s*depth\b|\bdetailed?\b|\bmore\s*detail\b|\bthorough(ly)?\b", re.IGNORECASE
        ),
        "detailed",
    ),
)
_CHARTS_PATTERN = re.compile(r"\bcharts?\b|\bgraphs?\b|\bvisuali[sz]", re.IGNORECASE)
_NO_CHARTS_PATTERN = re.compile(r"\bno\s*charts?\b|\btext\s*only\b|\bjust\s*text\b", re.IGNORECASE)

# After this many consistent mentions with no explicit statement, infer the preference.
_INFERENCE_THRESHOLD = 3


class Mention(TypedDict, total=False):
    """What one user message mentioned, and whether it was a standing statement."""

    preferred_format: Format
    preferred_depth: Depth
    prefers_charts: bool
    explicit: bool


def detect_mention(message: str) -> Mention:
    """Scan one user message for a format/depth/charts mention, explicit or not.

    Args:
        message: The user's current-turn message.

    Returns:
        A dict with whichever of `preferred_format`/`preferred_depth`/`prefers_charts`
        the message mentioned; `explicit` is `True` only if a standing-preference marker
        (`_STANDING_MARKER`) co-occurred. Empty if the message mentioned none of these.
    """
    found: Mention = {}
    is_standing = bool(_STANDING_MARKER.search(message))
    for format_pattern, format_value in _FORMAT_PATTERNS:
        if format_pattern.search(message):
            found["preferred_format"] = format_value
            break
    for depth_pattern, depth_value in _DEPTH_PATTERNS:
        if depth_pattern.search(message):
            found["preferred_depth"] = depth_value
            break
    if _NO_CHARTS_PATTERN.search(message):
        found["prefers_charts"] = False
    elif _CHARTS_PATTERN.search(message):
        found["prefers_charts"] = True
    if found:
        found["explicit"] = is_standing
    return found


def get_or_create_profile(conn: sqlite3.Connection, *, app_user_id: str) -> sqlite3.Row:
    """Return this user's profile row, creating a blank one if none exists yet."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE app_user_id = ?", (app_user_id,)
    ).fetchone()
    if row is not None:
        return row
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO user_profiles "
        "(app_user_id, preferred_format, preferred_depth, prefers_charts, "
        "explicit_overrides, inferred_signals, updated_at) "
        "VALUES (?, NULL, NULL, NULL, '{}', '{}', ?)",
        (app_user_id, now),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM user_profiles WHERE app_user_id = ?", (app_user_id,)
    ).fetchone()


def _apply_field(
    field: str,
    suggested: object,
    *,
    is_standing: bool,
    explicit_overrides: dict[str, object],
    inferred_signals: dict[str, dict[str, int]],
    updates: dict[str, object],
    now: str,
) -> None:
    """Update `explicit_overrides`/`inferred_signals`/`updates` in place for one field."""
    if suggested is None:
        return
    if is_standing:
        explicit_overrides[field] = {"value": suggested, "stated_at": now}
        updates[field] = suggested
        return
    if field in explicit_overrides:
        return  # an explicit override already wins; a one-off mention doesn't erode it
    counters = inferred_signals.setdefault(field, {})
    key = str(suggested)
    counters[key] = counters.get(key, 0) + 1
    if counters[key] >= _INFERENCE_THRESHOLD:
        updates[field] = suggested


def apply_turn(conn: sqlite3.Connection, *, app_user_id: str, message: str) -> None:
    """Update this user's profile from one turn's message: explicit first, then inferred.

    See the module docstring for the explicit-vs-inferred distinction.
    """
    profile = get_or_create_profile(conn, app_user_id=app_user_id)
    original_explicit = json.loads(profile["explicit_overrides"] or "{}")
    original_inferred = json.loads(profile["inferred_signals"] or "{}")
    explicit_overrides = dict(original_explicit)
    inferred_signals = json.loads(json.dumps(original_inferred))  # deep copy, nested dicts
    mention = detect_mention(message)
    now = datetime.now(UTC).isoformat()
    is_standing = mention.get("explicit", False)

    updates: dict[str, object] = {}
    _apply_field(
        "preferred_format",
        mention.get("preferred_format"),
        is_standing=is_standing,
        explicit_overrides=explicit_overrides,
        inferred_signals=inferred_signals,
        updates=updates,
        now=now,
    )
    _apply_field(
        "preferred_depth",
        mention.get("preferred_depth"),
        is_standing=is_standing,
        explicit_overrides=explicit_overrides,
        inferred_signals=inferred_signals,
        updates=updates,
        now=now,
    )
    if "prefers_charts" in mention:
        _apply_field(
            "prefers_charts",
            int(mention["prefers_charts"]),
            is_standing=is_standing,
            explicit_overrides=explicit_overrides,
            inferred_signals=inferred_signals,
            updates=updates,
            now=now,
        )

    if (
        not updates
        and explicit_overrides == original_explicit
        and inferred_signals == original_inferred
    ):
        return  # genuinely nothing changed this turn, not even an inference counter bump

    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    params: list[object] = list(updates.values())
    assignment = f"{set_clauses}, " if set_clauses else ""
    conn.execute(
        f"UPDATE user_profiles SET {assignment}explicit_overrides = ?, inferred_signals = ?, "
        "updated_at = ? WHERE app_user_id = ?",
        (*params, json.dumps(explicit_overrides), json.dumps(inferred_signals), now, app_user_id),
    )
    conn.commit()


def as_prompt_block(profile: sqlite3.Row) -> str:
    """Render a profile as a line to splice into the synthesis prompt, or '' if unset."""
    parts = []
    if profile["preferred_format"]:
        parts.append(f"format: {profile['preferred_format']}")
    if profile["preferred_depth"]:
        parts.append(f"depth: {profile['preferred_depth']}")
    if profile["prefers_charts"] is not None:
        parts.append("charts: " + ("yes" if profile["prefers_charts"] else "no, text only"))
    if not parts:
        return ""
    return "User preferences (follow these over the default persona verbosity): " + ", ".join(parts)
