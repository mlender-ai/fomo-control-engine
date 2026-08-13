#!/usr/bin/env bash
# WO-FCE-WORKER-HANG-02 Phase 0 — 매달림 증거 자동 포착.
#
# ## 왜 이 스크립트가 kill -9 **앞에** 있어야 하는가 (D5)
#
# supervisor 는 매달림을 감지한 직후 곧바로 `kill -9` 한다. 감지와 파괴 사이에 아무것도
# 없었기 때문에 수 주째(실측 101회) 원인을 못 잡았다. 증상이 잡힐 때마다 증거가 함께 사라졌다.
# 이 스크립트가 그 사이에 들어간다. **포착이 파괴에 선행한다**(C2).
#
# ## C3 — 포착이 사고를 만들지 않는다
#
# 전 단계에 타임아웃이 걸려 있고 모든 실패를 삼킨다. 덤프가 실패하거나 느려도
# 재시작은 그대로 진행된다. 감시자가 새로운 장애 원인이 되는 것을 금지한다.
# 이 스크립트는 **절대 0이 아닌 코드로 종료되지 않는다**(`exit 0` 고정).
#
# ## 도구 선택 (실측 2026-08-14)
#
#   py-spy dump  → macOS 에서 root 필요("This program requires root on OSX"). 쓸 수 없다.
#   sample <pid> → sudo 불필요하나 C 프레임까지만(_PyEval_EvalFrameDefault). 보조로 쓴다.
#   faulthandler → SIGUSR1 로 **파이썬 파일:라인**을 얻는다. 루프가 막혀도 C 시그널
#                  핸들러가 프레임을 직접 순회하므로 이 사건에 정확히 맞는다. 1순위.
#
# 사용: capture-hang.sh <pid> <log_dir> [reason]
set -uo pipefail

PID="${1:-}"
LOG_DIR="${2:-}"
REASON="${3:-heartbeat_stale}"
[ -z "$PID" ] || [ -z "$LOG_DIR" ] && exit 0

DUMP_DIR="$LOG_DIR/hang-dumps"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
OUT="$DUMP_DIR/${STAMP}-${PID}.txt"
# 전체 예산. 이 시간을 넘기면 포착을 포기하고 재시작을 진행한다.
BUDGET="${FCE_HANG_DUMP_BUDGET_SECONDS:-10}"
# 보존 개수. DB 12.8GB 비대 선례가 있어 관측물도 반드시 상한을 둔다.
KEEP="${FCE_HANG_DUMP_KEEP:-40}"

mkdir -p "$DUMP_DIR" 2>/dev/null || exit 0
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_DIR/supervisor.log" 2>/dev/null; }

{
  echo "=== FCE hang capture ==="
  echo "captured_at_utc: $STAMP"
  echo "pid: $PID"
  echo "reason: $REASON"
  echo
  echo "--- heartbeat ---"
  python3 - "$LOG_DIR/liveness.json" <<'PYEOF' 2>/dev/null || echo "heartbeat 판독 실패"
import json, sys
from datetime import datetime, timezone
try:
    data = json.load(open(sys.argv[1]))
    written = datetime.fromisoformat(str(data["written_at"]).replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - written).total_seconds()
    print(f"written_at: {data['written_at']}")
    print(f"age_seconds: {int(age)}")
    print(f"pid_in_file: {data.get('pid')}  source: {data.get('source')}")
except Exception as exc:
    print(f"heartbeat read error: {exc}")
PYEOF
  echo
  echo "--- process ---"
  ps -o pid,%cpu,%mem,etime,state,wchan= -p "$PID" 2>/dev/null || echo "ps 실패"
  echo "open_files: $(lsof -p "$PID" 2>/dev/null | wc -l | tr -d ' ')"
  echo "threads: $(ps -M "$PID" 2>/dev/null | wc -l | tr -d ' ')"
  echo
  echo "--- sqlite lock ---"
  ls -la "$LOG_DIR"/../backend/*.db-wal "$LOG_DIR"/../backend/*.db-shm 2>/dev/null || echo "wal/shm 없음"
  echo
  echo "--- last jobs (job-trace.jsonl tail) ---"
  tail -25 "$LOG_DIR/job-trace.jsonl" 2>/dev/null || echo "job-trace 없음"
  echo
  echo "--- loop lag (tail) ---"
  tail -10 "$LOG_DIR/loop-lag.jsonl" 2>/dev/null || echo "loop-lag 기록 없음(=지연 임계 초과 없음)"
} > "$OUT" 2>&1

# ── 1순위: faulthandler(SIGUSR1) — 파이썬 파일:라인 ────────────────────
#
# ⚠️ SIGUSR1 의 **기본 동작은 프로세스 종료**다. faulthandler 를 등록하지 않은 프로세스에
# 보내면 그 자리에서 죽는다(회귀 테스트에서 실제로 returncode -30 으로 확인됐다).
# 따라서 **하트비트 파일이 자기 pid 라고 밝힌 워커에게만** 보낸다 — pid 재사용이나
# 오인(프론트 8876 등)으로 엉뚱한 프로세스를 죽이면 포착이 파괴가 된다(C3).
HB_PID=$(python3 - "$LOG_DIR/liveness.json" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("pid") or "")
except Exception:
    print("")
PYEOF
)
BEFORE=$(wc -c < "$DUMP_DIR/faulthandler.log" 2>/dev/null || echo 0)
if [ "$HB_PID" != "$PID" ]; then
  {
    echo
    echo "--- python stack: SIGUSR1 생략 ---"
    echo "대상 pid=$PID 이 하트비트가 기록한 워커 pid=${HB_PID:-없음} 와 다릅니다."
    echo "핸들러 미등록 프로세스에 SIGUSR1 을 보내면 기본 동작으로 죽으므로 보내지 않았습니다(C3)."
  } >> "$OUT" 2>&1
  log "hang capture: SIGUSR1 생략(pid 불일치 target=$PID heartbeat=${HB_PID:-none})"
elif kill -USR1 "$PID" 2>/dev/null; then
  sleep 1
  AFTER=$(wc -c < "$DUMP_DIR/faulthandler.log" 2>/dev/null || echo 0)
  if [ "$AFTER" -gt "$BEFORE" ]; then
    {
      echo
      echo "--- python stack (faulthandler / SIGUSR1) ---"
      tail -c $((AFTER - BEFORE)) "$DUMP_DIR/faulthandler.log"
    } >> "$OUT" 2>&1
    log "hang capture: faulthandler 덤프 성공 → $OUT"
  else
    echo -e "\n--- python stack: SIGUSR1 후에도 기록 없음(핸들러 미등록 또는 프로세스 무응답) ---" >> "$OUT"
    log "hang capture: faulthandler 무응답(등록 여부 확인 필요)"
  fi
else
  echo -e "\n--- python stack: SIGUSR1 전송 실패(프로세스 없음?) ---" >> "$OUT"
  log "hang capture: SIGUSR1 전송 실패 pid=$PID"
fi

# ── 2순위: sample — 네이티브 프레임(어떤 C 호출에 갇혔는지) ────────────
SAMPLE_BIN="$(command -v sample 2>/dev/null || true)"
if [ -n "$SAMPLE_BIN" ]; then
  TMP="$DUMP_DIR/.sample-$PID.txt"
  # C3: 전체 예산 안에서만. 초과하면 포기하고 재시작을 막지 않는다.
  if timeout "$BUDGET" "$SAMPLE_BIN" "$PID" 2 -file "$TMP" >/dev/null 2>&1; then
    {
      echo
      echo "--- native stack (sample, 상위 60줄) ---"
      sed -n '1,60p' "$TMP"
    } >> "$OUT" 2>&1
  else
    echo -e "\n--- native stack: sample 타임아웃/실패(${BUDGET}s) — 재시작은 정상 진행 ---" >> "$OUT"
    log "hang capture: sample 타임아웃 ${BUDGET}s"
  fi
  rm -f "$TMP" 2>/dev/null
else
  echo -e "\n--- native stack: sample 없음 ---" >> "$OUT"
  log "hang capture: 덤프 도구 없음(sample 미설치)"
fi

# ── 보존 정책 — 관측물이 디스크를 먹지 않게 ─────────────────────────────
COUNT=$(ls -1 "$DUMP_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
if [ "${COUNT:-0}" -gt "$KEEP" ]; then
  ls -1t "$DUMP_DIR"/*.txt 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f 2>/dev/null
fi
# faulthandler 누적 로그도 상한을 둔다.
if [ -f "$DUMP_DIR/faulthandler.log" ] && [ "$(wc -c < "$DUMP_DIR/faulthandler.log")" -gt 20000000 ]; then
  tail -c 5000000 "$DUMP_DIR/faulthandler.log" > "$DUMP_DIR/faulthandler.log.tmp" 2>/dev/null &&
    mv "$DUMP_DIR/faulthandler.log.tmp" "$DUMP_DIR/faulthandler.log" 2>/dev/null
fi

log "hang capture 완료 → $OUT"
exit 0
