# KnowledgeDebt Web 工作台

这里是 Next.js 16 / React 19 主客户端。v0.2 默认提供五个工作区：**总览、课表、课程、待审核、设置**。

从仓库根目录一键启动：

```bash
./start.sh
```

只启动 Web：

```bash
make web-install
make web-run
```

生产构建验证：

```bash
make web-test
```

浏览器只访问同源 `/api/backend` 代理；Provider 密钥和可选 API 访问令牌不会下发到客户端。媒体上传和浏览器录音遵循“原文件先保存、后台再自动化”，外部转写必须逐次确认。

主要目录：

```text
src/app/                  路由、加载与错误边界
src/features/home/        总览与自动化队列
src/features/schedule/    周课表与 fixture 导入
src/features/review/      统一待审核与全局收件箱
src/features/sessions/    Session、录音转写、学习与验收
src/features/settings/    Provider、教务、隐私与用量
```
