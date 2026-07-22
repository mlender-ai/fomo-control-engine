#!/usr/bin/env bash
# 감시 루프와 서버를 의도적으로 완전히 내린다(포트 기준 — 광범위 pkill 금지).
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="$REPO_DIR/logs/supervisor.pid"

if [[ -f "$PIDFILE" ]]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "감시 루프 중지."
fi
# 서버 프로세스는 포트로만 종료(다른 프로젝트 next-server 오살상 방지).
for port in 8875 8876; do
  pids="$(lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill 2>/dev/null || true
    echo "포트 $port 종료."
  fi
done
