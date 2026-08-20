# KnowledgeDebt 中文文档

| 文档 | 内容 |
| --- | --- |
| [Provider 与密钥](providers.md) | Profile、能力声明、真实支持状态、密钥与调用台账 |
| [浙江工商大学教务连接器](zjsu-schedule.md) | 已实现边界、fixture 格式、实时连接前置条件 |
| [FFmpeg 与长录音](ffmpeg.md) | 安装、分片、续跑、时间戳与故障恢复 |
| [本地 ASR](local-asr.md) | whisper.cpp 与私网 ASR 服务、无 GPU 选型、超时与取消语义 |
| [隐私模型](privacy.md) | Local-first、逐次授权与录制合规 |
| [部署指南](deployment.md) | 本地与 Docker Compose 部署 |
| [数据库迁移](migrations.md) | Alembic、SQLite / PostgreSQL 迁移与回滚 |
| [Web-first 架构决策](architecture/0001-web-first-thin-backend.md) | 主客户端与薄后端的架构理由 |

脱敏教务样例位于 [`fixtures/zjsu-schedule.example.json`](fixtures/zjsu-schedule.example.json)。所有示例均为虚构数据，不包含真实账号、Cookie、学生或教师信息。
