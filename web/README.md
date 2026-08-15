# Web 主客户端

这里是 KnowledgeDebt 的主要产品界面，使用 Next.js 16、React 19 与 TypeScript 构建。

## 本地运行

从仓库根目录运行：

```bash
make web-install
make web-run
```

打开 `http://localhost:3000`。浏览器请求默认通过同源 `/api/backend` 代理到 `http://127.0.0.1:8123`，API Key 与可选访问令牌只保留在服务端。

## 验证

```bash
make web-test
```

该命令依次执行 ESLint、TypeScript 检查与 Next.js 生产构建。页面与组件位于 `src/app/` 和 `src/features/`；领域类型位于 `src/types/`。

## 部署

生产构建启用了 Next.js standalone 输出。仓库根目录的 `compose.yaml` 会同时启动 Web、FastAPI 与 PostgreSQL。完整说明见 [`../docs/deployment.md`](../docs/deployment.md)。
