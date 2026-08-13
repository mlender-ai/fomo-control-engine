#!/usr/bin/env bash
# WO-FCE-LIVENESS-VERDICT-01 Phase 0 — 로그 증거 수집 (읽기 전용).
#
# **아무것도 고치지 않는다.** 읽고 출력만 한다. 이 결과가 두 가지를 동시에 정한다:
#   ① H1/H2 판별 — 15:21 "8875 응답 없음" 이 매달림 재시작의 kill 창이었는가, 진짜 다운이었는가
#   ② 후속 WO(`WO-FCE-WORKER-HANG-02`) 착수 재료 — 하트비트 정체 시각의 워커 로그
#
# 호스트에서 실행:
#   bash scripts/local/collect-liveness-evidence.sh
#   bash scripts/local/collect-liveness-evidence.sh > ~/liveness-evidence.txt 2>&1
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="${FCE_LOG_DIR:-$REPO_DIR/logs}"

section() { printf '\n=== %s ===\n' "$1"; }

section "0. 로그 경로 확정 (§1 착수 전 확인)"
# ⚠️ 다른 파일을 보고 있으면 이 WO 의 모든 실측이 무효다.
echo "supervisor.sh:10 · deadman.sh:17 기준 경로 = $LOG_DIR"
ls -la "$LOG_DIR" 2>&1 | head -20
for candidate in "$HOME/fce-logs" "$HOME/fomo-control-engine/logs"; do
  if [ -e "$candidate" ]; then
    echo "--- $candidate"
    ls -ld "$candidate"
    # 심볼릭 링크면 같은 파일인지, 아니면 별도 사본인지 여기서 갈린다.
    [ -L "$candidate" ] && echo "    → 링크 대상: $(readlink "$candidate")"
    if [ -f "$candidate/supervisor.log" ] && [ -f "$LOG_DIR/supervisor.log" ]; then
      if [ "$candidate/supervisor.log" -ef "$LOG_DIR/supervisor.log" ]; then
        echo "    → supervisor.log 는 $LOG_DIR 와 **같은 파일**"
      else
        echo "    → ⚠️ supervisor.log 가 **다른 파일**이다 — 어느 쪽을 감시자가 쓰는지 확인 필요"
      fi
    fi
  fi
done

section "1. supervisor.log 마지막 50줄"
tail -50 "$LOG_DIR/supervisor.log" 2>&1

section "2. 매달림 재시작이 실제로 찍혔는가 — H1 vs H2 판별"
# H1: 존재 → 매달림 재시작이 돌고 있었고 카운터만 눈이 멀었던 것(A3)
# H2: 부재 → start_backend 도 안 떴다는 뜻이므로 신규 결함(A9) 등록 대상
printf 'heartbeat stale → 재시작 건수: '
grep -c 'heartbeat stale.*→ 재시작' "$LOG_DIR/supervisor.log" 2>/dev/null || echo 0
echo "--- 최근 30건 (heartbeat stale 전체: 재시작·포기 모두)"
grep 'heartbeat stale' "$LOG_DIR/supervisor.log" 2>/dev/null | tail -30

section "3. 자동 복구 포기만 반복되는가"
printf '자동 복구 포기 건수: '
grep -c '자동 복구 포기' "$LOG_DIR/supervisor.log" 2>/dev/null || echo 0
grep '자동 복구 포기' "$LOG_DIR/supervisor.log" 2>/dev/null | tail -10

section "4. 27 의 정체 — 포트 다운 재시작의 실제 시각·대상"
# 이 문자열만 예전 카운터가 셌다. frontend 재시작도 함께 셌다는 점에 주의.
printf 'down → restart 건수 (backend+frontend 합산): '
grep -c 'down → restart' "$LOG_DIR/supervisor.log" 2>/dev/null || echo 0
grep 'down → restart' "$LOG_DIR/supervisor.log" 2>/dev/null | tail -10

section "5. 원장 쪽 진실 — 사유별·대상별·시각별"
python3 - "$LOG_DIR/restarts.jsonl" <<'PYEOF' 2>&1
import collections, json, os, sys
from datetime import datetime, timedelta, timezone

path = sys.argv[1]
if not os.path.exists(path):
    print(f"{path} 없음 — record_restart 가 한 번도 호출되지 않았다는 뜻이다")
    raise SystemExit(0)
rows = []
for line in open(path).read().splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except json.JSONDecodeError:
        pass
print("총", len(rows), "건")
print("사유별:", dict(collections.Counter(str(r.get("reason") or "-") for r in rows)))
print("대상별:", dict(collections.Counter(str(r.get("target") or "-") for r in rows)))
now = datetime.now(timezone.utc)
for hours in (1, 24):
    cut = now - timedelta(hours=hours)
    recent = []
    for r in rows:
        try:
            stamp = datetime.fromisoformat(str(r.get("at")).replace("Z", "+00:00"))
        except Exception:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= cut:
            recent.append(r)
    print(f"최근 {hours}h:", len(recent), dict(collections.Counter(str(r.get('reason') or '-') for r in recent)))
print("--- 최근 15건")
for r in rows[-15:]:
    print(" ", r.get("at"), r.get("target"), r.get("reason"))
PYEOF

section "6. 이중 기동 여부 (A7 실증)"
# run-backend.sh 가 2개 이상이면 A7 이 실제로 발생한 것이다.
pgrep -fa 'supervisor.sh|run-backend.sh' 2>&1 || echo "(해당 프로세스 없음)"

section "7. 감시자 상태 파일"
echo "deadman.state = $(cat "$LOG_DIR/deadman.state" 2>/dev/null || echo '(없음)')"
echo "restart.lock  = $(cat "$LOG_DIR/restart.lock" 2>/dev/null || echo '(없음)')"
echo "--- liveness.json"
cat "$LOG_DIR/liveness.json" 2>/dev/null || echo "(없음)"

section "8. deadman.log 마지막 40줄"
tail -40 "$LOG_DIR/deadman.log" 2>&1

section "9. 후속 WO 재료 — 하트비트 정체 시각의 워커 로그"
# 정체 구간에서 **마지막으로 완료된 잡**이 무엇인지가 WORKER-HANG-02 의 출발점이다.
# 추측하지 않는다 — 파일:라인이 확정되기 전에는 수리하지 않는다.
tail -500 "$LOG_DIR/backend.log" 2>/dev/null | tail -120 || echo "(backend.log 없음)"

section "10. 정체 시각 분포 — 절전 구간(02:00~05:00 KST)과 겹치는가"
# 03:21·04:20·15:21·16:20 관측. 절전 구간이 아닌 건이 섞여 있으면 절전 대책만으로 해결되지 않는다.
grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}' "$LOG_DIR/supervisor.log" 2>/dev/null \
  | awk '{print $2}' | sort | uniq -c | sort -rn | head -24

printf '\n=== 수집 완료 ===\n'
echo "H1(매달림 재시작 존재) / H2(부재 → A9 신규 결함) 판별은 §2 결과로 한다."
