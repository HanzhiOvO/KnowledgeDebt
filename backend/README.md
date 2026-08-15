# FastAPI 后端

该目录包含 KnowledgeDebt 的领域逻辑、HTTP API、证据校验、检索、Provider、StorageProvider、数据库适配与后台 Job。

## 主要目录

- `app/main.py`：FastAPI 路由、鉴权、隐私同意清单与 Job 编排；
- `app/service.py`：课堂还原、验收、补课与掌握度业务流程；
- `app/database.py`：SQLite / PostgreSQL 仓储兼容层；
- `app/providers/`：AI、ASR 与 Embedding Provider；
- `app/retrieval/`：课堂还原 / 从零学习双检索策略；
- `app/storage/`：本地与 S3 兼容存储；
- `alembic/`：版本化数据库迁移；
- `tests/`：单元、集成、迁移与现实规模 E2E。

## 运行与测试

从仓库根目录执行：

```bash
make backend-install
make backend-run
make backend-lint
make backend-test
```

开发默认使用 SQLite；生产部署支持 PostgreSQL 16。数据库变更必须同时提供 Alembic migration 和升级测试。
