# Provider Profile 与外部数据边界

v0.2 将 AI 分析、语音转写（ASR）和向量检索（Embedding）拆成三组独立默认路由。一个 Profile 只声明后端真实实现并经过配置的能力；能力未声明时，界面不会把它作为对应默认项。

## 当前真实实现状态

| 适配器 | 状态 | 说明 |
| --- | --- | --- |
| OpenAI-compatible | 可用 | 支持实际兼容端点；具体模型能力由用户声明并自行验证 |
| Local Hash Embedding | 可用 | 本地确定性检索，不外发文本，不代表语义模型质量 |
| 本地 whisper.cpp（`local_whisper_cpp`） | 已实现 · 需自行安装运行时 | 调用本机可执行文件转写，解析 JSON 分段时间戳，超时与取消会真正终止子进程；见 [local-asr.md](local-asr.md) |
| 本地 / 私网 ASR 服务（`local_openai_asr`） | 已实现 · 需自行部署服务 | 只允许私网 Base URL，公网地址被拒绝；具体服务需操作者验证 |
| DeepSeek / Kimi / GLM / MiniMax / DashScope 兼容预设 | 未实测预设 | 复用兼容协议，不宣称供应商全部模型均已验证 |
| Anthropic / Gemini 原生协议 | 接口槽位 | 尚未实现，不显示为可用路由 |
| 腾讯云 / Google / DashScope 原生 ASR | 接口槽位 | 尚未实现，不显示为可用路由 |

仓库测试默认只使用本地假 Provider，不会调用付费 API。

## 密钥

推荐在 `.env` 保存供应商密钥，然后在 Profile 中填写 `env:变量名`。如果要从设置页输入并持久化密钥，后端要求 Fernet 主密钥：

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把输出放入本机 `.env` 的 `KNOWLEDGEDEBT_ENCRYPTION_KEY`。没有主密钥时，API 会拒绝明文密钥入库。主密钥和供应商密钥都不得提交到 Git。

## 外部调用授权

- 上传先写入已配置存储，成功后才进入自动化阶段；
- 本地 Provider 可直接执行；
- 外部 ASR 在用户确认前只标记“等待授权”，不创建 Job；
- 确认框列出实际 Vendor、Profile、模型、资源和发送/不发送的数据；
- 授权只对本次操作生效；取消会保留原始文件并显示“未转写”；
- 调用台账只记录操作、路由、模型、时长、状态和可得的费用信息，不记录密钥或完整请求正文；
- 供应商没有可靠价格元数据时显示“费用未知”，系统不会猜价。

## 能力声明

常用能力包括 `structured_generation`、`chat_analysis`、`embeddings`、`audio_transcription`、`async_audio_transcription`、`segment_timestamps`、`speaker_diarization`、`long_audio` 和 `hotwords`。声明只用于路由与校验，不会凭空给适配器增加能力。

两个本地 ASR 适配器只声明 `audio_transcription` 与 `segment_timestamps`：它们不声明 `long_audio`，因为长录音继续走本地分片流程，才能限制内存并支持失败分片续跑；也不声明 `speaker_diarization` 与 `hotwords`，因为这两项尚未实现。本地 Profile 一律被强制标记为本地（`external=false`），不会触发逐次外发授权。
