# KnowledgeDebt

> Classroom attendance may fail. Learning continuity should not.

KnowledgeDebt is an open-source, Web-first system for recovering lectures a student missed or did not master. It reconstructs a real **Course Session** from evidence, creates a source-grounded learning path, measures knowledge debt, and clears that debt only after sufficient mastery evidence.

[中文（默认）](README.md) · [Deployment](docs/deployment.md) · [Privacy](docs/privacy.md) · [Architecture](docs/architecture/0001-web-first-thin-backend.md)

> Experimental `0.x` software: APIs and schemas may evolve before a future `1.0`.

## Product loop

```text
Course Session → evidence → classroom reconstruction → Knowledge Points
→ from-zero learning path → adaptive Mastery Assessment → gap diagnosis
→ targeted remediation → reassessment → debt cleared → Session complete
```

A Session exists independently of recordings. Supplementary sources may teach but cannot prove classroom coverage. Reconstruction confidence and learning coverage are separate, and reading alone never clears debt.

## Implemented

- responsive Next.js 16 / React 19 primary Web client;
- FastAPI API with optional single-user bearer-token protection and same-origin Web proxy;
- four bounded evidence channels and recording-union timeline coverage;
- validated transcript, page, slide, chunk, and URL citations;
- PDF/PPTX/text extraction, visual derivatives, embeddings, and dual-policy retrieval;
- adaptive multi-KP assessment, follow-ups, persisted MasteryEvidence, and dependencies;
- background transcription, indexing, analysis, and assessment jobs;
- replaceable AI, ASR, embedding, and local/S3 storage providers;
- SQLite development, PostgreSQL deployment, SQLAlchemy, and Alembic;
- Docker Compose and a preserved Flutter prototype under `legacy/flutter-client/`.

## Start locally

Requirements: Python 3.12+, Node.js 24+, and npm 11+.

```bash
git clone https://github.com/HanzhiOvO/KnowledgeDebt.git
cd KnowledgeDebt
make backend-install
make web-install
cp .env.example .env
make dev
```

Open `http://localhost:3000`. SQLite and local file storage work without separate services. Configure an OpenAI-compatible provider in `.env` for hosted analysis and transcription. Local deterministic hash embeddings are the privacy-preserving default.

## Test

```bash
make verify
```

The realistic E2E test generates a valid 90-minute WAV, a 40-slide PPTX, and an 80-page PDF, then runs them through upload, parsing, transcription, retrieval, reconstruction, remediation, adaptive assessment, evidence aggregation, and debt clearing. CI also validates PostgreSQL 16 and the preserved legacy Flutter client.

## Privacy

External calls require an operation-specific manifest naming the provider, exact resources, data sent, and data not sent. Provider keys remain server-side. Original media is sent only for a selected transcription action, and model-produced evidence locators are validated by the API.

Recording classes may require permission under local law, university policy, course rules, and the expectations of people in the room. See the [privacy model](docs/privacy.md).

## Project

KnowledgeDebt is an experimental open-source Vibe Coding project built collaboratively by a human creator and AI coding agents under human-directed product ownership.

Owner: **HanzhiOvO** · License: [MIT](LICENSE)
