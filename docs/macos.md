# macOS 安装、升级与数据管理

KnowledgeDebt v0.2 的普通用户版本面向 **macOS 13 及以上、Apple Silicon（M1/M2/M3/M4）**。安装包已经包含应用所需的 Python、Node.js、FFmpeg、ffprobe 和 whisper.cpp 命令行运行时；无需安装 Docker、Homebrew、数据库或开发工具。Intel Mac 和 Windows 暂无原生安装包。

## 安装

1. 下载 `KnowledgeDebt-0.2.0-arm64.dmg` 和同目录的 `SHA256SUMS.txt`。
2. 可选但推荐：在终端进入下载目录并执行 `shasum -a 256 -c SHA256SUMS.txt`。
3. 双击 DMG，把 `KnowledgeDebt.app` 拖到“应用程序”。
4. 打开应用。应用会在菜单栏显示 `KD`，本地服务就绪后自动打开浏览器。

当前可复现构建默认使用 ad-hoc 本地签名，尚未 notarize。若 macOS 首次阻止打开，请在 Finder 中右键应用并选择“打开”，核对来源后再次确认。正式发布是否具有 Developer ID 签名，以交付目录中的 `SIGNING_STATUS.txt` 为准。

应用只监听随机分配的 `127.0.0.1` 本地端口。菜单栏可重新打开工作台、查看脱敏诊断日志或完整退出应用；关闭浏览器标签页不会停止本地服务。

## 第一次使用本地转写

安装包只包含 whisper.cpp 运行时，**不预装大型模型，也不会静默下载**：

1. 打开“设置 → 本地转写模型”。
2. 查看下载大小、磁盘占用、速度、准确率、语言和适用场景。
3. 主动确认下载；一般课堂推荐 `Whisper Medium Q5`。
4. 下载完成并通过固定 SHA-256 校验后，点击“设为当前模型”。

下载可取消，未完成分片会保留供下次断点续传；应用重启后可继续。模型损坏会被识别为异常，可重新下载。正在使用的模型必须先切换，才能删除。没有模型或 Provider 时，录音仍会先保存并进入“等待配置转写”，不会擅自外发。

## 数据位置

所有原生应用数据位于：

```text
~/Library/Application Support/KnowledgeDebt/
```

其中包括数据库、原始资源、浏览器录音分片、转写分片、模型、日志、运行状态和升级前数据库备份。模型保存在 `models/`，诊断日志保存在 `logs/`，数据库备份保存在 `backups/`。删除或替换 `.app` 不会删除这些数据。

Provider 加密主密钥保存在 macOS 钥匙串，服务名为 `io.github.hanzhiovo.KnowledgeDebt`；供应商密钥不会写入普通日志。

## 升级

1. 从菜单栏选择“退出 KnowledgeDebt”。
2. 先按下节方法制作完整备份。
3. 用新版 `KnowledgeDebt.app` 替换“应用程序”中的旧版本，再正常打开。

首次启动目标版本前，应用会使用 SQLite 在线备份 API，在 `backups/` 中创建一次一致的 `knowledgedebt-before-<版本>.sqlite3`，然后幂等升级数据库。该自动备份只保护数据库；录音、资料和模型仍需通过完整目录备份保护。

## 备份与恢复

完整备份：退出应用后，把整个 `~/Library/Application Support/KnowledgeDebt/` 目录复制到另一块磁盘或受保护的位置。只复制 SQLite 文件不会包含录音、课件和模型。

恢复到同一台 Mac：

1. 退出应用。
2. 保留当前数据目录作为第二份副本。
3. 将完整备份目录放回原路径。
4. 打开应用，确认课程、录音、模型和 Provider 状态。

跨 Mac 恢复时，课程与资源可随目录迁移；钥匙串密钥不会随普通文件复制，需要重新配置 Provider 密钥。不要在应用运行时直接覆盖数据目录。

## 卸载

仅卸载应用：退出后把 `/Applications/KnowledgeDebt.app` 移到废纸篓。课程和录音仍保留。

彻底卸载需要用户主动执行以下两项不可逆操作：

1. 删除 `~/Library/Application Support/KnowledgeDebt/`；
2. 在“钥匙串访问”中删除服务名为 `io.github.hanzhiovo.KnowledgeDebt` 的项目。

操作前请确认已经制作并验证备份。KnowledgeDebt 不会在删除应用本体时自动清除用户数据。

## 已知限制

- 当前交付包为 Apple Silicon 架构，不支持 Intel Mac；
- 未提供应用内自动更新；升级使用替换 `.app` 的方式；
- 无 Developer ID 凭证的构建未 notarize，首次打开需要用户确认；
- 大型模型下载依赖 Hugging Face 固定版本的官方 HTTPS 文件；受限网络下可取消后重试；
- 一台未安装开发环境的独立干净 Mac 仍应按发布验收清单复验，构建机冒烟不能替代正式发布验收。
