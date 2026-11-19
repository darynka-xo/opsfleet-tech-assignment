"""The `LLMClient` interface: the one seam a provider swap goes through.

AGENTS.md calls this out as a deliberate exception to "no premature abstraction" -- the
brief explicitly requires the agent to support a provider other than Gemini. Only the
methods a day's work actually needs are added; `embed` landed Day 3 for the Golden Bucket,
`generate` lands Day 4 for SQL generation and report synthesis.
"""

from __future__ import annotations

from typing import Protocol

from google import genai
from google.genai.types import GenerateContentConfig, HttpOptions

from retail_agent.resilience import CircuitBreaker, call_with_resilience


class LLMClient(Protocol):
    """What any LLM provider adapter must implement."""

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order.

        Args:
            texts: The strings to embed.
            model: The embedding model name (provider-specific).
        """
        ...

    def generate(self, prompt: str, *, model: str, system_instruction: str | None = None) -> str:
        """Return the model's text completion for `prompt`.

        Args:
            prompt: The user-turn content.
            model: The model name (provider-specific).
            system_instruction: Optional system-level instructions (persona, task framing).
        """
        ...


class GeminiClient:
    """Direct `google-genai` SDK calls -- no LangChain chat-model wrapper (ADR-002).

    Every call goes through `resilience.call_with_resilience`: a transient failure
    (timeout, 5xx, rate limit) gets retried with backoff; a service that keeps failing
    trips this client's own circuit breaker rather than retrying forever (requirement 5).
    """

    def __init__(self, api_key: str) -> None:
        """Create a client authenticated with the given Google AI Studio API key.

        Pinned to the `v1beta` API version -- found by actually hitting it: `v1`
        rejects `system_instruction` with "Developer instruction is not enabled for
        api version v1", which the router/guardrail/synthesis prompts all rely on.
        """
        self._client = genai.Client(api_key=api_key, http_options=HttpOptions(api_version="v1beta"))
        self._breaker = CircuitBreaker()

    def embed(self, texts: list[str], *, model: str = "gemini-embedding-001") -> list[list[float]]:
        """Embed each text with the given Gemini embedding model.

        Args:
            texts: The strings to embed.
            model: The embedding model name.

        Returns:
            One float vector per input text, in the same order.
        """
        response = call_with_resilience(
            lambda: self._client.models.embed_content(model=model, contents=texts),
            service_name="gemini-embed",
            breaker=self._breaker,
        )
        if response.embeddings is None:
            raise RuntimeError(f"Gemini returned no embeddings for model {model!r}")
        vectors: list[list[float]] = []
        for embedding in response.embeddings:
            if embedding.values is None:
                raise RuntimeError(f"Gemini returned an embedding with no values (model {model!r})")
            vectors.append(embedding.values)
        return vectors

    def generate(self, prompt: str, *, model: str, system_instruction: str | None = None) -> str:
        """Return the model's text completion for `prompt`.

        Args:
            prompt: The user-turn content.
            model: The model name, e.g. `gemini-2.5-flash` or `gemini-2.5-pro`.
            system_instruction: Optional system-level instructions (persona, task framing).

        Returns:
            The response text.

        Raises:
            RuntimeError: Gemini returned no text (an empty or blocked response).
        """
        config = (
            GenerateContentConfig(system_instruction=system_instruction)
            if system_instruction
            else None
        )
        response = call_with_resilience(
            lambda: self._client.models.generate_content(
                model=model, contents=prompt, config=config
            ),
            service_name="gemini-generate",
            breaker=self._breaker,
        )
        if not response.text:
            raise RuntimeError(
                f"Gemini returned no text for model {model!r} (empty or blocked response)"
            )
        return response.text
