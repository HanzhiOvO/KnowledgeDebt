# 可选的本地 ASR 服务（未在交接环境验证）

本目录构建 whisper.cpp 自带的 HTTP 服务，作为 `local_openai_asr` 适配器的私网后端。

> **状态说明**：交接机器没有安装 Docker，因此该镜像的构建与接口行为**没有在本机验证过**。仓库不会把它标记为“已支持”。首次部署请按下面步骤自行验证，再把对应 Profile 设为默认转写路由。

## 启动

```bash
# 1. 准备模型（宿主机）
docker volume create knowledgedebt_asr_models
docker run --rm -v knowledgedebt_asr_models:/models curlimages/curl:latest \
  -L -o /models/ggml-medium.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin

# 2. 只启动可选 ASR 服务
docker compose --profile local-asr up --build asr
```

## 验证清单

whisper.cpp 1.9.2 的 server 已在本机（非容器）实测：**没有** `/v1/models` 与 `/v1/audio/transcriptions`，转写接口是 `POST /inference`，`verbose_json` 会返回 `segments[].start/end/text`，并且可以直接接受 FLAC。

```bash
# 服务是否可达（compose 内网）：whisper-server 只提供根路径与 /inference
docker compose exec backend curl -sS -o /dev/null -w '%{http_code}\n' http://asr:8080/

# 真实音频是否能转写出分段时间戳
ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -ac 1 -ar 16000 /tmp/tone.wav
curl -sS -F file=@/tmp/tone.wav -F response_format=verbose_json http://127.0.0.1:8080/inference
```

- 若上传 FLAC 返回 4xx（旧版本可能只支持 WAV），在 KnowledgeDebt 的 `.env` 设置 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_CONVERT_WAV=true`，由后端先转成 WAV。
- 换成真正 OpenAI 兼容的服务时，把 `KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH` 改回 `/audio/transcriptions`。

## 在 KnowledgeDebt 中启用

```bash
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_URL=http://asr:8080
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH=/inference
KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_MODEL=ggml-medium
```

然后在「设置 → Provider」新建 Profile，接入方式选择“本地 / 私网 ASR 服务”，填写同一地址；后端只接受私网地址。

## 资源建议

- 先限制单并发：转写是 CPU 密集任务，和 API/Web 抢核会拖慢整个工作台；
- `cpus` 建议不超过物理核数的一半，`mem_limit` 至少为模型常驻内存的两倍；
- 模型放持久化卷，不要打进镜像。
