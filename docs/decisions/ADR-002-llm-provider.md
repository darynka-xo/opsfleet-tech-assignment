# ADR-002: LLM provider and model selection — Gemini via AI Studio

**Status:** Accepted (validated against the live API, 2026-11-18)

## Context

The brief requires Gemini for grading ("this is what we will use for testing"). Two access
paths exist: Google AI Studio (API key, simplest auth) and Vertex AI (IAM-integrated,
enterprise quota/VPC-SC controls, more setup). A direct `google-genai` SDK call vs. the
`langchain-google-genai` wrapper was also a live question once LangGraph was chosen (ADR-001).

## Decision

- **Access path: Google AI Studio API key** for the prototype. Simpler auth (one API key vs.
  service-account/IAM wiring), matches the brief's own setup pointer ("get a free API key
  from Google AI Studio"). Vertex AI is the documented production alternative — same model
  catalog, swap is just a client-construction change, not a redesign — noted here rather than
  implemented, since the brief doesn't require it and it adds GCP IAM complexity with no
  prototype benefit.
- **SDK: call `google-genai` directly from LangGraph nodes**, not `langchain-google-genai`.
  LangGraph doesn't require LangChain's chat-model abstraction, and direct SDK calls give
  full control over structured output / function-calling for SQL generation without an extra
  translation layer. `langchain-google-genai` stays in `requirements.txt` in case a later day
  wants a prebuilt LangGraph agent pattern, but isn't load-bearing today.
- **Models** (confirmed live against the API key's actual catalog — see "What we found"
  below, since pre-Day-1 web research on model names turned out stale/unreliable):
  - `gemini-2.5-flash` — default for SQL generation, intent routing/guardrail classification,
    and the self-correction retry loop. Fast and cheap, which matters since these are the
    highest-call-volume nodes per turn.
  - `gemini-2.5-pro` — for final report/insight synthesis, where answer quality matters more
    than latency and call volume is one-per-turn.
  - `gemini-embedding-001` — for Golden Bucket question embeddings (3072-dim, confirmed
    working).
  - All three are the stable, fully-documented tier (`createCachedContent` and
    `batchGenerateContent` support, which matters for later cost optimization via prompt
    caching). Several newer `gemini-3.x` flash variants and some clearly-unofficial
    codenamed models (`gemini-4-argon/carbon/hydrogen`) are also available to this key but are
    deliberately not used as the default — no stable public documentation to build a
    submission around, and the `LLMClient` abstraction makes swapping to them later a
    one-line change if the client prefers it.

## What we found (live catalog check, superseding earlier web research)

Earlier best-practices research (done before GCP access existed) surfaced conflicting,
unverifiable claims about Gemini model names and pricing from third-party blogs. Once the API
key existed, `client.models.list()` gave ground truth instead — see the full catalog note in
`PLANNING.md`'s decision table. Lesson applied project-wide: prefer a live check over
synthesized web research wherever one is cheaply available.

## Consequences

- `GEMINI_API_KEY` and `GOOGLE_CLOUD_PROJECT` are the two required `.env` variables (see
  `.env.example`). Both are live and validated.
- Rate limits for this tier were not explicitly re-verified against `ai.google.dev` on Day 1;
  revisit if Day 4's higher call volume (per-turn SQL-gen + retries + synthesis) hits 429s.

## Validation

`scripts/day1_spike.py` confirmed `generate_content` end to end; a standalone check (not
retained) confirmed `embed_content` on `gemini-embedding-001` returns 3072-dim vectors.
