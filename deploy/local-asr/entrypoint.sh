#!/bin/sh
# 缺少模型时立刻失败并给出可执行提示，不静默启动一个无法转写的服务。
set -eu

if [ ! -f "${WHISPER_MODEL}" ]; then
  echo "缺少模型文件 ${WHISPER_MODEL}：请先把 ggml 模型放入挂载的 /models 卷（见 deploy/local-asr/README.md）。" >&2
  exit 1
fi

exec whisper-server \
  --host 0.0.0.0 \
  --port 8080 \
  -m "${WHISPER_MODEL}" \
  -l "${WHISPER_LANGUAGE}" \
  -t "${WHISPER_THREADS}"
