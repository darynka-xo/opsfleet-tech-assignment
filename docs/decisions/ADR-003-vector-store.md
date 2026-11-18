# ADR-003: Golden Bucket vector store — Qdrant

**Status:** Accepted (embedded-mode round trip validated, 2026-11-18)

## Context

Requirement 1 ("Hybrid Intelligence") needs a vector store for the Golden Bucket: embed
question → SQL → report trios, retrieve the top-k most similar trios at query time to
steer SQL generation. The candidates considered were Qdrant, Chroma, and a hand-rolled
in-memory cosine-similarity index.

## Options considered

1. **Hand-rolled in-memory index.** Zero dependencies, but no persistence across runs, no
   payload filtering (needed to scope retrieval to a `capability_area` or an `approved`
   curation status — Day 6), and reimplements what any vector library already does well.
2. **Chroma, embedded/local mode.** Zero infra for the prototype, simple API. Its own
   docs position it as a prototyping tool first; the realistic production target is a
   different engine entirely (Vertex AI Vector Search or BigQuery `VECTOR_SEARCH`), so the
   prototype's vector store and the production vector store would never actually be the
   same system — only the `VectorStore` interface carries over, not the underlying engine
   or its operational characteristics (indexing behavior, filtering semantics, latency).
3. **Qdrant, embedded/local mode for the prototype.** The chosen option.

## Decision

Use **Qdrant**, via `qdrant-client`'s embedded/local mode (`QdrantClient(path=...)`, no
server process) for the prototype, behind the `VectorStore` interface.

1. **Same engine, prototype to production.** `qdrant-client`'s local mode and a real
   Qdrant deployment (Qdrant Cloud, or self-hosted) speak the same client API — moving to
   production is a connection-string change (`path=...` → `url=...`), not a rewrite behind
   the `VectorStore` interface. Chroma's own production story points elsewhere, so picking
   Qdrant removes a migration this project would otherwise owe the client later.
2. **Payload filtering is a first-class query parameter**, not bolted on — needed for Day
   6's retrieval-time scoping to `approved` (not `candidate`) trios, and for narrowing by
   `capability_area` when that improves few-shot relevance.
3. **Rust engine, HNSW index** — proven at a scale this prototype won't approach, but it's
   also the thing the client would actually run if this become the production retriever,
   not just a demo-only substitute.

## Consequences

- `qdrant-client==1.20.0` replaces `chromadb` in `requirements.txt`. Local storage under
  `qdrant_db/` (gitignored, parallel to the old `chroma_db/` entry).
- No server process for the prototype — embedded mode is a library, not infrastructure, so
  this doesn't add operational burden Day 3–7.
- Local mode is single-process-at-a-time (a file lock) — fine for a CLI-only prototype,
  would need the networked mode for any concurrent-access production deployment, which is
  exactly the `path=...` → `url=...` swap described above.

## Validation

Round-tripped locally on Python 3.14.3: created a collection, upserted a point, queried it
back by vector similarity, got the expected payload and a similarity score. One cosmetic
issue found and not a blocker: `qdrant-client`'s local-mode cleanup (via `portalocker`)
throws a noisy but harmless exception during Python's own interpreter shutdown on 3.14 —
it happens in `__del__` after the client has already completed its work.
