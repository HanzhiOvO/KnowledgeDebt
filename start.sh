#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
OPEN_BROWSER=1
SKIP_INSTALL=0

usage() {
  cat <<'EOF'
KnowledgeDebt 一键启动脚本

用法：
  ./start.sh [选项]

选项：
  --no-browser    服务启动后不自动打开浏览器
  --skip-install  跳过依赖检查与安装
  -h, --help      显示帮助
EOF
}

fail() {
  printf '错误：%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令 $1。$2"
}

hash_files() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$@" | shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@" | sha256sum | awk '{print $1}'
  else
    return 1
  fi
}

python_is_compatible() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
    >/dev/null 2>&1
}

select_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    require_command "$PYTHON_BIN" "请设置为 Python 3.12 或更高版本的可执行文件。"
    python_is_compatible "$PYTHON_BIN" || fail "$PYTHON_BIN 不是 Python 3.12 或更高版本。"
    return
  fi
  for python_candidate in python3.13 python3.12 python3; do
    if command -v "$python_candidate" >/dev/null 2>&1 && python_is_compatible "$python_candidate"; then
      PYTHON_BIN="$python_candidate"
      return
    fi
  done
  fail "未找到 Python 3.12 或更高版本。可通过 PYTHON_BIN=/path/to/python3.12 指定。"
}

open_browser_when_ready() {
  if ! command -v curl >/dev/null 2>&1; then
    return
  fi
  for _attempt in {1..90}; do
    if curl --fail --silent --output /dev/null http://127.0.0.1:3000; then
      printf '\n服务已就绪：http://localhost:3000\n'
      if command -v open >/dev/null 2>&1; then
        open http://localhost:3000
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open http://localhost:3000 >/dev/null 2>&1 || true
      fi
      return
    fi
    sleep 1
  done
  printf '\n服务仍在启动，请稍后手动打开 http://localhost:3000\n'
}

for argument in "$@"; do
  case "$argument" in
    --no-browser)
      OPEN_BROWSER=0
      ;;
    --skip-install)
      SKIP_INSTALL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知选项：$argument（使用 --help 查看帮助）"
      ;;
  esac
done

cd "$PROJECT_DIR"

require_command node "请安装 Node.js 24 或更高版本。"
require_command npm "请安装 npm。"
require_command make "请安装 make。"

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if (( NODE_MAJOR < 24 )); then
  fail "需要 Node.js 24 或更高版本，当前版本为 $(node --version)。"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  printf '已创建 .env。需要外部 AI/ASR 时，请先填写 OPENAI_API_KEY。\n'
fi

if (( SKIP_INSTALL == 0 )); then
  if [[ -x .venv/bin/python ]] && ! python_is_compatible .venv/bin/python; then
    fail "现有 .venv 的 Python 版本低于 3.12，请备份需要的内容后删除 .venv 并重试。"
  fi
  if [[ ! -x .venv/bin/python ]]; then
    select_python
    printf '正在创建 Python 虚拟环境……\n'
    "$PYTHON_BIN" -m venv .venv
  fi

  REQUIREMENTS_STAMP=".venv/.knowledgedebt-requirements.sha256"
  REQUIREMENTS_HASH="$(hash_files backend/requirements.txt backend/requirements-dev.txt || true)"
  INSTALLED_REQUIREMENTS_HASH="$(test -f "$REQUIREMENTS_STAMP" && sed -n '1p' "$REQUIREMENTS_STAMP" || true)"
  if [[ -z "$REQUIREMENTS_HASH" || "$REQUIREMENTS_HASH" != "$INSTALLED_REQUIREMENTS_HASH" ]]; then
    printf '正在安装后端依赖……\n'
    .venv/bin/pip install -r backend/requirements-dev.txt
    [[ -n "$REQUIREMENTS_HASH" ]] && printf '%s\n' "$REQUIREMENTS_HASH" > "$REQUIREMENTS_STAMP"
  else
    printf '后端依赖没有变化，跳过安装。\n'
  fi

  WEB_STAMP="web/node_modules/.knowledgedebt-package-lock.sha256"
  WEB_HASH="$(hash_files web/package.json web/package-lock.json || true)"
  INSTALLED_WEB_HASH="$(test -f "$WEB_STAMP" && sed -n '1p' "$WEB_STAMP" || true)"
  if [[ ! -d web/node_modules || -z "$WEB_HASH" || "$WEB_HASH" != "$INSTALLED_WEB_HASH" ]]; then
    printf '正在安装 Web 依赖……\n'
    (cd web && npm ci)
    [[ -n "$WEB_HASH" ]] && printf '%s\n' "$WEB_HASH" > "$WEB_STAMP"
  else
    printf 'Web 依赖没有变化，跳过安装。\n'
  fi
else
  [[ -x .venv/bin/python ]] || fail "未找到 .venv；请去掉 --skip-install 后重试。"
  python_is_compatible .venv/bin/python || fail ".venv 需要使用 Python 3.12 或更高版本重建。"
  [[ -d web/node_modules ]] || fail "未找到 web/node_modules；请去掉 --skip-install 后重试。"
fi

printf '\n正在启动 KnowledgeDebt……\n'
printf 'Web：http://localhost:3000\nAPI：http://127.0.0.1:8123\n按 Ctrl+C 可同时停止服务。\n\n'

if (( OPEN_BROWSER == 1 )); then
  open_browser_when_ready &
fi

exec make dev
