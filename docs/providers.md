# Provider Profile 与外部数据边界

v0.2 将 AI 分析、语音转写（ASR）和向量检索（Embedding）拆成三组独立默认路由。一个 Profile 只声明后端真实实现并经过配置的能力；能力未声明时，界面不会把它作为对应默认项。

## 当前真实实现状态

| 适配器 | 状态 | 说明 |
| --- | --- | --- |
| 本地规则引擎（`local_rule`） | 已测试 · 默认零配置路由 | 完全离线，只整理真实资料，结果明确标为推断，不冒充语义大模型 |
| OpenAI-compatible | 可用 | 支持实际兼容端点；具体模型能力由用户声明并自行验证 |
| Local Hash Embedding | 可用 | 本地确定性检索，不外发文本，不代表语义模型质量 |
| 本地 whisper.cpp（`local_whisper_cpp`） | 已实现 · 原生包内置运行时 | 调用本机可执行文件转写，解析 JSON 分段时间戳，超时与取消会真正终止子进程；原生应用可在设置中校验并管理模型，源码/服务器部署需自行安装运行时；见 [local-asr.md](local-asr.md) |
| 本地 / 私网 ASR 服务（`local_openai_asr`） | 已实现 · 需自行部署服务 | 只允许私网 Base URL，公网地址被拒绝；具体服务需操作者验证 |
| DeepSeek / OpenCode / Kimi / GLM / MiniMax / DashScope 兼容预设 | 未实测预设 | 复用兼容协议，不宣称供应商全部模型均已验证 |
| Anthropic / Gemini 原生协议 | 接口槽位 | 尚未实现，不显示为可用路由 |
| 腾讯云 / Google / DashScope 原生 ASR | 接口槽位 | 尚未实现，不显示为可用路由 |

仓库测试默认只使用本地假 Provider，不会调用付费 API。

首次启动且没有可用外部密钥时，系统会把本地规则引擎设为 AI 默认路由。设置页为 OpenAI、DeepSeek 和 OpenCode Zen 提供快速填充按钮，但仍保留完整 Profile 流程：先加密保存或引用环境变量，再真实测试连接，最后由用户选择是否设为默认。Anthropic 原生适配器仍是禁用接口槽位，因此不会出现在快速可用列表中。

“测试连接”只报告实际结果：OpenAI-compatible 会检查鉴权、精确模型 ID，并按 Profile 声明发送最小文本、向量或 0.5 秒静音 WAV 请求；界面会在外部测试前再次确认，并提示可能产生极少量费用。本地 whisper.cpp 会检查可执行文件、FFmpeg 和当前模型；私网 ASR 会检查受限地址和服务响应。尚未验证的供应商预设不会被描述为正式可用，也不会在失败时静默切换到收费或外部 Provider。

连接测试结果只对测试时的配置有效。修改 Base URL、凭据、模型、能力、请求头、启用状态或本地/外部属性后，旧结果会立即清空，必须重新测试；只修改显示名称不会使结果失效。请求字段校验使用脱敏中文错误，不回显用户提交的值。

自定义 OpenAI-compatible Profile 可以填写 JSON 格式的可选请求头，适合组织、项目或租户标识，最多 20 项。`Authorization`、`Cookie`、`X-API-Key` 等敏感或会干扰 HTTP 的请求头会被拒绝；认证信息必须使用加密密钥字段或 `env:` 引用，避免普通 Profile API 返回明文凭据。

外部 OpenAI-compatible Profile 的 Base URL 必须是无用户名、密码、查询参数和片段的 HTTPS 地址。HTTP 只保留给受限的本地/私网适配器，避免误把凭据和课程内容发往不安全的公网连接。

本地/私网 ASR 地址同样拒绝 URL 用户名、密码、查询参数和片段。健康检查和转写失败只保留必要的中文原因与 HTTP 状态，不把远端响应正文或完整地址写入 API 错误。

## 密钥

推荐在 `.env` 保存供应商密钥，然后在 Profile 中填写 `env:变量名`。如果要从设置页输入并持久化密钥，后端要求 Fernet 主密钥：

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把输出放入本机 `.env` 的 `KNOWLEDGEDEBT_ENCRYPTION_KEY`。没有主密钥时，API 会拒绝明文密钥入库。项目不会用普通 JSON 文件保存明文密钥；主密钥和供应商密钥都不得提交到 Git。

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
