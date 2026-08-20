# KnowledgeDebt 后端

这里是 FastAPI 薄后端，负责课程与 Session 领域模型、证据校验、课表/Occurrence 自动化、录音转写编排、统一审核、Provider 路由、调用台账、存储和数据库迁移。

从仓库根目录启动：

```bash
./start.sh
```

只启动后端：

```bash
make backend-install
make backend-run
```

验证：

```bash
make backend-lint
make backend-test
make migrate
```

开发默认使用 `data/knowledgedebt.sqlite3` 与本地资源目录。测试 Provider 均为本地替身，不调用付费 API。Provider、密钥、ASR 与 FFmpeg 配置见 [`../docs/providers.md`](../docs/providers.md)、[`../docs/local-asr.md`](../docs/local-asr.md) 和 [`../docs/ffmpeg.md`](../docs/ffmpeg.md)。

主要目录：

```text
app/automation.py          v0.2 自动化仓储与幂等操作
app/transcription.py       保存后转写、分片、重试与重启接管
app/provider_registry.py   Profile、能力与真实适配器注册
app/providers/local_asr.py 本地 whisper.cpp 与私网 ASR 服务适配器
app/schedule/              教务连接器边界与 fixture 解析
alembic/                   数据库迁移
scripts/                   真机运维脚本（本地 ASR 速度/质量 smoke test）
tests/                     单元、集成、现实规模与自动化回归
```
