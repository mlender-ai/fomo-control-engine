#!/usr/bin/env bash
# FCE 로컬 서버 keepalive 감시 루프 — 8875 백엔드·8876 프론트가 죽으면 다시 띄운다.
#
# 왜 launchd 가 아니라 이 방식인가: macOS TCC 가 ~/Documents 를 launchd 에이전트로부터
# 차단(Operation not permitted)한다. 이 루프는 현재 로그인 세션(파일 접근 허용됨)에서
# 백그라운드로 돌아 크래시·터미널 종료에도 서버를 유지한다. (재부팅 후엔 start-supervisor.sh 재실행)
set +e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"
INTERVAL="${FCE_SUPERVISOR_INTERVAL:-15}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_DIR/supervisor.log"; }

listening() { lsof -ti :"$1" -sTCP:LISTEN >/dev/null 2>&1; }

start_backend() {
  log "backend(8875) down → restart"
  nohup /bin/bash "$REPO_DIR/scripts/local/run-backend.sh" >> "$LOG_DIR/backend.log" 2>&1 &
}
start_frontend() {
  log "frontend(8876) down → restart"
  nohup /bin/bash "$REPO_DIR/scripts/local/run-frontend.sh" >> "$LOG_DIR/frontend.log" 2>&1 &
}

log "supervisor started (pid $$, interval ${INTERVAL}s)"
while true; do
  listening 8875 || start_backend
  listening 8876 || start_frontend
  sleep "$INTERVAL"
done
