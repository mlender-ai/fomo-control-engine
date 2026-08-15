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

# WO-FCE-LIVENESS-VERDICT-01: 문턱·집계 규약은 공용 파일 하나에만 둔다.
# 두 스크립트에 따로 두면 갈라진다 — 실제로 갈라져 있었다(A4).
# shellcheck source=lib/liveness.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/liveness.sh"

# 하트비트가 이 시간(초)을 넘겨 낡으면 사망으로 판정. 워커 기대 주기(300s)의 3배.
# **올리지 않는다**(C1) — 900을 늘리면 사망 판정이 줄 뿐 매달림은 그대로다.
STALE_LIMIT="$FCE_STALE_LIMIT"
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

# 사용자 지시(2026-08-16): 생존·사망·복구 푸시를 보내지 않는다.
#   "하트비트 죽는거 그만 보고해. 살아있다는것도 보고하지마. 그게 아무런 도움이 안 돼."
# 타당하다 — 죽었다는 알림을 받아도 사용자가 할 일이 없고(supervisor 가 자동 재시작한다),
# 살아있다는 알림은 정보가 0이다. 실측 heartbeat_stale 156회 중 사용자 조치로 이어진 것은 없다.
#
# **감시는 그대로 돈다.** 판정·재시작·기록 전부 유지되고 발송만 로그로 돌린다(강등이지 삭제가 아님).
# 조회: logs/deadman.log · logs/supervisor.log · GET /api/system/paper/diagnosis
# 되돌리려면 FCE_DEADMAN_PUSH=1 로 실행한다.
DEADMAN_PUSH="${FCE_DEADMAN_PUSH:-0}"

send_telegram() {
  local text="$1"
  local token chat
  if [[ "$DEADMAN_PUSH" != "1" ]]; then
    log "생존 알림 미발송(푸시 강등) — 내용은 아래에 기록만 한다:"
    printf '%s\n' "$text" >> "$DEADMAN_LOG"
    return 0
  fi
  token="$(read_env FCE_TELEGRAM_BOT_TOKEN)"; [[ -z "$token" ]] && token="$(read_env TELEGRAM_BOT_TOKEN)"
  chat="$(read_env FCE_TELEGRAM_CHAT_ID)";    [[ -z "$chat"  ]] && chat="$(read_env TELEGRAM_CHAT_ID)"
  if [[ -z "$token" || -z "$chat" ]]; then
    # 바이트 단위 절단(${text:0:60})은 UTF-8 문자를 반으로 잘라 로그를 깨뜨렸다.
    # 발송 실패는 **전문을 남긴다** — 못 보낸 내용을 여기서 못 보면 어디서도 못 본다(C5).
    log "텔레그램 자격증명 없음 — 발송 불가:"
    printf '%s\n' "$text" >> "$DEADMAN_LOG"
    return 1
  fi
  local code
  code=$(curl -s -m 15 -o /dev/null -w "%{http_code}" \
    -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    -d "chat_id=${chat}" -d "parse_mode=HTML" --data-urlencode "text=${text}")
  # 성공 경로는 첫 줄만 — 절단 대신 줄 단위로 자른다(문자 경계가 깨지지 않는다).
  log "telegram HTTP $code :: ${text%%$'\n'*}"
  [[ "$code" == "200" ]]
}

# state: "<status>|<last_notified_epoch>|<last_selfcheck_epoch>|<fresh_since_epoch>"
#
# `fresh_since` 는 하트비트가 **복구 문턱 이내로 들어온 시각**이다. 복구는 이 시각으로부터
# 지속 조건을 만족해야 선언된다 — 단발 갱신 하나로는 복구가 아니다(A1).
# 4번째 필드가 없는 구 상태 파일도 그대로 읽힌다(0으로 취급).
read_state() {
  if [[ -f "$STATE_FILE" ]]; then cat "$STATE_FILE"; else echo "ok|0|0|0"; fi
}
write_state() { echo "$1|$2|$3|${4:-0}" > "$STATE_FILE"; }

now_epoch=$(date +%s)
IFS='|' read -r prev_status last_notified last_selfcheck fresh_since <<< "$(read_state)"
last_notified=${last_notified:-0}; last_selfcheck=${last_selfcheck:-0}; fresh_since=${fresh_since:-0}

# supervisor 가 "자동 복구 포기"를 알릴 때 쓰는 강제 메시지 경로(C5).
#
# A5: 예전에는 상태 파일을 **읽지도 쓰지도 않고** 빠져나갔다. 그래서 `last_notified` 가
# 갱신되지 않았고, 60초 뒤 정규 틱의 `사망 감지` 가 리마인더 억제를 그대로 통과했다 —
# 16:20 에 두 메시지가 같은 분에 도착한 원인이다. 이제 같은 상태 규약을 쓴다.
if [ -n "${FCE_DEADMAN_FORCE_MESSAGE:-}" ]; then
  if send_telegram "$FCE_DEADMAN_FORCE_MESSAGE"; then
    write_state "dead" "$now_epoch" "$last_selfcheck" "$fresh_since"
  else
    log "포기 메시지 발송 실패 — 상태 유지(재시도 대상)"
  fi
  exit 0
fi

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
# A3·A4·A8: 예전에는 supervisor.log 에서 재시작 문자열을 grep 으로 셌다. 그 문자열은 포트 다운
# 경로에만 있어 **매달림 재시작을 한 건도 세지 않았고**, 창도 없어 파일 전체를 셌으며,
# 0건일 때 `grep -c` 가 exit 1 이라 `|| echo 0` 이 겹쳐 "0 0회"로 표시됐다.
# 이제 원장(`restarts.jsonl`) 하나만 보고, 사유·대상·창을 문구에 박는다.
restart_line="$(liveness_restart_line "$LOG_DIR/restarts.jsonl")"

# ── 2. 사망 판정 / 복구 판정 ────────────────────────────────────────
if [[ -n "$reason" ]]; then
  if [[ "$prev_status" == "dead" ]] && (( now_epoch - last_notified < REMIND_EVERY )); then
    exit 0   # 스팸 억제: 리마인더 주기 전이면 침묵
  fi
  text="🚨 <b>엔진 사망 감지 (외부 감시자)</b>
사유: ${reason}
프로세스: ${proc}
${restart_line}
감시자: deadman.sh (워커 외부)
→ 워커가 하트비트를 갱신하지 못하고 있습니다. 프로세스·로그를 확인하세요."
  # 사망 구간에서는 신선 지속을 처음부터 다시 센다.
  send_telegram "$text" && write_state "dead" "$now_epoch" "$last_selfcheck" 0 || write_state "dead" "$last_notified" "$last_selfcheck" 0
  exit 0
fi

# ── 2-2. 신선 지속 추적 (A1) ────────────────────────────────────────
#
# 사망 문턱(900) 아래라고 해서 살아난 것이 아니다. **복구 문턱(300) 이내**여야 신선이고,
# 그 신선이 지속(180초)돼야 복구다. 709초는 900 아래지만 300 위이므로 지속이 시작조차 되지 않는다.
if liveness_is_fresh "$age"; then
  (( fresh_since == 0 )) && fresh_since=$now_epoch
else
  fresh_since=0
fi

if [[ "$prev_status" == "dead" ]]; then
  if ! liveness_recovery_confirmed "$fresh_since" "$now_epoch"; then
    # C5 침묵 금지: 알림을 새로 만들지 않되, 중간 상태를 로그로 관측 가능하게 남긴다.
    if (( fresh_since > 0 )); then
      log "복구 대기 — 하트비트 ${age}초(신선), 지속 $((now_epoch - fresh_since))/${FCE_RECOVERY_SUSTAIN_SECONDS}초"
    else
      log "복구 미달 — 하트비트 ${age}초 (복구 문턱 ${FCE_FRESH_LIMIT}초 초과, 사망 문턱 ${STALE_LIMIT}초 이내)"
    fi
    write_state "dead" "$last_notified" "$last_selfcheck" "$fresh_since"
    exit 0
  fi
  # A6: 복구도 사망과 대칭으로 발송 실패를 처리한다. 예전에는 성공 여부를 보지 않고 상태를
  # ok 로 넘겨, curl 이 죽으면 복구 알림이 영영 오지 않았다.
  if send_telegram "✅ <b>엔진 복구</b>
하트비트가 ${FCE_RECOVERY_SUSTAIN_SECONDS}초 이상 신선하게 유지되고 있습니다 (경과 ${age}초 · 복구 문턱 ${FCE_FRESH_LIMIT}초).
프로세스: ${proc}
${restart_line}"; then
    write_state "ok" "$now_epoch" "$last_selfcheck" "$fresh_since"
  else
    log "복구 알림 발송 실패 — 상태를 dead 로 유지해 재시도한다"
    write_state "dead" "$last_notified" "$last_selfcheck" "$fresh_since"
  fi
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
${restart_line}"
  write_state "ok" "$last_notified" "$now_epoch" "$fresh_since"
  exit 0
fi

write_state "ok" "$last_notified" "$last_selfcheck" "$fresh_since"
exit 0
