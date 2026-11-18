"""Confirm the Markdown link checker (scripts/check_markdown_links.py) catches real breaks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_markdown_links import broken_links, main


def test_valid_relative_link_is_not_flagged(tmp_path: Path) -> None:
    """A relative link to a file that exists is not reported as broken."""
    (tmp_path / "target.md").write_text("target", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text("See [target](target.md).", encoding="utf-8")

    assert broken_links(doc) == []


def test_missing_relative_link_is_flagged(tmp_path: Path) -> None:
    """A relative link to a file that does not exist is reported."""
    doc = tmp_path / "doc.md"
    doc.write_text("See [missing](missing.md).", encoding="utf-8")

    assert broken_links(doc) == ["missing.md"]


def test_external_and_anchor_links_are_ignored(tmp_path: Path) -> None:
    """External URLs, mailto links, and in-page anchors are never checked."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        "[site](https://example.com) [mail](mailto:a@example.com) [anchor](#section)",
        encoding="utf-8",
    )

    assert broken_links(doc) == []


def test_links_inside_fenced_code_blocks_are_ignored(tmp_path: Path) -> None:
    """A Markdown link shown inside a fenced code example is not a real link."""
    doc = tmp_path / "doc.md"
    doc.write_text("```md\n[missing](missing.md)\n```", encoding="utf-8")

    assert broken_links(doc) == []


def test_main_returns_nonzero_on_broken_link(tmp_path: Path) -> None:
    """The CLI entry point exits 1 when any file has a broken link."""
    doc = tmp_path / "doc.md"
    doc.write_text("[missing](missing.md)", encoding="utf-8")

    assert main([str(doc)]) == 1


def test_main_returns_zero_when_all_links_resolve(tmp_path: Path) -> None:
    """The CLI entry point exits 0 when every link resolves."""
    (tmp_path / "target.md").write_text("target", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text("[target](target.md)", encoding="utf-8")

    assert main([str(doc)]) == 0
