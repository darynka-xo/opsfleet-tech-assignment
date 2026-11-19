"""The LangGraph state schema for one conversation turn.

Turn-scoped fields (sql, query_result, error, ...) are overwritten at the start of each
`invoke()` by the CLI, so last-write-wins is the right reducer for them even though the
checkpointer persists the whole state across turns. `history` is the one field that
genuinely accumulates across turns within a session (requirement: "discuss about it").
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

Intent = Literal["analysis", "off_topic", "delete"]
DeleteFilterKind = Literal["session", "mention"]


class Turn(TypedDict):
    """One past (question, answer) pair, kept for in-conversation context."""

    question: str
    answer: str


class AgentState(TypedDict):
    """State threaded through the graph for a single turn of a session."""

    session_id: str
    app_user_id: str
    turn_id: str
    question: str
    history: Annotated[list[Turn], operator.add]

    intent: Intent | None
    rejection_reason: str | None

    # Plain dicts, not GoldenBucketTrio objects: the checkpointer's serializer warns on
    # (and will eventually block) persisting an unregistered custom type.
    retrieved_trios: list[dict[str, object]]

    sql: str | None
    sql_attempts: int
    last_error: str | None
    needs_more_data_reason: str | None

    query_result: list[dict[str, object]] | None
    query_row_count: int | None

    # Destructive-ops flow (requirement 5): which reports a delete request targets, the
    # resolved candidate set, and the user's confirm/cancel answer from the interrupt().
    delete_filter_kind: DeleteFilterKind | None
    delete_filter_value: str | None
    delete_candidates: list[dict[str, object]] | None
    delete_confirmed: bool | None

    final_answer: str | None
