# 部署指南

知债（KnowledgeDebt）支持三种运行方式：

- macOS 原生应用：面向普通用户，安装包内置所需运行时，见 [macos.md](macos.md)；
- 本地开发：Next.js + FastAPI + 零配置 SQLite + 本地文件；
- 自托管：Docker Compose + PostgreSQL 16 + 持久化资源卷，可选 S3 兼容对象存储和私网 ASR。

基础 Compose 为三个服务合计分配 2 个 CPU 核和 2 GB 内存。文档渲染和并发任务实际建议预留 2–4 GB；本地 LLM、ASR 和 OCR 不计入这个基础额度。

## macOS 原生应用

Apple Silicon 普通用户应优先使用 `.dmg`，无需安装 Python、Node.js、FFmpeg、Docker 或 PostgreSQL。应用只监听 `127.0.0.1`，数据保存在 `~/Library/Application Support/KnowledgeDebt/`。安装、升级、备份、恢复与卸载见 [macOS 安装与数据管理](macos.md)。

## 本地开发

安装 Python 3.12+、Node.js 24+ 和 npm，然后在仓库根目录运行：

```bash
./start.sh
```

脚本会创建 `.env`、Python 虚拟环境并补齐项目依赖。Next.js 监听 `localhost:3000`，FastAPI 监听 `127.0.0.1:8123`。Web 通过同源 `/api/backend` 转发浏览器请求，因此 `OPENAI_API_KEY` 和 `KNOWLEDGEDEBT_ACCESS_TOKEN` 不会编译到浏览器 JavaScript。

Windows 10/11 可双击 `start.bat`，或运行 `.\start.ps1`；它与 `start.sh` 一样使用 SQLite，并只监听本机回环地址。Windows 脚本不会安装 PostgreSQL 二进制驱动或实验性 faster-whisper。

也可以逐项执行：

```bash
make backend-install
make web-install
cp .env.example .env
make dev
```

未设置 `KNOWLEDGEDEBT_DATABASE_URL` 时，后端会在 `KNOWLEDGEDEBT_DATA_DIR` 下创建 `knowledgedebt.sqlite3`。上传资源、录音分片、模型和衍生页面都保存在同一数据根目录下的独立子目录。

没有可用 API Key 时，首次启动会自动把本地规则引擎 `local_rule` 设为 AI 默认路由。它不联网、不下载模型，只整理已检索到的真实资料，并把结果明确标为推断。

Web 设置页提供 OpenAI、DeepSeek 和 OpenCode Zen 快速模板。密钥只能使用 `env:变量名` 引用，或在已配置 `KNOWLEDGEDEBT_ENCRYPTION_KEY` 时加密保存；项目不会创建含明文密钥的 `runtime-provider.json`。保存 Profile 后应先执行最小连接测试，再由用户设为默认路由。

## Compose 自托管

创建 `.env` 并设置互不相同的高强度密钥：

```dotenv
POSTGRES_PASSWORD=请替换为长随机密码
KNOWLEDGEDEBT_ACCESS_TOKEN=请替换为另一个长随机令牌
OPENAI_API_KEY=可选的供应商密钥
```

启动服务：

```bash
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8123/health
```

后端会等待 PostgreSQL 健康检查，通过 `alembic upgrade head` 升级结构，再启动 Uvicorn。API 只发布到 `127.0.0.1:8123`；Web 发布 3000 端口，并通过 Compose 私网访问 API。

若需要从互联网访问，应在 3000 端口前部署 TLS 反向代理，保持 8123 私有，并设置强 `KNOWLEDGEDEBT_ACCESS_TOKEN`。Next.js 服务端代理会附加该令牌。这是单用户保护边界，不是多租户身份系统。

## 数据库与迁移

开发默认使用 SQLite；生产可配置 PostgreSQL：

```dotenv
KNOWLEDGEDEBT_DATABASE_URL=postgresql://user:password@database:5432/knowledgedebt
```

连接本机 PostgreSQL 前，先安装可选二进制驱动：

```bash
.venv/bin/pip install -r backend/requirements-postgres.txt
```

每次应用升级前先备份，再运行：

```bash
make migrate
```

Compose 会自动执行迁移。升级已有数据目录前请阅读 [migrations.md](migrations.md)。PostgreSQL 通过 SQLAlchemy 兼容层支持；单机部署不要求 pgvector。

## 存储

本地存储是默认方案：

```dotenv
KNOWLEDGEDEBT_STORAGE_PROVIDER=local
KNOWLEDGEDEBT_DATA_DIR=/data
```

如需 AWS S3、Cloudflare R2、MinIO 或其他兼容服务，应在自定义后端镜像中安装 `backend/requirements-s3.txt`，并设置：

```dotenv
KNOWLEDGEDEBT_STORAGE_PROVIDER=s3
KNOWLEDGEDEBT_S3_BUCKET=your-private-bucket
KNOWLEDGEDEBT_S3_ENDPOINT_URL=https://optional-compatible-endpoint
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

存储桶必须保持私有。解析或转写任务需要时，`StorageProvider` 才会把选定对象临时物化到本机。

## Provider 与本地转写

配置 API Key 时，当前正式通用协议为 OpenAI-compatible。v0.2 的本地 ASR 正式路径为应用内置 whisper.cpp 或受限私网 ASR 服务；仓库保留的 faster-whisper 实验模块没有接入默认路由，也不会被一键启动或 Docker 默认安装，以避免未经确认下载模型。

| 设置页模板 / 适配器 | Base URL | 建议模型 | 状态 |
| --- | --- | --- | --- |
| OpenAI-compatible / OpenAI | `https://api.openai.com/v1` | `gpt-5-mini` | 合同与 mock 已测试，真实账号需用户测试 |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | 兼容模板，真实模型能力需用户测试 |
| OpenCode Zen | `https://opencode.ai/zen/v1` | `gpt-5.5` | 兼容模板，真实模型能力需用户测试 |
| Anthropic 原生协议 | `https://api.anthropic.com` | — | 当前仍为禁用的接口槽位，不冒充可用 |
| `local_rule` | — | `transparent-rules-v1` | 已测试，零配置、完全离线 |

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
KNOWLEDGEDEBT_AI_MODEL=gpt-5-mini
KNOWLEDGEDEBT_ASR_MODEL=gpt-4o-mini-transcribe
```

默认 Embedding 是本地、确定性的 `hash`，适合私有开发和中小型 Session 库。选择外部 Embedding 后，文档上传仍只保存分片；只有用户明确授权的索引任务才会外发文本。

寝室服务器可直接运行 whisper.cpp CLI，也可以在私网部署 OpenAI-compatible ASR。公网 URL 会被后端拒绝作为“本地服务”。完整配置、能力边界和无 GPU 建议见 [local-asr.md](local-asr.md)。

## 备份与恢复

应把关系数据库和资源存储作为同一个逻辑快照备份。

Compose PostgreSQL 示例：

```bash
docker compose exec -T database pg_dump -U knowledgedebt -d knowledgedebt -Fc > knowledgedebt.dump
```

同时快照 `resource_data` 卷或配置的 S3 桶。只备份数据库会保留元数据，却丢失录音、文档和页面图像。正式依赖备份前，应在独立部署中演练恢复。

SQLite 应在后端停止后复制整个数据目录，或使用 SQLite 在线备份 API。不要只复制 `.sqlite3` 并误以为资源文件嵌在数据库中。

## 运维检查

- `GET /health` 保持公开，供容器和负载均衡器健康检查；
- 其他 API 端点都要求配置的 Bearer Token；
- 长任务状态持久化在 `jobs` 表；当前工作器位于 API 进程内，适合单后端实例；
- 在任务执行迁移到独立队列/工作器前，只运行一个后端副本；
- 日志不得包含资源正文、账号 Cookie、密码、API Key 或完整 Token；
- 监控数据库卷、资源卷/存储桶、模型目录、Provider 配额、任务失败和反向代理上传限制。
