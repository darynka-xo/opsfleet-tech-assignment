"""Tests for the output-side PII scrub (defense-in-depth on free-text LLM output)."""

from __future__ import annotations

from retail_agent.pii_scrub import scrub_pii


def test_redacts_an_email_address() -> None:
    assert (
        scrub_pii("Contact jane.doe@example.com for details.") == "Contact [redacted] for details."
    )


def test_redacts_a_us_phone_number() -> None:
    assert scrub_pii("Call 555-123-4567 now.") == "Call [redacted] now."


def test_redacts_a_us_phone_number_with_parens_and_country_code() -> None:
    assert scrub_pii("Dial +1 (555) 123-4567.") == "Dial [redacted]."


def test_leaves_plain_text_untouched() -> None:
    text = "Revenue grew 12% quarter over quarter for Calvin Klein."
    assert scrub_pii(text) == text


def test_does_not_mangle_a_plain_order_id_number() -> None:
    text = "Order id 1023456 shipped yesterday."
    assert scrub_pii(text) == text


def test_redacts_multiple_matches_in_one_string() -> None:
    text = "Reach a@b.com or c@d.com for help."
    assert scrub_pii(text) == "Reach [redacted] or [redacted] for help."
