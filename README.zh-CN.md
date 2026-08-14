# KnowledgeDebt

> **课堂可以缺席，知识不能欠账。**

KnowledgeDebt 是一个面向大学生的开源、Local-first 补课应用。它管理“已经发生、但尚未真正掌握”的课程，通过不同可信等级的资料还原课堂、生成从零学习路径，再以课程资料为边界进行掌握验收。只有达到课程要求的掌握等级，知识债务才会清零。

它不是“录音 → 转写 → 摘要”工具。Course Session 表示一次真实课堂，录音、PPT、教材、作业和外部链接都只是可选资源；即使用户缺席且没有任何资料，Session 也合法存在。

## 核心闭环

```text
Course Session → 收集资料 → 课堂还原 → Knowledge Point
→ 从零学习路径 → 资料内验收 → 识别缺口
→ 针对性补课 → 重新验收 → 债务清零 → Session 完成
```

## MVP 已实现

- 单一 Flutter 代码库与 Android、iOS、macOS、Windows 工程
- 债务优先首页、课程与 Session、资料、课堂还原、学习路径、债务、验收、录音、设置页面
- 每门课程可编辑 Course Profile 权重
- 无资料 / 无录音 Session 的合法空状态
- 本地录音、暂停、继续、停止、退出保护与五分钟安全分段
- 音视频、PDF、PPTX、文本、教材、作业、大纲等资料管理
- 资料覆盖率、质量和本节相关度输入
- SQLite 结构化实体：转写片段、还原、知识点、债务、学习步骤、题目、作答、针对性补课
- 相互独立的课堂还原度与学习资料完备度
- 可替换 `AIProvider` / `TranscriptionProvider`
- 实际可配置的 OpenAI-compatible AI 与 ASR Provider
- 每次 AI / ASR 外传前必须明确确认
- 0–4 掌握等级、目标等级判断、语义评价、针对性补课、重新验收、自动完成 Session
- Flutter / Python 测试、lint 与 GitHub Actions

## 快速开始

需要 Flutter stable、Python 3.12，以及目标平台自己的构建工具链。

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cp .env.example .env
# 编辑 .env
make backend-run
```

另开终端：

```bash
cd client
flutter pub get
flutter run
```

桌面端 / iOS 默认访问 `http://127.0.0.1:8123`；Android 模拟器默认访问 `http://10.0.2.2:8123`。真机请在设置中改成局域网可访问地址。

## AI / ASR

在 `.env` 中设置：

```dotenv
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
KNOWLEDGEDEBT_AI_MODEL=gpt-5-mini
KNOWLEDGEDEBT_ASR_MODEL=gpt-4o-mini-transcribe
```

API Key 只保存在后端环境，不会返回客户端。生成题目时优先依据官方课程资料与课堂证据，并限制在 Knowledge Point 的目标掌握等级内；开放题按 rubric 和语义评价，不使用纯字符串匹配。

## 隐私

录制课堂前请遵守所在地法律、学校规定和课堂隐私要求，并在必要时获得授权。

录音首先保存在本机；添加到 Session 时会说明后端目的地；发送给 AI / ASR 前还有单独确认。`.env`、数据库和常见媒体文件均已加入 `.gitignore`。敏感资料建议使用自托管后端。

## 验证

```bash
make verify
```

集成测试真实覆盖 Course → Session → Resource → Knowledge Point → Remediation → Question → Mastery → Session complete。

## 路线图

- **已完成：** 可用的知识债务闭环、结构化数据、Provider 抽象、隐私确认、自动化测试、四端工程骨架与 CI。
- **进行中：** 真机 UX 验证、录音时间轴跳转、资料页码预览、各系统版本下更稳健的后台录音。
- **计划中：** 本地 Whisper / LLM、自动学习 Course Profile、依赖感知的最低学习量、同步、无障碍与本地化完善。

## Vibe Coding 声明

KnowledgeDebt 是一个通过 Vibe Coding 工作流构建的开源实验项目。产品愿景、需求设计、架构讨论以及大量开发工作由人类创作者与 AI Coding Agent 协作完成。项目强调 AI-assisted development + human-directed product design，并公开 AI 的参与。

项目 Owner：**HanzhiOvO**

## License

[MIT](LICENSE)

