# Deployment guide

KnowledgeDebt supports two deployment profiles:

- local development: Next.js + FastAPI + zero-configuration SQLite + local files;
- self-hosted: Docker Compose + PostgreSQL 16 + persistent local resource volume, with optional S3-compatible object storage.

The base Compose allocation is 2 CPU cores and 2 GB RAM across all three services. Allow 2–4 GB in practice for document rendering and concurrent jobs. Local LLM, ASR, and OCR runtimes are intentionally outside this base profile.

## Local development

Install Python 3.12+, Node.js 24+, and npm 11+, then run:

```bash
make backend-install
make web-install
cp .env.example .env
make dev
```

Next.js listens on `localhost:3000`; FastAPI listens on `127.0.0.1:8123`. The Web server forwards browser requests through `/api/backend`, so `OPENAI_API_KEY` and `KNOWLEDGEDEBT_ACCESS_TOKEN` are not compiled into browser JavaScript.

`KNOWLEDGEDEBT_AI_PROVIDER=auto`（默认）会在没有 API Key 时启用本地规则引擎 `local_rule`。Web 设置页提供 OpenAI、DeepSeek、Anthropic、OpenCode Zen 的一键 Key 配置，保存到数据目录的 `runtime-provider.json`（0600）并即时生效；环境变量优先级高于 Web 保存的运行时配置。

With `KNOWLEDGEDEBT_DATABASE_URL` empty, the backend creates `knowledgedebt.sqlite3` under `KNOWLEDGEDEBT_DATA_DIR`. Uploaded resources and derived page images stay below the same data root.

## Compose deployment

Create `.env` and set unique secrets:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-password
KNOWLEDGEDEBT_ACCESS_TOKEN=replace-with-a-different-long-random-token
OPENAI_API_KEY=optional-provider-key
```

Then start the stack:

```bash
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8123/health
```

The backend waits for PostgreSQL health, applies `alembic upgrade head`, and then starts Uvicorn. The API publishes only to `127.0.0.1:8123`; the Web service publishes port `3000` and reaches the API over the private Compose network.

For an Internet-facing installation, put a TLS reverse proxy in front of port `3000`, keep `8123` private, and set a strong `KNOWLEDGEDEBT_ACCESS_TOKEN`. The Next.js server adds that token when proxying API calls. This is a single-user protection boundary, not a hosted multi-tenant identity system.

## Database and migrations

Development defaults to SQLite. Production uses:

```dotenv
KNOWLEDGEDEBT_DATABASE_URL=postgresql://user:password@database:5432/knowledgedebt
```

For a local PostgreSQL target, install the optional binary driver first:

```bash
.venv/bin/pip install -r backend/requirements-postgres.txt
```

Run migrations before each application rollout:

```bash
make migrate
```

Compose does this automatically. See [migrations.md](migrations.md) before upgrading an existing data directory.

PostgreSQL is supported now through the SQLAlchemy compatibility adapter. The domain repository is being moved incrementally to typed ORM sessions; the public API does not depend on that internal transition. pgvector is a future optional index for large libraries, not a requirement for the base deployment.

## Storage

Local storage is the default:

```dotenv
KNOWLEDGEDEBT_STORAGE_PROVIDER=local
KNOWLEDGEDEBT_DATA_DIR=/data
```

For AWS S3, Cloudflare R2, MinIO, or another compatible service, install `backend/requirements-s3.txt` in a custom backend image and set:

```dotenv
KNOWLEDGEDEBT_STORAGE_PROVIDER=s3
KNOWLEDGEDEBT_S3_BUCKET=your-private-bucket
KNOWLEDGEDEBT_S3_ENDPOINT_URL=https://optional-compatible-endpoint
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Keep buckets private. StorageProvider materializes a selected object only when a parser or transcription job needs a local file.

## Provider profiles

The default AI contract is OpenAI-compatible when an API key exists. The default ASR provider is local faster-whisper (`local_whisper`), so audio does not leave the machine:

| `KNOWLEDGEDEBT_AI_PROVIDER` | Preset base URL | Default model | Notes |
| --- | --- | --- | --- |
| `openai_compatible` | `https://api.openai.com/v1` | `gpt-5-mini` | OpenAI 官方 Key |
| `deepseek` | `https://api.deepseek.com` | `deepseek-chat` | DeepSeek 官方 Key |
| `anthropic` | `https://api.anthropic.com` | `claude-sonnet-4-5` | 原生 Messages API |
| `opencode` | `https://opencode.ai/zen/v1` | `gpt-5.5` | OpenCode Zen 官方 Key |
| `local_rule` | — | `local` | 零配置本地规则引擎 |

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
KNOWLEDGEDEBT_AI_MODEL=gpt-5-mini
KNOWLEDGEDEBT_ASR_MODEL=gpt-4o-mini-transcribe
```

The default embedding provider is `hash`, which is local, deterministic, and deliberately small. It is useful for private development and modest Session libraries. If `KNOWLEDGEDEBT_EMBEDDING_PROVIDER=openai_compatible` is selected, document upload stores chunks without sending them; an explicitly consented indexing job performs the external call.

## Backup and restore

Back up both the relational database and resource storage as one logical snapshot.

For Compose PostgreSQL:

```bash
docker compose exec -T database pg_dump -U knowledgedebt -d knowledgedebt -Fc > knowledgedebt.dump
```

Also snapshot the `resource_data` volume or the configured S3 bucket. A database-only backup can preserve metadata while losing recordings, documents, and page images. Test restore procedures on a separate deployment before relying on them.

For SQLite, stop the backend or use SQLite's online backup mechanism before copying the database. Never copy only the `.sqlite3` file while assuming resource files are embedded inside it.

## Operational checks

- `GET /health` remains public so container and load-balancer health checks work.
- All other API endpoints require the configured bearer token.
- Long tasks persist status in the `jobs` table; the current worker is in-process and suitable for a single backend replica.
- Run only one backend replica until job execution is moved to a dedicated queue/worker architecture.
- Keep application logs free of resource bodies and provider secrets.
- Monitor PostgreSQL volume, resource volume/bucket, provider quotas, job failures, and reverse-proxy upload limits.
