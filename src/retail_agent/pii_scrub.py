"""Output-side PII scrub: a second defense layer on the synthesized report text.

The primary control is the Day 3 masked views -- PII columns are never in a result set a
generated query can return, so there's nothing for the model to echo. This module exists
for the belt-and-suspenders case the brief calls for anyway: the model free-texting
something that merely *looks* like PII (an invented example email, a phone number typed
as part of a hypothetical), which the data-layer control can't catch because it isn't
data at all. Pattern-based, not a data lookup -- it has no access to real PII to compare
against, and doesn't need any to redact an email- or phone-shaped string.
"""

from __future__ import annotations

import re

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# US-style phone numbers: optional country code, common separators. Deliberately narrow
# (not international) -- a looser pattern risks redacting legitimate numeric data like
# order ids, which would silently corrupt a real report.
#
# The leading boundary is `(?<!\w)`, not `\b`: a `+` country-code prefix is itself a
# non-word character, so a `\b` immediately before an optional `\+?` can never anchor on
# the `+` -- the match starts one character late and leaves a stray "+" outside the
# redaction. `(?<!\w)` anchors on "not preceded by a word character" instead, which the
# `+` (and the start of string) both satisfy.
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?1?[-.\s]?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")


def scrub_pii(text: str) -> str:
    """Redact anything that looks like an email address or a US-style phone number.

    Args:
        text: The report text to scrub before it reaches the user.

    Returns:
        `text` with any matches replaced by `[redacted]`.
    """
    text = _EMAIL_PATTERN.sub("[redacted]", text)
    return _PHONE_PATTERN.sub("[redacted]", text)
