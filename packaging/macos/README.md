# macOS Apple Silicon 原生构建

构建目标是一个无需用户安装 Python、Node.js、FFmpeg、whisper.cpp、Docker 或 PostgreSQL 的 `KnowledgeDebt.app` 和 `.dmg`。应用双击后以菜单栏进程运行，只监听 `127.0.0.1`，等待后端与 Web 健康检查成功后打开默认浏览器。模型权重不随安装包分发，由用户在设置中确认后下载到数据目录。

## 构建

构建机需要 macOS Apple Silicon、Xcode Command Line Tools、项目 `.venv` 和 npm。普通使用者不需要这些工具。

```bash
make native-macos
```

脚本会：

1. 从 Node.js、FFmpeg、CMake 与 whisper.cpp 官方地址下载固定版本并校验 SHA-256；
2. 以不启用 GPL/nonfree 组件的配置从源码编译 FFmpeg/ffprobe，并静态编译内置 whisper.cpp Apple Silicon CLI；
3. 在系统临时构建目录维护按 requirements 哈希复用的独立 Python 打包环境，再用 PyInstaller 冻结 FastAPI 与 Python 运行时，避免同步目录的小文件延迟影响构建；
4. 构建 Next.js standalone 并放入官方 Node.js arm64 运行时；
5. 编译 Swift 菜单栏启动器；
6. 执行无系统 Python/Node/FFmpeg/whisper.cpp 参与的应用内运行时冒烟；
7. 拒绝包含构建用户主目录、仓库目录或临时构建目录绝对路径的应用包；
8. 生成 `.app`、`.dmg`、zip、源码归档、许可证清单和 `SHA256SUMS.txt`。

默认产物位于 `dist/macos/`。没有 Developer ID 时只做 ad-hoc 签名，并在 `SIGNING_STATUS.txt` 中明确说明未 notarize；不会伪造签名状态。

FFmpeg 使用稳定安装前缀和 `DESTDIR` 分阶段安装；whisper.cpp 编译使用文件/调试路径映射。两者只链接系统库，不依赖 Homebrew 或开发机目录。构建结束后仍会扫描整个 `.app`，任何命中的开发者绝对路径都会让构建失败。

## 运行与数据

应用数据保存在：

`~/Library/Application Support/KnowledgeDebt/`

升级前，启动器内的后端会为每个目标版本创建一次一致的 SQLite 备份。删除 `.app` 不会删除课程、录音、模型或备份。

备份：退出应用后复制整个 `KnowledgeDebt` 数据目录。恢复：退出应用，将备份目录复制回原位置后重新打开。彻底卸载：先删除应用，再由用户主动删除数据目录和钥匙串中服务名为 `io.github.hanzhiovo.KnowledgeDebt` 的项目。

普通用户的完整操作手册见 [`docs/macos.md`](../../docs/macos.md)。

## 正式签名

```bash
KD_CODESIGN_IDENTITY='Developer ID Application: Example (TEAMID)' make native-macos
```

代码签名不等于 notarization。正式公开发布仍需使用 Apple 开发者账号运行 `notarytool submit`、等待成功并 staple；构建脚本不会在缺少凭证时声称已经完成。
