# v0.2 发布与验收记录

记录日期：2026-08-23。目标分支：`agent/v0-2-automated-course-workbench`。本候选已合并远端 `main` 的 `6f45a72`，包含其后的中文品牌、零配置本地规则引擎、来源预览、Provider 快速配置与 Windows 一键启动修复。

## 版本摘要

v0.2 把 KnowledgeDebt 从手动整理原型扩展为本地优先的自动化课程工作台：权威课表快照、惰性 Session、浏览器增量录音、课程链接和来源预览、默认自动转写、分片续跑、统一审核、自动归档与课堂标题更新、Provider Profile、本地模型管理，以及 Apple Silicon 原生应用打包。本轮又补齐了低配 CPU 路径：FLAC 直读、保守静音跳过、CPU 安全线程策略和 Large v3 Turbo Q5 课堂推荐档。

首页和用户文档默认使用中文。普通用户优先使用 macOS DMG；源码一键启动和 Docker Compose 保留给开发、自托管和服务器部署。

## 构建产物

`make native-macos` 会在 `dist/macos/` 生成：

- `KnowledgeDebt-0.2.0-arm64.dmg`；
- `KnowledgeDebt-0.2.0-arm64.zip`；
- FFmpeg 9.0.1 与 whisper.cpp b4938 对应源码归档；
- `SHA256SUMS.txt`、`SIGNING_STATUS.txt` 和应用内第三方许可证清单。

构建固定并校验 Node.js 24.19.0、FFmpeg 9.0.1、CMake 4.1.0（仅构建使用）、whisper.cpp b4938 / CLI 1.9.3 和 Silero VAD 6.2.0。FFmpeg 与 whisper.cpp 为 arm64，未链接 Homebrew 或用户目录动态库；安装包会拒绝开发者主目录、仓库目录和临时构建路径。包内只预装不到 1 MB 的静音检测权重；大型转写模型仍需用户确认后下载。

最终校验值以交付目录中的 `SHA256SUMS.txt` 为唯一依据，不应从本文手工复制旧值。

## 已执行的自动化验证

发布前需要重新执行并记录：

```bash
make verify
make native-macos
cd dist/macos && shasum -a 256 -c SHA256SUMS.txt
```

2026-08-23 当前候选已实际执行：

- `make verify`：Ruff 通过；Pytest `136 passed, 1 skipped`，唯一跳过项为本机没有可用 PostgreSQL；Web ESLint、TypeScript 与 Next.js 16.3.1 production build 通过；
- macOS/Linux `start.sh --skip-install --no-browser` 实际启动通过：API 与 Web 均返回 HTTP 200，只监听 `127.0.0.1`，按 `Ctrl+C` 后两个端口均释放；
- Windows `start.ps1` 与 `start.bat` 已补齐同等启动流程并限制回环监听；当前构建机没有 Windows/PowerShell，仍需在 Windows 10/11 实机复验；
- 本地模型取消/断点续传竞态回归额外连续执行 20 次，均进入稳定的 `cancelled` 终态并可从精确偏移继续；
- 全新 SQLite 从基线升级到 `20260821_0006`，重复执行 `upgrade head` 无变更；Provider 自定义请求头列、转写唯一索引和模型下载索引均存在；
- 真实 60 秒 STM32 课程样本、强制纯 CPU：Large v3 Turbo Q5 保守 VAD 开启前后文本逐句一致，耗时从 70.5 秒降到 61.3 秒；Small Q5 仅用 21.9 秒但术语明显错，因此不会为速度自动降模型；三分之二静音样本上 VAD 节省约 47% CPU 耗时并消除静音幻觉；
- `make native-macos`：应用内 API、Web、录音落盘、FFmpeg、whisper.cpp、VAD 哈希与运行时冒烟通过，DMG `hdiutil verify` 通过；本行以同日重新构建成功及交付目录 SHA 清单为准；
- 独立解压 ZIP 与只读挂载 DMG：两份 `.app` 均通过 `codesign --verify --deep --strict` 与 `plutil`；启动器、FFmpeg、ffprobe、whisper-cli 均为 arm64；whisper-cli 只链接 macOS 系统框架；
- SHA256 清单复核通过；应用大小约 302 MB；未预装模型权重，未发现数据库、真实 `.env`、Finder 元数据或开发者绝对路径；排除固定官方二进制与签名清单的项目文本疑似凭据扫描为零。

Provider 连接测试覆盖鉴权、精确模型 ID、声明的文本/向量/语音能力、最小合成请求、请求计数和中文错误。自定义 OpenAI-compatible Profile 可带非敏感请求头；凭据头和 CRLF 注入会被拒绝。修改地址、凭据、模型、能力或请求头后，旧的连接测试结果会立即失效；请求校验与本地 ASR 错误不会回显提交的密钥、远端响应正文或带凭据的地址。

媒体上传是否自动转写统一服从“设置 → 应用偏好”，不再由上传表单暗中覆盖全局值。转写完成后，置信度足够的主题会自动生成“课程-日期-主题”标题；短或模糊主题进入待审核，用户手动锁定的标题永远不会被后续自动化覆盖，Session 页面可直接修改并锁定标题。

依赖中固定 SHA-256 的官方 Node.js 二进制包含上游 AWS 示例访问标识字符串，安全扫描将该单一官方文件按已验证上游制品处理；项目源码、配置、其余应用文件和用户数据没有此例外。

## 22 项干净 Mac 验收状态

下表区分“已有自动证据”和“必须在另一台干净 Mac 人工复验”。构建机通过不等于独立发布环境通过。

| # | 验收项 | 当前证据 | 发布状态 |
| ---: | --- | --- | --- |
| 1 | DMG 安装 | DMG 校验、只读挂载、Applications 链接通过 | 待干净 Mac 拖拽安装 |
| 2 | 双击应用启动 | Swift 启动器编译、签名结构和组件检查通过 | 待干净 Mac 双击 |
| 3 | 前后端健康 | 应用内运行时冒烟通过 | 已有自动证据 |
| 4 | SQLite 初始化/迁移 | 全新 SQLite 升级到 head，并重复升级通过 | 已有自动证据 |
| 5 | 导入示例课表 | fixture 解析、预览、权威快照测试通过 | 待干净 Mac UI 复验 |
| 6 | 浙江工商大学连接/尝试 | UI 和安全连接器边界可用；实时登录禁用 | 受授权会话/HAR 阻塞 |
| 7 | 创建课程和 Session | API 与原生冒烟通过 | 已有自动证据 |
| 8 | 初始标题格式 | 自动化测试通过 | 已有自动证据 |
| 9 | 上传音频 | API 集成测试与原生录音入库冒烟通过 | 待干净 Mac UI 复验 |
| 10 | 开始/停止/恢复浏览器录音 | IndexedDB、分片幂等、恢复与失败测试通过 | 待真实麦克风权限复验 |
| 11 | 内置 FFmpeg 处理长音频 | 固定 FFmpeg、媒体探测/分片测试与 FLAC 冒烟通过 | 待真实整节课录音复验 |
| 12 | 模型下载/校验/使用/删除 | Range、取消、续传、重启、SHA、损坏、切换/删除测试通过 | 待干净 Mac 下载真实权重并推理 |
| 13 | 外部 API 连接测试 | 鉴权/模型/文本/向量/静音 ASR 最小请求及自定义请求头 mock 测试通过 | 待获授权的真实厂商账号 |
| 14 | 默认自动转写 | 默认设置与等待配置状态测试通过 | 待干净 Mac 真实模型复验 |
| 15 | 自动主题与标题更新 | 假 Provider 集成测试通过 | 待真实模型质量复验 |
| 16 | 手动标题不被覆盖 | 标题锁与回归测试通过 | 已有自动证据 |
| 17 | 退出后子进程停止 | 启动器停止逻辑与后端 shutdown 测试覆盖 | 待双击应用进程表复验 |
| 18 | 异常退出后恢复 | 录音、转写任务和模型下载恢复测试通过 | 待强制退出整应用复验 |
| 19 | 重启保留历史 | 持久化数据库/资源测试通过 | 待干净 Mac 重启复验 |
| 20 | 升级不丢数据库/录音/模型 | 版本前 SQLite 一致备份与数据目录隔离测试通过 | 待旧包→新包实机复验 |
| 21 | 日志无敏感信息 | 项目与安装包静态扫描通过 | 待真实账号操作后复查日志 |
| 22 | 包内无开发路径/账号/密钥 | 构建拒绝门槛、ZIP 和 DMG 独立扫描通过 | 已有自动证据 |

因此，当前可以交付**可复现的候选构建**，但不能声称“另一台完全干净的 Apple Silicon Mac 22/22 已通过”。

## 外部阻塞与最短复验步骤

### 浙江工商大学实时教务

阻塞条件：没有用户明确授权的已登录会话、脱敏 HAR 或测试账号；系统可能包含 SSO、验证码与反爬限制。项目不会猜接口或绕过验证。

最短步骤：用户在自己的浏览器正常登录，按 [zjsu-schedule.md](zjsu-schedule.md) 提供脱敏 HAR/字段样本；开发者实现并复核登录过期、学期发现、单双周、停调补课和快照差异，再用真实账号在本机确认。

### Developer ID 与 notarization

阻塞条件：构建环境没有 Apple Developer ID 证书和 notarization 凭证。当前包只有 ad-hoc 签名，`SIGNING_STATUS.txt` 会如实说明。

最短步骤：提供有效证书后设置 `KD_CODESIGN_IDENTITY` 构建，使用 `notarytool submit --wait` 提交，成功后 staple，并在干净 Mac 验证 Gatekeeper。

### 真实厂商 Provider

阻塞条件：没有可用于测试的授权账户、配额和明确模型 ID。OpenAI-compatible 合同与 mock 已验证，兼容预设不代表每个厂商模型都正式支持。

最短步骤：在“设置 → Provider”创建独立 Profile，配置服务端密钥引用，点击“测试连接”，再用一份非敏感短音频/短文本执行实际任务并核对调用台账与费用。

### 独立干净 Mac

阻塞条件：当前只有构建机，无法证明另一台无全局开发工具的 macOS 环境。

最短步骤：按上表 1–22 顺序执行，至少使用一节真实长录音和一个主动下载的本地模型；保存系统版本、芯片、安装包 SHA、每步结果和脱敏日志。

## 已知限制

- 只提供 macOS 13+ Apple Silicon 候选包；Intel、Windows 和 Linux 桌面包属于 P2；
- 浙江工商大学实时抓取保持 fixture-only，不冒充已连接；
- Anthropic、Gemini 和部分原生云 ASR 仍是接口槽位；DeepSeek、OpenCode、Kimi、GLM、MiniMax、DashScope 为未实测兼容预设；零配置本地规则引擎已测试，但不代表语义大模型质量；
- Windows 一键启动脚本尚未在 Windows 实机运行；
- 本地 Hash Embedding 可用但不代表通用语义模型质量；
- 当前长任务工作器在 API 进程内，只支持单后端实例；
- 没有应用内自动更新；公开发布仍需 Developer ID 和 notarization；
- 尚未在独立干净 Mac 完成真实麦克风、90 分钟课堂、真实模型权重和真实外部账号的端到端验收；
- 应用内浏览器策略阻止了本轮 localhost 视觉自动化，响应式布局仍需在干净 Mac 上以桌面和手机宽度人工复核。
