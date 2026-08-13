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
# WO-FCE-LIVENESS-VERDICT-01: 문턱·집계 규약은 공용 파일 하나. deadman.sh 와 같은 것을 본다.
# shellcheck source=lib/liveness.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/liveness.sh"

# A7: 매달림 재시작이 kill 한 직후, 백엔드가 아직 리스닝하지 않는 구간에 메인 루프의
# `start_backend` 가 끼어들어 **두 번째 run-backend.sh** 를 띄울 수 있다. 그 창을 표식으로 막되
# TTL 을 반드시 둔다 — 표식이 남아 영구히 재시작을 막으면 그게 새로운 침묵이다(C5).
RESTART_LOCK="$LOG_DIR/restart.lock"
RESTART_LOCK_TTL="${FCE_SUPERVISOR_RESTART_LOCK_TTL:-120}"
INTERVAL="${FCE_SUPERVISOR_INTERVAL:-15}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_DIR/supervisor.log"; }

listening() { lsof -ti :"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# WO-FCE-ENGINE-LIVENESS-01 작업 6: 재시작을 조용히 넘기지 않는다(C4).
# 워커가 이 파일을 읽어 "최근 24h 재시작 N회"를 알림·진단에 노출한다.
record_restart() {
  printf '{"at":"%s","target":"%s","reason":"%s"}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')" "$1" "${2:-port_down}" >> "$LOG_DIR/restarts.jsonl"
}

# ── 재시작 진행 표식 (A7) ────────────────────────────────────────────
take_restart_lock() { date +%s > "$RESTART_LOCK"; }

restart_lock_held() {
  [ -f "$RESTART_LOCK" ] || return 1
  local started now
  started="$(cat "$RESTART_LOCK" 2>/dev/null || echo 0)"
  now=$(date +%s)
  if [ $((now - ${started:-0})) -lt "$RESTART_LOCK_TTL" ] 2>/dev/null; then
    return 0
  fi
  # TTL 만료는 조용히 넘기지 않는다 — 표식이 왜 풀렸는지 남아야 한다(C5).
  log "restart lock TTL 만료($(( now - ${started:-0} ))s > ${RESTART_LOCK_TTL}s) — 포트 감시 재개"
  rm -f "$RESTART_LOCK"
  return 1
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
# 사망 문턱은 공용 규약과 같은 값을 쓴다(따로 두면 두 감시자가 다른 판정을 한다).
# **올리지 않는다**(C1).
HB_STALE_LIMIT="${FCE_SUPERVISOR_HB_STALE_SECONDS:-$FCE_STALE_LIMIT}"
HB_COOLDOWN="${FCE_SUPERVISOR_HB_COOLDOWN_SECONDS:-600}"
HB_MAX_PER_HOUR="${FCE_SUPERVISOR_HB_MAX_RESTARTS_PER_HOUR:-3}"
HB_LAST_RESTART=0
HB_GIVEUP_NOTIFIED=0
# A2: 포기 래치는 **단발 하트비트로 풀리지 않는다.** 복구 문턱 이내가 지속돼야 해제한다 —
# 래치를 더 오래 유지하는 방향이므로 복구 폭주 금지(C5 원칙)를 약화시키지 않는다.
HB_FRESH_SINCE=0

# 하트비트 판독도 공용 규약을 쓴다 — deadman 과 다른 계산을 하면 두 감시자가 다른 나이를 본다.
heartbeat_age() { liveness_heartbeat_age "$LIVENESS_FILE"; }

# 포기 판정용 집계. **알림 카운터와 같은 원장·같은 함수**를 쓴다(A4) — 예전에는 알림이
# supervisor.log grep, 포기 판정이 restarts.jsonl 이라 두 숫자가 서로를 설명하지 못했다.
restarts_last_hour() {
  liveness_restarts_in_window "$LOG_DIR/restarts.jsonl" heartbeat_stale 1
}

check_heartbeat_hang() {
  local age recent now
  age="$(heartbeat_age)"
  [ "$age" -lt 0 ] 2>/dev/null && return 0          # 하트비트 없음 → deadman.sh 가 알린다
  now=$(date +%s)

  # A2: 신선 지속을 추적한다. 예전에는 `age <= 900` 한 틱만으로 래치가 풀렸고, 그래서
  # 16:52 의 단발 하트비트 하나가 16:20 의 포기를 해제했다.
  if liveness_is_fresh "$age"; then
    [ "$HB_FRESH_SINCE" -eq 0 ] 2>/dev/null && HB_FRESH_SINCE=$now
  else
    HB_FRESH_SINCE=0
  fi

  if [ "$age" -le "$HB_STALE_LIMIT" ] 2>/dev/null; then
    if liveness_recovery_confirmed "$HB_FRESH_SINCE" "$now"; then
      if [ "$HB_GIVEUP_NOTIFIED" = "1" ]; then
        log "heartbeat 신선 지속 확인(${age}s ≤ ${FCE_FRESH_LIMIT}s, $((now - HB_FRESH_SINCE))s 유지) — 자동 복구 포기 해제"
      fi
      HB_GIVEUP_NOTIFIED=0
    fi
    return 0
  fi
  if [ $((now - HB_LAST_RESTART)) -lt "$HB_COOLDOWN" ]; then return 0; fi   # C5 쿨다운
  recent="$(restarts_last_hour)"
  if [ "$recent" -ge "$HB_MAX_PER_HOUR" ] 2>/dev/null; then
    if [ "$HB_GIVEUP_NOTIFIED" = "0" ]; then
      # WO-FCE-WORKER-HANG-02: **포기 상태야말로 증거가 가장 필요한 순간이다.**
      # "재시작으로 해결되지 않는다"는 판단을 내린 시점인데 여기서 return 하면
      # 스택을 뜰 기회가 영영 없다. 포착은 프로세스를 죽이지 않으므로 안전하다.
      # HB_GIVEUP_NOTIFIED 가드 안에 두어 15초마다 반복 포착하지 않는다.
      giveup_pids="$(lsof -ti :8875 -sTCP:LISTEN 2>/dev/null || true)"
      for giveup_pid in $giveup_pids; do
        timeout "${FCE_HANG_CAPTURE_TIMEOUT:-20}" /bin/bash "$REPO_DIR/scripts/local/capture-hang.sh" \
          "$giveup_pid" "$LOG_DIR" "giveup_${age}s" >/dev/null 2>&1 || \
          log "hang capture 실패/타임아웃(포기 경로) pid=$giveup_pid"
      done
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
  take_restart_lock            # A7: 재기동 완료 전까지 포트 감시가 끼어들지 않게 한다
  pids="$(lsof -ti :8875 -sTCP:LISTEN 2>/dev/null || true)"
  # ── WO-FCE-WORKER-HANG-02 (D5·C2): 포착이 파괴에 선행한다 ──────────────
  # 여기가 kill -9 **바로 앞**이어야 하는 이유: 감지와 파괴 사이에 아무것도 없었기 때문에
  # 실측 101회의 매달림에서 단 한 번도 스택을 못 떴다. 증상이 잡힐 때마다 증거가 사라졌다.
  # C3: 전체 예산(기본 10초 + 여유) 안에서만 시도하고, 실패·타임아웃해도 재시작을 막지 않는다.
  if [ -n "$pids" ]; then
    for hang_pid in $pids; do
      timeout "${FCE_HANG_CAPTURE_TIMEOUT:-20}" /bin/bash "$REPO_DIR/scripts/local/capture-hang.sh" \
        "$hang_pid" "$LOG_DIR" "heartbeat_stale_${age}s" >/dev/null 2>&1 || \
        log "hang capture 실패/타임아웃 pid=$hang_pid — 재시작은 정상 진행"
    done
  fi
  [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
  sleep 2
  nohup /bin/bash "$REPO_DIR/scripts/local/run-backend.sh" >> "$LOG_DIR/backend.log" 2>&1 &
}

log "supervisor started (pid $$, interval ${INTERVAL}s)"
deadman_tick=0
while true; do
  # A7: 매달림 재시작이 진행 중이면 포트 감시는 손을 뗀다(이중 기동 방지). TTL 이 있으므로
  # 표식이 남아도 최대 RESTART_LOCK_TTL 초 뒤에는 감시가 반드시 재개된다.
  if ! restart_lock_held; then
    listening 8875 || start_backend
  fi
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
