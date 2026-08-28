#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
export npm_config_cache="${npm_config_cache:-$PROJECT_DIR/.npm-cache}"
OPEN_BROWSER=1
SKIP_INSTALL=0
LAN_MODE=0
WEB_HOST="127.0.0.1"

usage() {
  cat <<'EOF'
知债 KnowledgeDebt 一键启动脚本

用法：
  ./start.sh [选项]

选项：
  --lan           允许同一局域网 / 校园网内的设备访问 Web（仅开放 3000）
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

create_virtualenv() {
  if "$PYTHON_BIN" -m venv .venv 2>/dev/null; then
    return 0
  fi
  printf '系统 python3-venv 不可用，正在用 get-pip.py 引导本地 virtualenv……\n'
  require_command curl "启用 venv 引导回退需要 curl。"
  local bootstrap="$PROJECT_DIR/.venv-bootstrap"
  mkdir -p "$bootstrap"
  curl --fail --silent --show-error --location \
    https://bootstrap.pypa.io/get-pip.py -o "$PROJECT_DIR/.get-pip.py" \
    || fail "无法下载 get-pip.py；可手动创建 .venv 后使用 ./start.sh --skip-install。"
  "$PYTHON_BIN" "$PROJECT_DIR/.get-pip.py" --target "$bootstrap" --break-system-packages --no-warn-script-location \
    || fail "get-pip.py 引导失败；可手动创建 .venv 后使用 ./start.sh --skip-install。"
  PYTHONPATH="$bootstrap" "$PYTHON_BIN" -m pip install --target "$bootstrap" --no-warn-script-location virtualenv \
    || fail "virtualenv 安装失败；可手动创建 .venv 后使用 ./start.sh --skip-install。"
  PYTHONPATH="$bootstrap" "$PYTHON_BIN" -m virtualenv .venv \
    || fail "virtualenv 创建失败；可手动创建 .venv 后使用 ./start.sh --skip-install。"
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

detect_lan_ip() {
  local detected=""
  if command -v ip >/dev/null 2>&1; then
    detected="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (index = 1; index <= NF; index += 1) if ($index == "src") {print $(index + 1); exit}}')"
  elif command -v route >/dev/null 2>&1 && command -v ipconfig >/dev/null 2>&1; then
    local interface_name
    interface_name="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
    [[ -n "$interface_name" ]] && detected="$(ipconfig getifaddr "$interface_name" 2>/dev/null || true)"
    if [[ -z "$detected" ]]; then
      for interface_name in en0 en1; do
        detected="$(ipconfig getifaddr "$interface_name" 2>/dev/null || true)"
        [[ -n "$detected" ]] && break
      done
    fi
  elif command -v hostname >/dev/null 2>&1; then
    detected="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  printf '%s' "$detected"
}

clear_stale_next_lock() {
  local lock_file="$PROJECT_DIR/web/.next/dev/lock"
  [[ -f "$lock_file" ]] || return 0
  local lock_pid lock_command
  lock_pid="$(sed -n 's/.*"pid":\([0-9][0-9]*\).*/\1/p' "$lock_file" | head -n 1)"
  lock_command="$(test -n "$lock_pid" && ps -p "$lock_pid" -o command= 2>/dev/null || true)"
  if [[ -n "$lock_pid" && "$lock_command" == *"next"* ]]; then
    fail "检测到另一个 Next.js 开发服务仍在运行（PID $lock_pid）。请先停止旧实例。"
  fi
  local stale_lock="${lock_file}.stale.$(date +%Y%m%d%H%M%S)"
  mv "$lock_file" "$stale_lock"
  printf '已保留并移开失效的 Next.js 启动锁：%s\n' "${stale_lock#$PROJECT_DIR/}"
}

for argument in "$@"; do
  case "$argument" in
    --no-browser)
      OPEN_BROWSER=0
      ;;
    --lan)
      LAN_MODE=1
      WEB_HOST="0.0.0.0"
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

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if (( NODE_MAJOR < 24 )); then
  fail "需要 Node.js 24 或更高版本，当前版本为 $(node --version)。"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  printf '已创建 .env。AI Provider 可在 Web 设置页粘贴官方 Key；语音转写默认使用本地 Whisper。\n'
fi
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if (( SKIP_INSTALL == 0 )); then
  if [[ -x .venv/bin/python ]] && ! python_is_compatible .venv/bin/python; then
    fail "现有 .venv 的 Python 版本低于 3.12，请备份需要的内容后删除 .venv 并重试。"
  fi
  if [[ ! -x .venv/bin/python ]]; then
    select_python
    printf '正在创建 Python 虚拟环境……\n'
    create_virtualenv
  fi

  REQUIREMENTS_STAMP=".venv/.knowledgedebt-requirements.sha256"
  REQUIREMENTS_FILES=(backend/requirements.txt backend/requirements-dev.txt)
  REQUIREMENTS_HASH="$(hash_files "${REQUIREMENTS_FILES[@]}" || true)"
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

printf '\n正在启动知债（KnowledgeDebt）……\n'
printf 'Web：http://localhost:3000\nAPI：http://127.0.0.1:8123\n按 Ctrl+C 可同时停止服务。\n\n'
if (( LAN_MODE == 1 )); then
  LAN_IP="$(detect_lan_ip)"
  if [[ -n "$LAN_IP" ]]; then
    printf '校园网访问地址：http://%s:3000\n' "$LAN_IP"
  else
    printf '校园网访问地址：http://<这台电脑的局域网 IPv4>:3000\n'
  fi
  printf '已仅向局域网开放 Web 3000；API 8123 仍只允许本机访问。\n'
  printf '请只在防火墙中放行 TCP 3000，不要放行 8123。\n\n'
fi

if (( OPEN_BROWSER == 1 )); then
  open_browser_when_ready &
fi

clear_stale_next_lock

API_PID=""
WEB_PID=""

stop_services() {
  trap - INT TERM EXIT
  [[ -n "$API_PID" ]] && kill "$API_PID" >/dev/null 2>&1 || true
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" >/dev/null 2>&1 || true
  [[ -n "$API_PID" ]] && wait "$API_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && wait "$WEB_PID" 2>/dev/null || true
}

trap stop_services INT TERM EXIT
(cd backend && ../.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8123) &
API_PID=$!
(cd web && npm run dev -- --hostname "$WEB_HOST") &
WEB_PID=$!

while kill -0 "$API_PID" >/dev/null 2>&1 && kill -0 "$WEB_PID" >/dev/null 2>&1; do
  sleep 1
done

if ! kill -0 "$API_PID" >/dev/null 2>&1; then
  wait "$API_PID"
else
  wait "$WEB_PID"
fi
