# ADR 0001: Web-first client and thin backend

- Status: accepted
- Date: 2026-08-15
- Owner: HanzhiOvO

## Context

The Technical MVP proved the core loop with FastAPI, SQLite, and a Flutter client. Maintaining four native targets before the learning model is stable would spread the project too thin. Self-hosters may also run KnowledgeDebt on a small server that cannot host a large language model, ASR model, OCR stack, and embedding model at the same time.

The product invariant is not a client technology. It is the loop:

`Course Session → evidence → reconstruction → Knowledge Point → learning → Mastery Assessment → remediation → debt cleared`.

A Session remains valid with zero resources. A recording remains only one optional resource.

## Decision

KnowledgeDebt is Web-first. `web/` is the primary Next.js + React + TypeScript client. The original Flutter implementation is preserved under `legacy/flutter-client/` as an experimental client and API compatibility reference.

FastAPI remains the application boundary. The backend owns structured results and orchestration, while swappable providers own expensive work:

- `AIProvider` for analysis, question generation, evaluation, and remediation;
- `TranscriptionProvider` for ASR;
- `EmbeddingProvider` for retrieval vectors;
- `StorageProvider` for local or S3-compatible object storage.

The default self-hosted profile is a thin backend using external AI providers. Local AI implementations are optional and never required by the base deployment.

SQLite remains the zero-configuration development database. Schema migrations are explicit and tested. PostgreSQL is the production direction; pgvector is an optional retrieval backend, not a requirement for local development.

## Boundaries

- Browser clients never receive AI provider secrets.
- The server validates every evidence locator. A model cannot invent timestamps, pages, slides, chunks, or resource identifiers.
- External uploads require an operation-specific manifest and explicit consent. The UI names the exact provider and resources that will leave the server.
- Reconstruction coverage and learning coverage are distinct metrics.
- Watching or reading content does not clear debt. Mastery is aggregated from persisted assessment evidence.
- Long-running transcription, indexing, analysis, and assessment generation are jobs with visible progress.

## Consequences

The Web client can ship to desktop and mobile browsers from one codebase. The backend stays deployable on roughly 2 CPU cores and 2–4 GB RAM when external providers are used. Native-platform integrations are deferred. The Flutter code remains available but does not gate primary CI or Web delivery.
