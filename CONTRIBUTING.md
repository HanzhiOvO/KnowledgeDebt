# 参与贡献

感谢你帮助改进 KnowledgeDebt。项目默认使用中文进行 Issue、Pull Request 与产品讨论；英文贡献同样欢迎。

[English contribution guide](CONTRIBUTING.en.md)

1. 涉及产品模型、Provider 协议、隐私边界或数据库结构的较大变更，请先创建 Issue 讨论。
2. 从最新 `main` 创建聚焦的分支，并保持提交容易审阅。
3. 使用 `make backend-install` 与 `make web-install` 安装依赖。
4. 创建 Pull Request 前运行 `make verify` 以及相关 Alembic 迁移测试。
5. 不得提交 API Key、访问令牌、课堂录音、私人笔记、数据库或未获授权的版权课程材料。
6. 在 PR 中如实说明实际测试过的浏览器、数据库和部署路径。

所有变更必须保留以下产品不变量：

- Course Session 独立于录音存在；
- 外部资料不能被描述成“老师在课堂上讲过”的证据；
- 阅读内容不会清债，只有达到目标等级的掌握验收可以清债；
- 课堂还原可信度与学习资料完备度是两个独立指标；
- AI 生成题目不得超出已提供课程证据和预期掌握等级。

主产品由 `web/` 与 `backend/` 组成。`legacy/flutter-client/` 是保留的实验性兼容原型；除非提案明确恢复受支持的原生范围，新产品功能不应依赖 Flutter。

数据库变更必须同步更新 `backend/app/orm.py`、新增 Alembic revision、保护已有 SQLite 数据，并包含升级测试。外部 Provider 相关变更必须同步更新同意清单，并证明上传不会触发未经授权的数据外传。
