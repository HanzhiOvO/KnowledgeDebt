# 数据库迁移

KnowledgeDebt 使用 Alembic 管理 SQLite / PostgreSQL 结构。升级前请备份数据库和资源目录。

```bash
make migrate
```

当前版本链：

| Revision | 作用 |
| --- | --- |
| `20260815_0001_baseline` | v0.1 课程、Session、资源、知识点、学习、验收与 Job 基线 |
| `20260815_0002_automation_workbench` | Provider Profile、学期/课表/Occurrence、Session/资源自动化、转写分片、全局收件箱、统一审核、调用与审计台账 |

开发环境的 `Database` 初始化仍会幂等补齐表结构，便于零配置 SQLite；生产部署应以 Alembic revision 为准。迁移脚本不得删除原始媒体，也不得把 Provider 明文密钥写入数据库。

查看状态与回滚：

```bash
cd backend
../.venv/bin/alembic -c alembic.ini current
../.venv/bin/alembic -c alembic.ini history
../.venv/bin/alembic -c alembic.ini downgrade 20260815_0001_baseline
```

回滚 v0.2 会删除自动化相关表，因此必须先备份；资源文件本身不在迁移中删除，但自动化状态、审核和调用台账会丢失。
