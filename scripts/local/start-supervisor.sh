#!/usr/bin/env bash
# FCE keepalive 감시 루프를 백그라운드로 기동(터미널 종료에도 유지).
# 이미 돌고 있으면 재기동하지 않는다(중복 방지).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="$REPO_DIR/logs/supervisor.pid"
mkdir -p "$REPO_DIR/logs"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "이미 실행 중 (pid $(cat "$PIDFILE"))."
  exit 0
fi

nohup /bin/bash "$REPO_DIR/scripts/local/supervisor.sh" >> "$REPO_DIR/logs/supervisor.log" 2>&1 &
SUP_PID=$!
disown "$SUP_PID" 2>/dev/null || true
echo "$SUP_PID" > "$PIDFILE"
echo "감시 루프 시작 (pid $SUP_PID). 로그: logs/supervisor.log"
echo "중지: scripts/local/stop-supervisor.sh"
