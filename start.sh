#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
API_PID=""
WEB_PID=""

info() { printf '\033[1;32m[KnowledgeDebt]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$1"; }
fail() { printf '\033[1;31m[启动失败]\033[0m %s\n' "$1" >&2; exit 1; }

cleanup() {
  trap - INT TERM EXIT
  [ -z "$API_PID" ] || kill "$API_PID" 2>/dev/null || true
  [ -z "$WEB_PID" ] || kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

command -v node >/dev/null 2>&1 || fail "未找到 Node.js，请先安装 Node.js 24 或更高版本。"
command -v npm >/dev/null 2>&1 || fail "未找到 npm。"

node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' \
  || fail "Node.js 版本过低，需要 20 或更高版本（推荐 24）。"

cd "$PROJECT_ROOT"
if [ ! -f .env ]; then
  cp .env.example .env
  info "已从 .env.example 创建本地 .env（默认不调用付费 API）。"
fi

PYTHON_BOOTSTRAP=""
if [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  PYTHON_BOOTSTRAP="$VENV_DIR/bin/python"
else
  for CANDIDATE in python3.13 python3.12 python3; do
    if command -v "$CANDIDATE" >/dev/null 2>&1 \
      && "$CANDIDATE" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
      PYTHON_BOOTSTRAP="$(command -v "$CANDIDATE")"
      break
    fi
  done
fi
[ -n "$PYTHON_BOOTSTRAP" ] || fail "未找到 Python 3.12 或更高版本。"

if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  info "正在创建 Python 虚拟环境…"
  "$PYTHON_BOOTSTRAP" -m venv "$VENV_DIR"
fi

PY_DEPS_SIGNATURE="$(cksum backend/requirements.txt backend/requirements-dev.txt | cksum | awk '{print $1}')"
PY_DEPS_MARKER="$VENV_DIR/.knowledgedebt-deps-$PY_DEPS_SIGNATURE"
if [ ! -f "$PY_DEPS_MARKER" ]; then
  info "正在安装或更新后端依赖…"
  "$VENV_DIR/bin/pip" install -r backend/requirements-dev.txt
  touch "$PY_DEPS_MARKER"
fi

if [ ! -d web/node_modules ] \
  || [ web/package-lock.json -nt web/node_modules ] \
  || ! node -e 'const fs=require("node:fs"); const data=fs.readFileSync("web/node_modules/next/dist/bin/next"); process.exit(data.length > 0 ? 0 : 1)' 2>/dev/null; then
  info "正在安装或更新 Web 依赖…"
  (cd web && npm ci)
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "未找到 FFmpeg：短音频仍可由兼容 ASR 处理；长录音规范化/切分前请按 docs/ffmpeg.md 安装。"
fi

# 只提示，不安装系统软件，也不下载模型。
if [ -n "${KNOWLEDGEDEBT_LOCAL_ASR_MODEL:-}" ] && ! command -v "${KNOWLEDGEDEBT_LOCAL_ASR_BINARY:-whisper-cli}" >/dev/null 2>&1; then
  warn "已配置本地 ASR 模型，但未找到 ${KNOWLEDGEDEBT_LOCAL_ASR_BINARY:-whisper-cli}：请按 docs/local-asr.md 安装 whisper.cpp。"
fi

info "正在启动本地 API（http://127.0.0.1:8123）…"
(cd backend && "$VENV_DIR/bin/uvicorn" app.main:app --reload --host 127.0.0.1 --port 8123) &
API_PID=$!

info "正在启动课程工作台（http://localhost:3000）…"
(cd web && npm run dev) &
WEB_PID=$!

HEALTH_ATTEMPT=0
while [ "$HEALTH_ATTEMPT" -lt 40 ]; do
  if curl -fsS http://127.0.0.1:8123/health >/dev/null 2>&1; then
    info "启动完成：请在浏览器打开 http://localhost:3000"
    info "按 Ctrl+C 可同时安全停止 Web 与 API。"
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    fail "API 提前退出，请查看上方日志。"
  fi
  sleep 0.5
  HEALTH_ATTEMPT=$((HEALTH_ATTEMPT + 1))
done

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 1
done

fail "其中一个服务已退出，请查看上方日志。"
