#!/usr/bin/env bash
# 開発用サービスの起動・停止(クラウド VM 内だけ)。入っていなければ「無い」と言って0で終わる。
#   services.sh start postgres|redis
#   services.sh stop  postgres|redis
#   services.sh status
# Claude Code on the web: PostgreSQL 16 と Redis 7 が入っていて既定で停止(公式文書)。
# Codex cloud: 公式に記載なし。無ければ docker compose か、テストのモックで代替する。
set -u
act="${1:-status}"; svc="${2:-}"
have() { command -v "$1" >/dev/null 2>&1; }
sudo_() { if [ "$(id -u)" = 0 ]; then "$@"; elif have sudo; then sudo -n "$@"; else "$@"; fi; }

pg() {
  case "$1" in
    start)
      if have service && [ -x /etc/init.d/postgresql ]; then sudo_ service postgresql start
      elif have pg_ctlcluster; then sudo_ pg_ctlcluster "$(ls /etc/postgresql | head -1)" main start
      else echo "postgres: 入っていない"; return 0; fi
      for _ in $(seq 1 20); do pg_isready -q 2>/dev/null && { echo "postgres: ready"; return 0; }; sleep 0.5; done
      echo "postgres: 起動を確認できない"; return 1 ;;
    stop) have service && sudo_ service postgresql stop ;;
    status) pg_isready 2>/dev/null || echo "postgres: 停止中または未導入" ;;
  esac
}
rd() {
  case "$1" in
    start)
      if have service && [ -x /etc/init.d/redis-server ]; then sudo_ service redis-server start
      elif have redis-server; then redis-server --daemonize yes
      else echo "redis: 入っていない"; return 0; fi
      for _ in $(seq 1 20); do redis-cli ping >/dev/null 2>&1 && { echo "redis: ready"; return 0; }; sleep 0.5; done
      echo "redis: 起動を確認できない"; return 1 ;;
    stop) redis-cli shutdown nosave >/dev/null 2>&1 || true ;;
    status) redis-cli ping 2>/dev/null || echo "redis: 停止中または未導入" ;;
  esac
}
case "$act:$svc" in
  start:postgres|stop:postgres) pg "$act" ;;
  start:redis|stop:redis) rd "$act" ;;
  status:*) pg status; rd status ;;
  *) echo "usage: $0 {start|stop} {postgres|redis} | status" >&2; exit 2 ;;
esac
