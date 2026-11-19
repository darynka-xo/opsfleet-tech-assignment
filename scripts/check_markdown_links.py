"""Fail when a Markdown file links to a relative path that does not exist."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# No `(?<!!)` exclusion: an `![alt](path)` image embed is just as broken as a `[text](path)`
# link if the path doesn't resolve -- found while adding docs/images/*.png, which this
# checker had never actually been validating.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)
EXTERNAL = re.compile(r"^(https?:|mailto:|#)")


def broken_links(markdown_file: Path) -> list[str]:
    """Return the relative link targets in a Markdown file that do not exist.

    Args:
        markdown_file: Markdown file to scan. Fenced code blocks are ignored.
    """
    text = FENCE.sub("", markdown_file.read_text(encoding="utf-8"))
    broken: list[str] = []
    for match in LINK.finditer(text):
        target = match.group(1)
        if EXTERNAL.match(target):
            continue
        path_part = unquote(target.split("#", 1)[0])
        if path_part and not (markdown_file.parent / path_part).exists():
            broken.append(target)
    return broken


def main(argv: list[str]) -> int:
    """Check every Markdown file passed on the command line.

    Args:
        argv: Paths to Markdown files, as supplied by pre-commit.

    Returns:
        0 when every relative link resolves, 1 otherwise.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    failures = 0
    for name in argv:
        for target in broken_links(Path(name)):
            logger.error("%s: broken link %s", name, target)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
