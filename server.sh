#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"

usage() {
  cat <<'EOF'
知债 KnowledgeDebt 寝室服务器管理脚本（Docker Compose）

用法：
  ./server.sh [start|stop|restart|status|logs]

命令：
  start     创建安全的本机配置，构建并后台启动服务（默认）
  stop      停止服务，保留 PostgreSQL、资源和模型数据卷
  restart   重建并重新启动服务
  status    查看容器状态和校园网访问地址
  logs      持续查看 Web、API 和数据库日志；按 Ctrl+C 退出日志
EOF
}

fail() {
  printf '错误：%s\n' "$1" >&2
  exit 1
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "未安装 Docker。请先安装 Docker Engine + Compose 插件或 Docker Desktop。"
  docker compose version >/dev/null 2>&1 || fail "当前 Docker 没有 Compose v2；请确认 docker compose 命令可用。"
  docker info >/dev/null 2>&1 || fail "Docker 服务未运行。请先启动 Docker。"
}

read_env_value() {
  local key="$1"
  [[ -f "$PROJECT_DIR/.env" ]] || return 0
  awk -v key="$key" '
    index($0, key "=") == 1 {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "$PROJECT_DIR/.env"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local temporary
  temporary="$(mktemp "$PROJECT_DIR/.env.tmp.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) print key "=" value
    }
  ' "$PROJECT_DIR/.env" > "$temporary"
  mv "$temporary" "$PROJECT_DIR/.env"
}

generate_secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

prepare_env() {
  if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    printf '已从 .env.example 创建仅供服务器使用的 .env。\n'
  fi

  local database_password access_token
  database_password="$(read_env_value POSTGRES_PASSWORD)"
  if [[ -z "$database_password" || "$database_password" == "change-this-password" ]]; then
    set_env_value POSTGRES_PASSWORD "$(generate_secret)"
    printf '已为 PostgreSQL 生成随机密码（只保存在 .env，不会输出）。\n'
  fi

  access_token="$(read_env_value KNOWLEDGEDEBT_ACCESS_TOKEN)"
  if [[ -z "$access_token" ]]; then
    set_env_value KNOWLEDGEDEBT_ACCESS_TOKEN "$(generate_secret)"
    printf '已为后端生成随机访问令牌（只保存在 .env，不会输出）。\n'
  fi

  chmod 600 "$PROJECT_DIR/.env" 2>/dev/null || true
}

detect_lan_ip() {
  local detected=""
  if [[ -n "${KNOWLEDGEDEBT_SERVER_IP:-}" ]]; then
    detected="$KNOWLEDGEDEBT_SERVER_IP"
  elif command -v ip >/dev/null 2>&1; then
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

print_access_url() {
  local lan_ip web_port bind_address
  lan_ip="$(detect_lan_ip)"
  web_port="$(read_env_value KNOWLEDGEDEBT_WEB_PORT)"
  bind_address="$(read_env_value KNOWLEDGEDEBT_WEB_BIND_ADDRESS)"
  web_port="${web_port:-3000}"
  bind_address="${bind_address:-0.0.0.0}"

  printf '\n本机访问：http://127.0.0.1:%s\n' "$web_port"
  if [[ "$bind_address" == "127.0.0.1" || "$bind_address" == "localhost" ]]; then
    printf '当前 KNOWLEDGEDEBT_WEB_BIND_ADDRESS=%s，只允许本机访问。\n' "$bind_address"
  elif [[ -n "$lan_ip" ]]; then
    printf '校园网访问：http://%s:%s\n' "$lan_ip" "$web_port"
  else
    printf '校园网访问：http://<GMK G10 的局域网 IPv4>:%s\n' "$web_port"
  fi
  printf '防火墙只需放行 TCP %s；不要放行 API 8123。\n' "$web_port"
}

wait_until_ready() {
  command -v curl >/dev/null 2>&1 || return 0
  local web_port
  web_port="$(read_env_value KNOWLEDGEDEBT_WEB_PORT)"
  web_port="${web_port:-3000}"
  printf '正在等待 Web 与同源 API 代理就绪……\n'
  for _attempt in {1..90}; do
    if curl --fail --silent --output /dev/null "http://127.0.0.1:${web_port}/api/backend/health"; then
      printf '服务已就绪。\n'
      return 0
    fi
    sleep 2
  done
  printf '服务尚未通过健康检查，请运行 ./server.sh logs 查看原因。\n' >&2
  return 1
}

cd "$PROJECT_DIR"

case "$ACTION" in
  -h|--help|help)
    usage
    exit 0
    ;;
  start|stop|restart|status|logs)
    ;;
  *)
    fail "未知命令：$ACTION（使用 ./server.sh --help 查看帮助）"
    ;;
esac

require_docker

case "$ACTION" in
  start)
    prepare_env
    docker compose up --build --detach
    wait_until_ready || true
    print_access_url
    ;;
  restart)
    prepare_env
    docker compose up --build --detach --force-recreate
    wait_until_ready || true
    print_access_url
    ;;
  stop)
    docker compose down
    printf '服务已停止；PostgreSQL、资源和模型数据卷均已保留。\n'
    ;;
  status)
    docker compose ps
    print_access_url
    ;;
  logs)
    docker compose logs --follow web backend database
    ;;
esac
