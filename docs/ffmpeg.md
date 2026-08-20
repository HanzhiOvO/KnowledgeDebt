# FFmpeg 与长录音处理

原始媒体始终先保存。短音频且格式可由 ASR 直接接受时，不强制依赖 FFmpeg；需要规范化、转码或分片的长音频才会调用它。

## 安装

macOS（Homebrew）：

```bash
brew install ffmpeg
ffmpeg -version
ffprobe -version
```

Ubuntu / Debian：

```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

也可以将 `KNOWLEDGEDEBT_FFMPEG_PATH` 指向自定义可执行文件。

## 处理与恢复

- 默认分片为 1500 秒，可用 `KNOWLEDGEDEBT_TRANSCRIPTION_CHUNK_SECONDS` 调整；
- 规范化输出为单声道 16 kHz FLAC；
- 每个分片的范围、状态、尝试次数、错误和片段均持久化；
- 成功分片不会在重试时重复发送，失败分片可从原媒体直接续跑；
- 每段转写会加回分片偏移，再叠加录音在课堂中的偏移，形成全课堂时间戳；
- 进程重启后，未完成 Job 会恢复为排队并接管；
- FFmpeg 缺失或处理失败时，原始文件不删除，资源会保留明确诊断并允许重试。

仓库不会内置或自动下载 FFmpeg 二进制。一键启动脚本会检测并给出提示，但不会用未经用户同意的系统安装操作修改电脑。
