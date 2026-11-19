"""Tests for persona loading (requirement 8). Offline, deterministic, no network."""

from __future__ import annotations

from pathlib import Path

from retail_agent import persona


def test_load_persona_reads_the_real_config_file() -> None:
    result = persona.load_persona()

    assert result.tone
    assert result.default_verbosity
    assert result.formatting_rules


def test_load_persona_falls_back_to_safe_defaults_on_missing_file(tmp_path: Path) -> None:
    result = persona.load_persona(tmp_path / "does_not_exist.yaml")

    assert result.tone
    assert result.default_verbosity == "balanced"


def test_load_persona_falls_back_to_safe_defaults_on_malformed_yaml(tmp_path: Path) -> None:
    bad_file = tmp_path / "persona.yaml"
    bad_file.write_text("tone: [unclosed", encoding="utf-8")

    result = persona.load_persona(bad_file)

    assert result.tone
    assert result.default_verbosity == "balanced"


def test_load_persona_reads_fresh_every_call(tmp_path: Path) -> None:
    config_file = tmp_path / "persona.yaml"
    config_file.write_text("tone: First tone.\ndefault_verbosity: concise\n", encoding="utf-8")
    first = persona.load_persona(config_file)

    config_file.write_text("tone: Second tone.\ndefault_verbosity: detailed\n", encoding="utf-8")
    second = persona.load_persona(config_file)

    assert first.tone == "First tone."
    assert second.tone == "Second tone."
    assert second.default_verbosity == "detailed"


def test_as_prompt_block_includes_closing_note_only_when_set() -> None:
    without_note = persona.Persona(
        tone="t", default_verbosity="balanced", formatting_rules="f", closing_note=""
    )
    with_note = persona.Persona(
        tone="t", default_verbosity="balanced", formatting_rules="f", closing_note="Thanks!"
    )

    assert "Thanks!" not in without_note.as_prompt_block()
    assert "Thanks!" in with_note.as_prompt_block()
