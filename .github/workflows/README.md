# 持续集成工作流

`ci.yml` 会在 Pull Request 和 `main` 分支推送时验证：

- Next.js ESLint、TypeScript 与生产构建；
- FastAPI Ruff、Pytest、Alembic 与 PostgreSQL 16；
- 保留的 Legacy Flutter analyze 与 test。

修改工作流后，请在 PR 中等待全部检查完成，不要只依据本地结果判断成功。
