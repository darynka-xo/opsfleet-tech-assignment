"""The `LLMClient` interface: the one seam a provider swap goes through.

AGENTS.md calls this out as a deliberate exception to "no premature abstraction" -- the
brief explicitly requires the agent to support a provider other than Gemini. Only the
methods a day's work actually needs are added; `embed` lands Day 3 for the Golden Bucket,
`generate` lands Day 4 for SQL generation and report synthesis.
"""

from __future__ import annotations

from typing import Protocol

from google import genai


class LLMClient(Protocol):
    """What any LLM provider adapter must implement."""

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order.

        Args:
            texts: The strings to embed.
            model: The embedding model name (provider-specific).
        """
        ...


class GeminiClient:
    """Direct `google-genai` SDK calls -- no LangChain chat-model wrapper (ADR-002)."""

    def __init__(self, api_key: str) -> None:
        """Create a client authenticated with the given Google AI Studio API key."""
        self._client = genai.Client(api_key=api_key)

    def embed(self, texts: list[str], *, model: str = "gemini-embedding-001") -> list[list[float]]:
        """Embed each text with the given Gemini embedding model.

        Args:
            texts: The strings to embed.
            model: The embedding model name.

        Returns:
            One float vector per input text, in the same order.
        """
        response = self._client.models.embed_content(model=model, contents=texts)
        if response.embeddings is None:
            raise RuntimeError(f"Gemini returned no embeddings for model {model!r}")
        vectors: list[list[float]] = []
        for embedding in response.embeddings:
            if embedding.values is None:
                raise RuntimeError(f"Gemini returned an embedding with no values (model {model!r})")
            vectors.append(embedding.values)
        return vectors
