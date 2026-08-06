#!/usr/bin/env bash
# WO-FCE-SAMPLE-VIABILITY-01 PHASE 5 — 호스트 리소스 실측.
#
# 왜 이 스크립트가 있나: VPS 이전(안 C)을 검토할 때 **필요 사양을 추측으로 산정하지 않기**
# 위해서다. "2vCPU/2GB면 되겠지"는 근거가 아니다. 실제로 이 엔진이 쓰는 CPU·메모리·디스크·
# 네트워크를 재서, 그 수치로 사양을 고른다.
#
# 사용:
#   scripts/local/measure-resources.sh            # 기본 60회 × 10초 = 10분
#   FCE_SAMPLES=360 FCE_INTERVAL=10 scripts/local/measure-resources.sh   # 1시간
#
# 출력: logs/resource-usage.jsonl (샘플별 1줄) + 표준출력 요약.
# 결정은 사람이 한다 — 이 스크립트는 비교표의 빈칸을 채울 뿐이다.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
OUT="$LOG_DIR/resource-usage.jsonl"
SAMPLES="${FCE_SAMPLES:-60}"
INTERVAL="${FCE_INTERVAL:-10}"
mkdir -p "$LOG_DIR"

# 대상: 백엔드(uvicorn/워커) 프로세스. 포트로 찾는다 — 프로세스 이름은 실행 방식마다 다르다.
target_pids() {
  lsof -ti :8875 -sTCP:LISTEN 2>/dev/null
}

# 네트워크 누적 바이트. macOS 는 netstat -ib, 리눅스는 /proc/net/dev.
net_bytes() {
  if [[ "$(uname)" == "Darwin" ]]; then
    netstat -ib 2>/dev/null | awk '$1 !~ /^lo/ && $7 ~ /^[0-9]+$/ {rx+=$7; tx+=$10} END {print rx+0, tx+0}'
  else
    awk 'NR>2 && $1 !~ /^lo:/ {gsub(/:/,"",$1); rx+=$2; tx+=$10} END {print rx+0, tx+0}' /proc/net/dev
  fi
}

read -r NET_RX_START NET_TX_START <<<"$(net_bytes)"
START_EPOCH="$(date +%s)"
echo "측정 시작: ${SAMPLES}샘플 × ${INTERVAL}초 = $((SAMPLES * INTERVAL))초 · 출력 $OUT"

for ((i = 0; i < SAMPLES; i++)); do
  pids="$(target_pids | tr '\n' ',' | sed 's/,$//')"
  if [[ -n "$pids" ]]; then
    # ps 의 %cpu 는 코어 합산이라 100%를 넘을 수 있다. rss 는 KB.
    read -r cpu rss <<<"$(ps -o %cpu=,rss= -p "$pids" 2>/dev/null | awk '{cpu+=$1; rss+=$2} END {print cpu+0, rss+0}')"
  else
    cpu=0
    rss=0
  fi
  db_bytes="$(find "$HOME" -maxdepth 2 -name 'fomo_control_engine.db' -type f -exec wc -c {} + 2>/dev/null | awk 'NR==1 {print $1+0}')"
  printf '{"at":"%s","cpu_pct":%s,"rss_kb":%s,"db_bytes":%s}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${cpu:-0}" "${rss:-0}" "${db_bytes:-0}" >>"$OUT"
  sleep "$INTERVAL"
done

read -r NET_RX_END NET_TX_END <<<"$(net_bytes)"
ELAPSED=$(($(date +%s) - START_EPOCH))

awk -v elapsed="$ELAPSED" -v rx=$((NET_RX_END - NET_RX_START)) -v tx=$((NET_TX_END - NET_TX_START)) -v n="$SAMPLES" '
  BEGIN { max_cpu=0; max_rss=0 }
  { sum_cpu+=$1; sum_rss+=$2; if ($1>max_cpu) max_cpu=$1; if ($2>max_rss) max_rss=$2; c++ }
  END {
    if (c==0) { print "샘플 없음 — 백엔드가 8875 에서 실행 중인지 확인"; exit 1 }
    printf "\n측정 구간 %d초 (%d샘플)\n", elapsed, c
    printf "CPU  평균 %.1f%% · 최대 %.1f%%  (코어 합산)\n", sum_cpu/c, max_cpu
    printf "RSS  평균 %.0f MB · 최대 %.0f MB\n", sum_rss/c/1024, max_rss/1024
    printf "네트워크 수신 %.1f MB · 송신 %.1f MB (%.2f MB/시간 수신)\n", rx/1048576, tx/1048576, rx/1048576/(elapsed/3600)
    print  "\n※ 호스트 전체가 아니라 8875 백엔드 프로세스 기준이다. 브라우저·프론트는 포함하지 않는다."
    print  "※ 이 수치로 VPS 사양을 고른다. 추측하지 않는다 (PHASE 5)."
  }
' < <(awk -F'[:,]' '{for (i=1;i<=NF;i++) {if ($i ~ /"cpu_pct"/) c=$(i+1); if ($i ~ /"rss_kb"/) r=$(i+1)} print c, r}' "$OUT")
