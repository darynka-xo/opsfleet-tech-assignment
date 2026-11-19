"""Load the agent's persona from `config/persona.yaml` (requirement 8).

Read fresh on every call -- no caching across turns. A non-developer edits the YAML file
directly; the very next turn reflects the change, with no code edit or redeploy. Versioned
in git, so a bad edit is a `git revert`, not an incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PERSONA_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "persona.yaml"


@dataclass(frozen=True)
class Persona:
    """The agent's current tone/verbosity/formatting configuration."""

    tone: str
    default_verbosity: str
    formatting_rules: str
    closing_note: str

    def as_prompt_block(self) -> str:
        """Render this persona as a block to splice into the synthesis prompt."""
        lines = [
            f"Tone: {self.tone.strip()}",
            f"Default verbosity: {self.default_verbosity}",
            f"Formatting rules: {self.formatting_rules.strip()}",
        ]
        if self.closing_note.strip():
            lines.append(f"Always end the report with this exact line: {self.closing_note.strip()}")
        return "\n".join(lines)


def load_persona(path: Path = DEFAULT_PERSONA_PATH) -> Persona:
    """Read and parse `persona.yaml`, with safe defaults for any missing field.

    Args:
        path: Path to the persona YAML file. Defaults to `config/persona.yaml`.

    Returns:
        The current `Persona`. Never raises on a missing/malformed optional field --
        a persona edit should never be able to crash the agent, only change its tone.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError, yaml.YAMLError:
        raw = {}
    return Persona(
        tone=str(raw.get("tone", "Clear and professional.")),
        default_verbosity=str(raw.get("default_verbosity", "balanced")),
        formatting_rules=str(raw.get("formatting_rules", "Lead with the direct answer.")),
        closing_note=str(raw.get("closing_note", "")),
    )
