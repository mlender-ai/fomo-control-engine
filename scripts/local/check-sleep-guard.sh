#!/usr/bin/env bash
# WO-FCE-VALIDATION-VERDICT-01 Phase 2 — 절전 방지 자가 점검·적용.
#
# 왜: 맥 절전이 US 정규장 후반(02:00~05:00 KST)을 반복 삭제했다. 설정을 한 번 껐어도
# OS 업데이트·재부팅·배터리 전환이 되돌릴 수 있고, 그러면 **아무 소리 없이 손실이
# 다시 시작된다.** 조치를 적용했다는 사실보다 조치가 지금도 살아 있다는 사실이 중요하다.
#
# 사용:
#   scripts/local/check-sleep-guard.sh          # 점검만 (기본)
#   scripts/local/check-sleep-guard.sh --apply  # 점검 + 적용 (sudo·caffeinate)
#
# 종료 코드: 0 = 보호 중, 1 = 미적용(조치 필요), 2 = 판정 불가(macOS 아님/pmset 없음)
set -uo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

if [[ "$(uname)" != "Darwin" ]]; then
  echo "판정 불가: macOS 전용 점검입니다 (현재 $(uname))."
  exit 2
fi
if ! command -v pmset >/dev/null 2>&1; then
  echo "판정 불가: pmset 을 찾을 수 없습니다."
  exit 2
fi

if [[ "$APPLY" == "1" ]]; then
  echo "절전 방지 적용 중 (sudo 필요)..."
  sudo pmset -a sleep 0 disksleep 0 standby 0 powernap 0
  # caffeinate 가 이미 떠 있으면 중복 실행하지 않는다 — 프로세스만 늘고 효과는 같다.
  if pgrep -f "caffeinate -dimsu" >/dev/null 2>&1; then
    echo "caffeinate 이미 실행 중"
  else
    nohup caffeinate -dimsu >/dev/null 2>&1 &
    echo "caffeinate -dimsu 시작 (PID $!)"
  fi
  echo
fi

# 두 근거를 함께 본다: 설정이 꺼져 있어도 caffeinate 가 잡고 있으면 안 자고,
# caffeinate 가 죽어도 설정이 살아 있으면 안 잔다. 둘 다 없을 때만 위험이다.
SLEEPING_AXES=""
for key in sleep disksleep standby powernap; do
  value="$(pmset -g 2>/dev/null | awk -v k="$key" '$1 == k {print $2; exit}')"
  [[ -z "$value" ]] && continue
  if [[ "${value%%[!0-9]*}" != "0" ]]; then
    SLEEPING_AXES="$SLEEPING_AXES $key=$value"
  fi
done

CAFFEINATE=0
if pmset -g assertions 2>/dev/null | grep -qE "PreventUserIdleSystemSleep\s+[1-9]"; then
  CAFFEINATE=1
fi

echo "전원 설정 유휴 절전 켜진 항목:${SLEEPING_AXES:- 없음}"
echo "caffeinate wake lock: $([[ "$CAFFEINATE" == "1" ]] && echo 활성 || echo 없음)"

if [[ -z "$SLEEPING_AXES" || "$CAFFEINATE" == "1" ]]; then
  echo
  echo "✅ 절전 방지 적용 중 — 02:00~05:00 KST 구간 관측이 유지됩니다."
  exit 0
fi

cat <<'MSG'

⛔ 절전 방지가 적용돼 있지 않습니다.
   이 상태면 02:00~05:00 KST 가 다시 비고 US 정규장 후반이 유실됩니다.

   조치: scripts/local/check-sleep-guard.sh --apply
   또는: sudo pmset -a sleep 0 disksleep 0 standby 0 powernap 0
         nohup caffeinate -dimsu &

   ※ 재부팅 후에도 유지되는지 반드시 다시 확인하세요. 자동 복구가 안 되면
     "적용됐다"고 말할 수 없고, 그 경우 사고는 반드시 재발합니다.
MSG
exit 1
