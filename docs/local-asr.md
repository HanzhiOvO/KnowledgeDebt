# 本地 ASR（寝室服务器 / 本机转写）

本文覆盖 v0.2 的两个**已实现**本地转写适配器，以及无 GPU 服务器的选型建议。目标是把课堂录音留在本机或私网内完成转写，避免长时间占用 MacBook，也避免把音频交给外部服务。

```text
浏览器 → KnowledgeDebt Web → FastAPI（分片 / 断点续跑 / 台账）
      → 本地 whisper.cpp 进程 或 私网 ASR 服务 → 带时间戳的转写分片
```

## 两种接入方式

| 适配器 | 状态 | 适用场景 | 音频去向 |
| --- | --- | --- | --- |
| `local_whisper_cpp` | 已实现，需自行安装运行时 | API 与转写在同一台机器（MacBook 或寝室服务器） | 只在本机进程之间传递 |
| `local_openai_asr` | 已实现，需自行部署服务 | 转写放在另一台私网机器，API 通过 HTTP 调用 | 只在私网内传输 |

两者都：

- 强制 `external=false`：不触发逐次外发授权，也不会被计为外部调用；
- 只声明 `audio_transcription` 与 `segment_timestamps`；**不声明** `long_audio`、`speaker_diarization`、`hotwords`；
- 长录音仍由 FFmpeg 分片（默认 1500 秒/片），成功分片跳过、失败分片续跑、时间戳按全课堂合并；
- 失败、超时、取消都不会删除原始录音或已成功分片。

`local_openai_asr` 的 Base URL 必须是私网地址：`localhost`、`127.0.0.0/8`、`10./172.16-31./192.168.`、Tailscale `100.64.0.0/10`、`fd00::/8`、`.local`/`.lan`/`.internal`/`.ts.net` 或不含点号的局域网主机名。填入公网地址会被后端拒绝（HTTP 422），防止把外发伪装成本地。

## 无 GPU 服务器选型（16GB 及以上内存）

| 模型 | 磁盘 | 常驻内存 | 中文效果 | 速度参考（CPU） |
| --- | --- | --- | --- | --- |
| `ggml-tiny`（78 MB） | 极小 | ~0.4 GB | 只能听出大意，技术名词基本错 | 本机实测 3.18× 实时 |
| `ggml-small`（466 MB） | 小 | ~1 GB | 可用，专业名词易错 | 约 2–4× 实时 |
| **`ggml-medium`（1.5 GB，推荐）** | 中 | ~2.6 GB | 明显更好，适合课堂 | 约 1–2× 实时 |
| `ggml-large-v3-turbo`（1.6 GB） | 中 | ~2.5 GB | 接近 large，速度较快 | 视核心数波动大 |

`tiny` 的实测结论（本机 macOS + whisper.cpp 1.9.2，真实 STM32 课程录音 60 秒）：速度 3.18× 实时、时间戳正确，但把 “STM32” 听成 “还是天不沾二”、“51 单片机” 听成 “5 月大面机”。**课堂用途不要用 tiny**，它只适合验证链路是否连通。`small`/`medium` 才有可用的术语准确度。

判断标准：**转写速度必须快于录音时长**，否则会持续积压。先用一节真实课堂录音实测，再决定档位：

```bash
.venv/bin/python backend/scripts/local_asr_smoke.py 你的录音.aac --seconds 60
```

脚本会打印实时倍速、分段时间戳和前几段文本，不写数据库、不外发、不改动原文件。8 核以上 CPU 建议从 `medium` 起步；核心较少时先用 `small`。

> 没有 NVIDIA GPU 时不要安装 CUDA 栈，也不要下载 Qwen3-ASR / faster-whisper 的 GPU 权重。

## 安装 whisper.cpp

macOS：

```bash
brew install whisper-cpp        # 提供 whisper-cli
```

Debian / Ubuntu（源码编译，建议固定 tag）：

```bash
sudo apt-get install -y build-essential cmake git
git clone --depth 1 --branch v1.7.4 https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j"$(nproc)"
sudo install -m755 build/bin/whisper-cli /usr/local/bin/whisper-cli
```

下载模型到数据目录（默认 `backend/data/asr-models`）：

```bash
mkdir -p backend/data/asr-models
curl -L -o backend/data/asr-models/ggml-medium.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```

模型文件不属于仓库内容，不要提交。

## 服务端配置

在本机 `.env` 中：

```bash
KNOWLEDGEDEBT_LOCAL_ASR_BINARY=whisper-cli          # 或绝对路径
KNOWLEDGEDEBT_LOCAL_ASR_MODEL=ggml-medium.bin       # 绝对路径、模型目录下文件名，或 medium 简称
KNOWLEDGEDEBT_LOCAL_ASR_MODEL_DIR=                  # 留空则用 <数据目录>/asr-models
KNOWLEDGEDEBT_LOCAL_ASR_LANGUAGE=zh
KNOWLEDGEDEBT_LOCAL_ASR_THREADS=0                   # 0 表示交给 whisper.cpp 决定
KNOWLEDGEDEBT_LOCAL_ASR_TIMEOUT_SECONDS=3600        # 单个分片的墙钟上限
KNOWLEDGEDEBT_LOCAL_ASR_INITIAL_PROMPT=             # 可选：课程术语提示（不是热词接口）
```

私网 ASR 服务额外配置：

```bash
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_URL=http://192.168.1.30:8080/v1
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_MODEL=whisper-small
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH=/audio/transcriptions   # whisper.cpp server 改成 /inference
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_CONVERT_WAV=false            # 服务只接受 WAV 时设为 true
```

### whisper.cpp 自带 server 的实测事实（1.9.2）

本机实测（`whisper-server -m ggml-tiny.bin -l zh --host 127.0.0.1 --port 18080`）：

- **没有** `/v1/models`，也**没有** `/v1/audio/transcriptions`，请求会返回 404；
- 转写接口是 `POST /inference`，`response_format=verbose_json` 返回的 `segments[].start/end/text` 与 OpenAI 格式一致，本适配器可直接解析；
- 1.9.2 直接接受 FLAC 分片，无需先转 WAV；旧版本若报错，再打开 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_CONVERT_WAV=true`；
- `model` 字段会被该服务忽略，但 Profile 仍需填写（用于台账记录）。

对应配置：

```bash
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_URL=http://192.168.1.30:8080
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH=/inference
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_MODEL=ggml-medium
```

「测试连接」在这种服务上会显示“可达（未实现 /models）”，这是真实状态，不是失败。

重启 API 后打开「设置 → Provider」：

1. **本地转写 · whisper.cpp** 卡片会显示可执行文件、模型、模型目录、线程、超时与 FFmpeg 的真实就绪状态；
2. 首次初始化的数据库在本地运行时就绪时，会直接把「语音转写」默认路由指向本地 Profile；
3. 已有数据库不会被改动，请用「＋ 新建 Profile」选择接入方式后手动切换默认路由；
4. 点击「测试连接」会真实检查可执行文件与模型（whisper.cpp）或请求 `/models`（私网服务），不会伪造通过。

## 行为与边界

- **格式**：whisper.cpp 只读 16kHz 单声道 WAV，适配器会先用 FFmpeg 把分片转成 WAV，转换文件放在临时目录并在结束后删除，原始分片不变；
- **时间戳**：优先读取 JSON 的 `offsets`（毫秒），缺失时回退解析 `timestamps`；两者都没有会直接报错，不会伪造时间戳；
- **超时**：超过配置秒数会先 `SIGTERM` 再 `SIGKILL`，并给出可执行的中文建议；
- **取消**：在分片运行中点「取消」会终止本地进程，该分片回到 `pending`，已完成分片保留，重试时只跑剩余分片；
- **重启**：服务重启后 `queued`/`running` 的 Job 会被接管续跑；
- **台账**：只记录操作、Profile、模型、时长与状态，不记录音频、转写正文或密钥。

## 可选：compose 中的 ASR 服务

`compose.yaml` 提供 `local-asr` profile（默认不启动）：

```bash
docker compose --profile local-asr up --build
```

它构建 `deploy/local-asr/`（whisper.cpp 自带 HTTP 服务），只在 compose 内网暴露 8080，并把模型放在 `asr_models` 卷。搭配 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_URL=http://asr:8080` 与 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH=/inference` 使用。**该镜像未在交接环境验证**（交接机器没有 Docker），首次部署请按 `deploy/local-asr/README.md` 自行验证；适配器本身已在本机对真实 `whisper-server` 实测通过。

## 部署建议（寝室服务器）

- 转写与 Web/API 分离，先限制单并发，避免抢占内存；
- 原始媒体、分片和数据库都要放在持久化卷并做备份；
- 不要把 FastAPI 8123 暴露到公网；优先 Tailscale/私网 VPN，并设置 `KNOWLEDGEDEBT_ACCESS_TOKEN`；
- 先用一节真实课堂录音测速，再决定模型档位与分片长度。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 「未找到 whisper.cpp 可执行文件」 | 未安装或路径错误；设置 `KNOWLEDGEDEBT_LOCAL_ASR_BINARY` 或在 Profile 填绝对路径 |
| 「未找到 whisper.cpp 模型」 | 模型未下载或文件名不符；确认模型目录与文件名 |
| 「需要 FFmpeg」 | 安装 FFmpeg 或设置 `KNOWLEDGEDEBT_FFMPEG_PATH`，见 [ffmpeg.md](ffmpeg.md) |
| 「超过 N 秒仍未完成」 | 模型过大或分片过长；调小分片、换小模型或提高超时 |
| 「JSON 结果文件」相关错误 | whisper.cpp 版本过旧，不支持 `-oj/--output-json`，请升级 |
| 私网服务返回 HTTP 400/422 | 多为格式不被接受；把 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_CONVERT_WAV` 设为 `true` |
| 私网服务返回 HTTP 404 | 路径不对；whisper.cpp server 要用 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH=/inference` |
| 中文识别到大意但术语全错 | 模型太小（典型是 tiny）；换 `small` 或 `medium` |

## 回归测试

`backend/tests/test_local_asr.py` 使用真实子进程与 127.0.0.1 回环 HTTP 服务，不访问外部网络、不下载模型，覆盖：命令行契约与 JSON 时间戳解析、时钟时间戳回退、非 WAV 分片转换、缺少运行时/模型/FFmpeg 的可执行报错、真实 stderr 透出、超时与取消真正杀掉进程、私网地址守卫、默认路由选择、运行中取消后的断点续跑，以及 API 层拒绝公网地址并强制本地标记。
