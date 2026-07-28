#!/usr/bin/env bash
# FCE 로컬 서버 keepalive 감시 루프 — 8875 백엔드·8876 프론트가 죽으면 다시 띄운다.
#
# 왜 launchd 가 아니라 이 방식인가: macOS TCC 가 ~/Documents 를 launchd 에이전트로부터
# 차단(Operation not permitted)한다. 이 루프는 현재 로그인 세션(파일 접근 허용됨)에서
# 백그라운드로 돌아 크래시·터미널 종료에도 서버를 유지한다. (재부팅 후엔 start-supervisor.sh 재실행)
set +e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LIVENESS_FILE="$LOG_DIR/liveness.json"
mkdir -p "$LOG_DIR"
INTERVAL="${FCE_SUPERVISOR_INTERVAL:-15}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_DIR/supervisor.log"; }

listening() { lsof -ti :"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# WO-FCE-ENGINE-LIVENESS-01 작업 6: 재시작을 조용히 넘기지 않는다(C4).
# 워커가 이 파일을 읽어 "최근 24h 재시작 N회"를 알림·진단에 노출한다.
record_restart() {
  printf '{"at":"%s","target":"%s","reason":"%s"}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')" "$1" "${2:-port_down}" >> "$LOG_DIR/restarts.jsonl"
}

start_backend() {
  log "backend(8875) down → restart"
  record_restart "backend:8875"
  nohup /bin/bash "$REPO_DIR/scripts/local/run-backend.sh" >> "$LOG_DIR/backend.log" 2>&1 &
}
start_frontend() {
  log "frontend(8876) down → restart"
  record_restart "frontend:8876"
  nohup /bin/bash "$REPO_DIR/scripts/local/run-frontend.sh" >> "$LOG_DIR/frontend.log" 2>&1 &
}

# ── WO-FCE-ENGINE-RESTORE-01 (C4·C5): 하트비트 기반 매달림 복구 ──────────────
#
# C4(감지-조치 일치): 사망 판정 신호와 복구 트리거 신호가 같아야 한다.
#   기존엔 감지=하트비트(deadman), 조치=포트(listening) 로 **어긋나 있었다.**
#   그래서 포트는 열린 채 워커만 매달린 11.7시간 동안 supervisor 가 개입하지 않았다.
# C5(복구 폭주 금지): 쿨다운 10분 · 1시간 3회 상한. 초과 시 자동 복구를 포기하고 사람을 부른다.
HB_STALE_LIMIT="${FCE_SUPERVISOR_HB_STALE_SECONDS:-900}"
HB_COOLDOWN="${FCE_SUPERVISOR_HB_COOLDOWN_SECONDS:-600}"
HB_MAX_PER_HOUR="${FCE_SUPERVISOR_HB_MAX_RESTARTS_PER_HOUR:-3}"
HB_LAST_RESTART=0
HB_GIVEUP_NOTIFIED=0

heartbeat_age() {
  [ -f "$LIVENESS_FILE" ] || { echo -1; return; }
  python3 - "$LIVENESS_FILE" <<'PYEOF' 2>/dev/null || echo -1
import json, sys
from datetime import datetime, timezone
try:
    written = json.load(open(sys.argv[1]))["written_at"]
    stamp = datetime.fromisoformat(written.replace("Z", "+00:00"))
    print(int((datetime.now(timezone.utc) - stamp).total_seconds()))
except Exception:
    print(-1)
PYEOF
}

restarts_last_hour() {
  [ -f "$LOG_DIR/restarts.jsonl" ] || { echo 0; return; }
  python3 - "$LOG_DIR/restarts.jsonl" <<'PYEOF' 2>/dev/null || echo 0
import json, sys
from datetime import datetime, timedelta, timezone
cut = datetime.now(timezone.utc) - timedelta(hours=1)
count = 0
try:
    for line in open(sys.argv[1]).read().splitlines()[-200:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("reason") or "") != "heartbeat_stale":
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("at")).replace("Z", "+00:00"))
        except Exception:
            continue
        if stamp >= cut:
            count += 1
except OSError:
    pass
print(count)
PYEOF
}

check_heartbeat_hang() {
  local age recent now
  age="$(heartbeat_age)"
  [ "$age" -lt 0 ] 2>/dev/null && return 0          # 하트비트 없음 → deadman.sh 가 알린다
  [ "$age" -le "$HB_STALE_LIMIT" ] 2>/dev/null && { HB_GIVEUP_NOTIFIED=0; return 0; }
  now=$(date +%s)
  if [ $((now - HB_LAST_RESTART)) -lt "$HB_COOLDOWN" ]; then return 0; fi   # C5 쿨다운
  recent="$(restarts_last_hour)"
  if [ "$recent" -ge "$HB_MAX_PER_HOUR" ] 2>/dev/null; then
    if [ "$HB_GIVEUP_NOTIFIED" = "0" ]; then
      log "heartbeat stale ${age}s BUT 1시간 재시작 ${recent}회 — 자동 복구 포기(수동 개입 필요)"
      FCE_DEADMAN_FORCE_MESSAGE="🛑 <b>자동 복구 포기 · 수동 개입 필요</b>
하트비트 ${age}초 정체가 지속되는데 최근 1시간 재시작이 ${recent}회입니다.
재시작으로 해결되지 않는 문제이므로 자동 복구를 중단합니다."         /bin/bash "$REPO_DIR/scripts/local/deadman.sh" >> "$LOG_DIR/deadman.log" 2>&1 || true
      HB_GIVEUP_NOTIFIED=1
    fi
    return 0
  fi
  log "heartbeat stale ${age}s (>${HB_STALE_LIMIT}s) — 포트는 열려 있으나 워커 매달림 → 재시작"
  record_restart "backend:8875:heartbeat_stale" "heartbeat_stale"
  HB_LAST_RESTART=$now
  pids="$(lsof -ti :8875 -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
  sleep 2
  nohup /bin/bash "$REPO_DIR/scripts/local/run-backend.sh" >> "$LOG_DIR/backend.log" 2>&1 &
}

log "supervisor started (pid $$, interval ${INTERVAL}s)"
deadman_tick=0
while true; do
  listening 8875 || start_backend
  listening 8876 || start_frontend
  # 포트가 열려 있어도 심장박동이 멎었으면 매달림이다 — 감지 신호와 조치 신호를 일치시킨다(C4).
  check_heartbeat_hang
  # WO-FCE-ENGINE-LIVENESS-01 작업 3: 외부 데드맨 스위치.
  # 프로세스가 살아 있어도 워커가 하트비트를 못 쓰면(스케줄러 정지·잡 데드락) 여기서 잡는다.
  # 60초에 한 번만 평가(감시 루프는 15초 주기).
  deadman_tick=$((deadman_tick + INTERVAL))
  if [ "$deadman_tick" -ge 60 ]; then
    deadman_tick=0
    /bin/bash "$REPO_DIR/scripts/local/deadman.sh" >> "$LOG_DIR/deadman.log" 2>&1 || true
  fi
  sleep "$INTERVAL"
done
