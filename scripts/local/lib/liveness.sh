#!/usr/bin/env bash
# WO-FCE-LIVENESS-VERDICT-01 — 생존 판정 공용 규약.
#
# 왜 파일을 나눴나: 사망 문턱·복구 문턱·재시작 집계가 `deadman.sh` 와 `supervisor.sh` 에
# **따로 살아 있었고, 서로 다른 것을 세고 있었다.** 알림에 뜬 "최근 재시작 27회"와 포기 판정의
# "최근 1시간 3회"는 소스도 필터도 창도 전부 달랐다. 한 곳에 두면 갈라질 수 없다.
#
# 의존성: bash, python3(JSON 1회). 앱 코드 import 없음 — 감시자는 감시 대상 밖에 산다(C2).

# ── 문턱 2종 ────────────────────────────────────────────────────────────
#
# 사망과 복구가 **같은 문턱**을 쓰면 그 근처에서 진동한다. 실측 2026-08-13 17:04,
# 709초 묵은 하트비트가 "다시 갱신되고 있습니다"로 발송됐다 — 900을 넘지 않았다는 이유만으로.
#
#   사망:  age >  FCE_STALE_LIMIT        (900초 = 워커 기대 주기 300초의 3배)
#   복구:  age <= FCE_FRESH_LIMIT  가  FCE_RECOVERY_SUSTAIN_SECONDS 동안 지속
#
# ⚠️ `FCE_STALE_LIMIT` 은 **올리지 않는다**(C1). 900을 늘리면 사망 판정이 줄어들 뿐 매달림은
# 그대로다 — 그건 수리가 아니라 은폐다. 이 파일의 변경 방향은 복구를 **더 늦게** 선언하는 쪽뿐이다.
FCE_STALE_LIMIT="${FCE_DEADMAN_STALE_SECONDS:-900}"

# 복구 문턱. 워커 기대 하트비트 주기 **1주기**(300초) — "살아 있다"고 말하려면 최소한 한 주기
# 안에 찍혀 있어야 한다. 사망 문턱(900)의 1/3이라 그 사이 600초 구간이 완충대가 된다.
FCE_FRESH_LIMIT="${FCE_LIVENESS_FRESH_SECONDS:-300}"

# 복구 선언에 요구하는 **지속 시간**. 단발 하트비트 하나로 복구를 선언하지 않는다.
# 180초 = deadman 틱(60초) 3회분. 시간 기준이라 감시 루프 주기가 달라도 같은 의미다
# (supervisor 는 15초 주기 → 12틱, deadman 은 60초 주기 → 3틱).
FCE_RECOVERY_SUSTAIN_SECONDS="${FCE_LIVENESS_RECOVERY_SUSTAIN_SECONDS:-180}"

# 재시작 집계 창(시간). 변수명과 실제 창을 일치시킨다 — 예전 `restarts_24h` 는 이름만 24h 이고
# 실제로는 로그 파일 전체를 셌다(A4).
FCE_RESTART_WINDOW_HOURS="${FCE_LIVENESS_RESTART_WINDOW_HOURS:-24}"

# ── 하트비트 판독 ───────────────────────────────────────────────────────

# liveness_heartbeat_age <liveness.json> → 경과 초. 파일 없음/손상이면 -1.
liveness_heartbeat_age() {
  [ -f "$1" ] || { echo -1; return; }
  python3 - "$1" <<'PYEOF' 2>/dev/null || echo -1
import json, sys
from datetime import datetime, timezone
try:
    written = json.load(open(sys.argv[1]))["written_at"]
    stamp = datetime.fromisoformat(str(written).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    print(int((datetime.now(timezone.utc) - stamp).total_seconds()))
except Exception:
    print(-1)
PYEOF
}

# liveness_is_fresh <age> → 복구 문턱 이내인가. 사망 문턱이 아니라 **복구 문턱**을 본다.
liveness_is_fresh() {
  local age="${1:--1}"
  [ "$age" -ge 0 ] 2>/dev/null || return 1
  [ "$age" -le "$FCE_FRESH_LIMIT" ] 2>/dev/null
}

# liveness_is_stale <age> → 사망 문턱 초과인가.
liveness_is_stale() {
  local age="${1:--1}"
  [ "$age" -ge 0 ] 2>/dev/null || return 1
  [ "$age" -gt "$FCE_STALE_LIMIT" ] 2>/dev/null
}

# liveness_recovery_confirmed <fresh_since_epoch> <now_epoch> → 지속 조건을 만족했는가.
# `fresh_since` 가 0이면 아직 신선 구간에 들어오지도 않은 것이다.
liveness_recovery_confirmed() {
  local since="${1:-0}" now="${2:-0}"
  [ "$since" -gt 0 ] 2>/dev/null || return 1
  [ $((now - since)) -ge "$FCE_RECOVERY_SUSTAIN_SECONDS" ] 2>/dev/null
}

# ── 재시작 집계 — 원장 하나만 본다 (A3·A4) ──────────────────────────────
#
# 예전에는 `grep -c "down → restart" supervisor.log` 였다. 그 문자열은 **포트 다운 경로에만**
# 있고 매달림 재시작(`heartbeat stale ... → 재시작`)에는 없다. 그래서 카운터가 매달림 재시작을
# **한 건도 세지 않았고**, 27회에서 8시간 동안 얼어 있었다.
#
# 로그 문자열 파싱은 문자열이 바뀌면 조용히 깨진다. 그래서 원장(`restarts.jsonl`)만 본다 —
# `record_restart` 가 모든 재시작 경로에서 쓰는 유일한 기록처다.

# liveness_restart_counts <restarts.jsonl> [window_hours]
#   → "total heartbeat_stale port_down backend frontend"
liveness_restart_counts() {
  local ledger="$1" hours="${2:-$FCE_RESTART_WINDOW_HOURS}"
  [ -f "$ledger" ] || { echo "0 0 0 0 0"; return; }
  python3 - "$ledger" "$hours" <<'PYEOF' 2>/dev/null || echo "0 0 0 0 0"
import json, sys
from datetime import datetime, timedelta, timezone

path, hours = sys.argv[1], float(sys.argv[2])
cut = datetime.now(timezone.utc) - timedelta(hours=hours)
total = hb = port = backend = frontend = 0
try:
    for line in open(path).read().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("at")).replace("Z", "+00:00"))
        except Exception:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp < cut:
            continue
        total += 1
        reason = str(row.get("reason") or "")
        target = str(row.get("target") or "")
        if reason == "heartbeat_stale":
            hb += 1
        elif reason == "port_down":
            port += 1
        if target.startswith("backend"):
            backend += 1
        elif target.startswith("frontend"):
            frontend += 1
except OSError:
    pass
print(total, hb, port, backend, frontend)
PYEOF
}

# liveness_restart_line <restarts.jsonl> [window_hours]
#   → 알림 한 줄. **무엇을 어느 창에서 세는지 문구에 박는다**(C8) — 숫자만 있으면 다음 사람이
#     또 다른 의미로 읽는다.
liveness_restart_line() {
  local ledger="$1" hours="${2:-$FCE_RESTART_WINDOW_HOURS}"
  local total hb port backend frontend
  read -r total hb port backend frontend <<< "$(liveness_restart_counts "$ledger" "$hours")"
  echo "최근 ${hours}h 재시작 ${total}회 (매달림 ${hb} · 포트다운 ${port} / backend ${backend} · frontend ${frontend})"
}

# liveness_restarts_in_window <restarts.jsonl> <reason> <window_hours> → 사유별 건수.
# 포기 판정(1시간 3회 상한)이 쓰는 경로. 알림 카운터와 **같은 원장**을 본다(A4).
liveness_restarts_in_window() {
  local ledger="$1" reason="$2" hours="${3:-1}"
  local total hb port _b _f
  read -r total hb port _b _f <<< "$(liveness_restart_counts "$ledger" "$hours")"
  case "$reason" in
    heartbeat_stale) echo "$hb" ;;
    port_down) echo "$port" ;;
    *) echo "$total" ;;
  esac
}
