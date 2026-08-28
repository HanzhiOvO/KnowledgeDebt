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

## GMK G10 寝室服务器与校园网访问

推荐把 GMK G10 当作单机自托管服务器，使用 Docker Compose 运行生产构建。Windows 10/11 只需预先安装一次 Docker Desktop；GMK G10 不需要安装 Node.js、Python 或 PostgreSQL 到宿主系统。

### Windows 一键启动（推荐）

把仓库克隆或解压到 GMK G10 后，直接双击：

```text
一键启动服务器.bat
```

英文入口 `server.bat` 与它完全等价。

第一次运行会依次完成：

1. 若 Docker Desktop 已安装但没有运行，自动启动并等待它就绪；
2. 选择 OpenCode Go、OpenAI、DeepSeek、OpenCode Zen、自定义兼容服务或本地规则引擎；
3. 在隐藏输入框中粘贴 API Key，Key 只写入服务器 `.env`，不会出现在终端或浏览器 JavaScript 中；
4. 自动生成 PostgreSQL 密码和后端访问令牌；
5. 构建并后台启动 Web、FastAPI 与 PostgreSQL；
6. 弹出 Windows UAC，只为本地子网放行 TCP 3000；
7. 验证同源 API 后自动打开本机页面，并显示其他设备可打开的校园网地址。

其他设备唯一需要做的事情是在浏览器打开脚本显示的地址，例如：

```text
http://10.20.30.45:3000
```

客户端不安装程序、不填写 API Key；所有设备共享 GMK G10 上的服务配置与课程数据。需要更换 Key、供应商或默认模型时，双击：

```text
配置AI服务.bat
```

英文入口 `server-configure.bat` 与它完全等价。

重新配置会更新服务器 `.env`、同步系统保留的“环境变量 · AI” Profile 并重启容器，不会删除数据库或上传资源。当前 OpenCode Go 预设使用官方 `https://opencode.ai/zen/go/v1` OpenAI-compatible 端点与 `deepseek-v4-flash`。OpenAI 预设同时启用兼容语音转写；DeepSeek、OpenCode Go/Zen 和自定义服务默认只用于 AI 分析，自动云端转写保持关闭，避免把录音发送到不支持 ASR 的接口。

PowerShell 管理命令仍可使用：

```powershell
.\server.ps1
.\server.ps1 configure
.\server.ps1 status
.\server.ps1 logs
.\server.ps1 stop
```

Windows 脚本会自动申请管理员权限创建 `KnowledgeDebt Web 3000 (Local Subnet)` 防火墙规则。若用户取消 UAC，服务器仍会启动，但其他设备可能被 Windows 防火墙拦截。该规则只允许本地子网连接 Web，不开放 FastAPI 8123。

如果自动 UAC 被组策略阻止，可在管理员 PowerShell 中手动执行等价命令：

```powershell
New-NetFirewallRule -DisplayName "KnowledgeDebt Web 3000 (Local Subnet)" `
  -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow `
  -Profile Any -RemoteAddress LocalSubnet
```

### Linux 首次启动

```bash
git clone https://github.com/HanzhiOvO/KnowledgeDebt.git
cd KnowledgeDebt
./server.sh
```

`server.sh` 会完成以下操作：

1. 从 `.env.example` 创建不进入 Git 的 `.env`；
2. 在缺少安全值时生成随机 `POSTGRES_PASSWORD` 与 `KNOWLEDGEDEBT_ACCESS_TOKEN`，且不向终端打印；
3. 构建 Web、FastAPI 与 PostgreSQL 生产容器并后台启动；
4. 通过 `http://127.0.0.1:3000/api/backend/health` 验证 Web 和同源后端代理；
5. 显示 GMK G10 的本机地址与校园网 IPv4 访问地址。

常用运维命令：

```bash
./server.sh status
./server.sh logs
./server.sh restart
./server.sh stop       # 保留数据库、资源和模型数据卷
```

容器配置为 `restart: unless-stopped`。Linux 还应让 Docker 随系统启动：

```bash
sudo systemctl enable --now docker
```

### Linux 防火墙与访问验证

先用 `ip -4 addr` 找到 GMK G10 的校园网 IPv4 和实际子网。以下示例中的 `10.20.30.0/24` 必须替换成真实子网，不要照抄：

```bash
sudo ufw allow from 10.20.30.0/24 to any port 3000 proto tcp
```

在 GMK G10 本机验证：

```bash
curl http://127.0.0.1:3000/api/backend/health
```

再用同一校园网内的手机或电脑打开脚本显示的地址，例如：

```text
http://10.20.30.45:3000
```

仅需要 TCP 3000。`compose.yaml` 把 Web 发布到 `${KNOWLEDGEDEBT_WEB_BIND_ADDRESS:-0.0.0.0}:${KNOWLEDGEDEBT_WEB_PORT:-3000}`，但把 API 固定发布到 `127.0.0.1:8123`；不要为了排障把 8123 改成 `0.0.0.0`。

若要改 Web 端口，在 GMK G10 的 `.env` 中修改并重启：

```dotenv
KNOWLEDGEDEBT_WEB_BIND_ADDRESS=0.0.0.0
KNOWLEDGEDEBT_WEB_PORT=3000
```

校园网地址最好在宿舍路由器或校园网管理界面中做 DHCP 保留；否则重连后 IPv4 可能变化，需要以 `./server.sh status` 或 `ipconfig` 显示的新地址为准。

### 无 Docker 的临时源码模式

已经安装 Python 3.12+、Node.js 24+ 和 npm 时，可以显式开启局域网监听：

```bash
./start.sh --lan
```

Windows 使用：

```powershell
.\start.ps1 -Lan
```

普通 `./start.sh` / `start.bat` 仍只监听 `127.0.0.1`；只有显式的 `--lan` / `-Lan` 才会让 Next.js 监听 `0.0.0.0:3000`。这一路径是开发服务器，终端关闭后服务也会停止，不适合长期无人值守。

### 校园网常见阻断与安全边界

- GMK G10 本机可访问、其他设备连接超时：先检查宿主机防火墙；若规则正确，校园网很可能启用了 AP / 客户端隔离。应用代码无法绕过该网络策略，可改用自己的路由器私有 LAN，或在自己的设备之间使用 Tailscale 等私网。
- 页面能打开但数据请求失败：运行 `./server.sh logs`，并在本机请求 `/api/backend/health`；不要向校园网开放 8123。
- 多个网卡显示了错误地址：启动前设置 `KNOWLEDGEDEBT_SERVER_IP=实际地址`，它只影响脚本显示，不改变监听范围。
- 当前前端没有多用户账号隔离。任何能够访问 3000 的设备都可以操作同一份数据；校园网不是可信公网边界，不应在路由器上配置公网端口转发。
- 需要跨网络或仅限本人设备访问时，优先选择私有 VPN；若确需互联网公开访问，应增加 HTTPS 反向代理和独立身份认证，而不是直接暴露此 Compose 栈。

## Compose 自托管

创建 `.env` 并设置互不相同的高强度密钥：

```dotenv
POSTGRES_PASSWORD=请替换为长随机密码
KNOWLEDGEDEBT_ACCESS_TOKEN=请替换为另一个长随机令牌
OPENAI_API_KEY=可选的供应商密钥
```

启动服务：

```bash
./server.sh
# 等价的底层命令：docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8123/health
```

后端会等待 PostgreSQL 健康检查，通过 `alembic upgrade head` 升级结构，再启动 Uvicorn。API 只发布到 `127.0.0.1:8123`；Web 默认在 `0.0.0.0:3000` 发布，并通过 Compose 私网访问 API。

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
| OpenCode Go | `https://opencode.ai/zen/go/v1` | `deepseek-v4-flash` | 官方 OpenAI-compatible Go 端点，真实结构化输出能力需用户测试 |
| OpenCode Zen | `https://opencode.ai/zen/v1` | `deepseek-v4-flash` | 兼容模板，真实模型能力需用户测试 |
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
