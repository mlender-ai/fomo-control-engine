#!/usr/bin/env bash
# WO-FCE-ENGINE-LIVENESS-01 작업 3 — 외부 데드맨 스위치.
#
# 원칙: **감시자는 감시 대상 안에 살 수 없다.**
#   워커 안의 모든 감시(펄스·일일요약·data_stall)는 워커가 죽으면 함께 죽는다. 침묵이 스스로를 은폐한다.
#   이 스크립트는 워커 프로세스 **밖**에서 돌며, 워커가 남긴 하트비트 파일의 타임스탬프만 읽는다.
#   임계 초과 시 **텔레그램 API로 직접** 쏜다 — 앱/워커 코드를 경유하지 않는다(죽은 경로 재사용 금지).
#
# 왜 별도 프로세스가 아니라 기존 keepalive supervisor 확장인가:
#   감시자가 늘면 "감시자의 감시자" 문제가 늘어난다. 이미 세션 상주하며 프로세스를 되살리는
#   supervisor 루프 하나에 통합해 실패 지점을 최소화한다(WO 119행: 감시자는 워커보다 단순해야 한다).
#
# 의존성: bash, curl, date, python3(JSON 파싱 1회). 앱 코드 import 없음.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LIVENESS_FILE="$LOG_DIR/liveness.json"
STATE_FILE="$LOG_DIR/deadman.state"     # 재발송 억제·복구 알림 1회용
DEADMAN_LOG="$LOG_DIR/deadman.log"
ENV_FILE="$REPO_DIR/backend/.env"

# 하트비트가 이 시간(초)을 넘겨 낡으면 사망으로 판정. 워커 기대 주기(300s)의 3배.
STALE_LIMIT="${FCE_DEADMAN_STALE_SECONDS:-900}"
# 사망 지속 시 리마인더 간격(초).
REMIND_EVERY="${FCE_DEADMAN_REMIND_SECONDS:-3600}"
# 감시자 자체 생존 확인 주기(초, 기본 7일) — 감시자가 조용한 게 정상인지 확인 불능이면 안 된다.
SELFCHECK_EVERY="${FCE_DEADMAN_SELFCHECK_SECONDS:-604800}"

mkdir -p "$LOG_DIR"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$DEADMAN_LOG"; }

# 텔레그램 자격증명은 감시자가 .env 에서 **독립적으로** 읽는다(앱 설정 로더 경유 금지).
read_env() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'
}

send_telegram() {
  local text="$1"
  local token chat
  token="$(read_env FCE_TELEGRAM_BOT_TOKEN)"; [[ -z "$token" ]] && token="$(read_env TELEGRAM_BOT_TOKEN)"
  chat="$(read_env FCE_TELEGRAM_CHAT_ID)";    [[ -z "$chat"  ]] && chat="$(read_env TELEGRAM_CHAT_ID)"
  if [[ -z "$token" || -z "$chat" ]]; then
    log "텔레그램 자격증명 없음 — 발송 불가: ${text:0:60}"
    return 1
  fi
  local code
  code=$(curl -s -m 15 -o /dev/null -w "%{http_code}" \
    -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    -d "chat_id=${chat}" -d "parse_mode=HTML" --data-urlencode "text=${text}")
  log "telegram HTTP $code :: ${text:0:80}"
  [[ "$code" == "200" ]]
}

# state: "<status>|<last_notified_epoch>|<last_selfcheck_epoch>"
read_state() {
  if [[ -f "$STATE_FILE" ]]; then cat "$STATE_FILE"; else echo "ok|0|0"; fi
}
write_state() { echo "$1|$2|$3" > "$STATE_FILE"; }

now_epoch=$(date +%s)
IFS='|' read -r prev_status last_notified last_selfcheck <<< "$(read_state)"
last_notified=${last_notified:-0}; last_selfcheck=${last_selfcheck:-0}

# ── 1. 하트비트 판독 ────────────────────────────────────────────────
reason=""
if [[ ! -f "$LIVENESS_FILE" ]]; then
  age=-1
  reason="하트비트 파일 없음 (워커가 한 번도 기록하지 않음)"
else
  written_at=$(python3 -c "
import json,sys
try:
    print(json.load(open('$LIVENESS_FILE')).get('written_at',''))
except Exception:
    print('')
" 2>/dev/null)
  if [[ -z "$written_at" ]]; then
    age=-1
    reason="하트비트 파일 손상"
  else
    hb_epoch=$(python3 -c "
from datetime import datetime
try:
    print(int(datetime.fromisoformat('$written_at'.replace('Z','+00:00')).timestamp()))
except Exception:
    print(0)
" 2>/dev/null)
    age=$(( now_epoch - ${hb_epoch:-0} ))
    (( age > STALE_LIMIT )) && reason="하트비트 ${age}초 경과 (허용 ${STALE_LIMIT}초)"
  fi
fi

# 프로세스 생존 여부(부가 정보)
if lsof -ti :8875 -sTCP:LISTEN >/dev/null 2>&1; then proc="8875 리스닝 중"; else proc="8875 응답 없음"; fi
restarts_24h=$(grep -c "down → restart" "$LOG_DIR/supervisor.log" 2>/dev/null || echo 0)

# ── 2. 사망 판정 / 복구 판정 ────────────────────────────────────────
if [[ -n "$reason" ]]; then
  if [[ "$prev_status" == "dead" ]] && (( now_epoch - last_notified < REMIND_EVERY )); then
    exit 0   # 스팸 억제: 리마인더 주기 전이면 침묵
  fi
  text="🚨 <b>엔진 사망 감지 (외부 감시자)</b>
사유: ${reason}
프로세스: ${proc}
최근 재시작: ${restarts_24h}회
감시자: deadman.sh (워커 외부)
→ 워커가 하트비트를 갱신하지 못하고 있습니다. 프로세스·로그를 확인하세요."
  send_telegram "$text" && write_state "dead" "$now_epoch" "$last_selfcheck" || write_state "dead" "$last_notified" "$last_selfcheck"
  exit 0
fi

if [[ "$prev_status" == "dead" ]]; then
  send_telegram "✅ <b>엔진 복구</b>
하트비트가 다시 갱신되고 있습니다 (경과 ${age}초).
프로세스: ${proc}"
  write_state "ok" "$now_epoch" "$last_selfcheck"
  exit 0
fi

# ── 3. 감시자 자가 점검(주 1회) — 감시자가 살아있음을 스스로 증명 ────
if (( now_epoch - last_selfcheck > SELFCHECK_EVERY )); then
  stale_tracks=$(python3 -c "
import json
try:
    d=json.load(open('$LIVENESS_FILE')); t=d.get('stale_tracks') or []
    print(', '.join(t) if t else '없음')
except Exception:
    print('판독 실패')
" 2>/dev/null)
  send_telegram "🩺 <b>외부 감시자 자가 점검</b>
데드맨 스위치 정상 작동 중입니다.
하트비트 경과: ${age}초 · 정지 트랙: ${stale_tracks}
최근 재시작: ${restarts_24h}회"
  write_state "ok" "$last_notified" "$now_epoch"
  exit 0
fi

write_state "ok" "$last_notified" "$last_selfcheck"
exit 0
