# KnowledgeDebt 第三方组件说明

原生安装包把以下运行组件作为独立程序或依赖随包分发：

- Node.js 24.19.0：MIT License，来自 Node.js 官方 Darwin arm64 发行包，并使用官方 `SHASUMS256.txt` 校验。
- FFmpeg 9.0.1：本构建脚本从 FFmpeg 官方源码归档编译，不启用 `--enable-gpl` 或 `--enable-nonfree`，按 LGPL 2.1-or-later 使用；对应源码归档与构建配置随发布产物保存。
- whisper.cpp b4938（源码版本 1.9.3）：MIT License；从固定 GitHub 标签源码编译为静态 Apple Silicon CLI，Metal 库嵌入可执行文件，不依赖 Homebrew。对应源码归档与哈希随发布产物保存。
- Python 3.12 运行环境与 FastAPI、Uvicorn、Pydantic、SQLAlchemy、Alembic、Cryptography、PyMuPDF、python-pptx、pypdf、HTTPX 等依赖：由 PyInstaller 冻结；详细名称、版本和声明许可证由构建脚本生成到 `PYTHON_PACKAGES.txt`。
- Next.js、React、React DOM、Sharp 与 standalone 运行依赖：详细名称、版本和声明许可证由构建脚本生成到 `NODE_PACKAGES.txt`。

KnowledgeDebt 自身使用 MIT License。各组件仍分别受其原始许可证约束；本文件不是法律意见。正式发布时应将 FFmpeg 与 whisper.cpp 源码归档和安装包放在同一下载位置，并保留 `LICENSES` 目录。
