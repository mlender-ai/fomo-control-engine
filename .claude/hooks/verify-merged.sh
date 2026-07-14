#!/usr/bin/env bash
# Claude Code Stop hook — AGENTS.md 불변 규칙 1(무조건 머지)의 결정론적 강제.
# 미커밋 변경 또는 미푸시 커밋이 있으면 세션 종료를 막고 정리를 지시한다.
# 사용자 명시 보류 시: `touch .git/FCE_STOP_OK` 로 1회 통과(통과 시 자동 삭제).

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR" || exit 0

# git 저장소가 아니면 통과
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# 보류 승인 마커 — 1회용
if [[ -f .git/FCE_STOP_OK ]]; then
  rm -f .git/FCE_STOP_OK
  exit 0
fi

DIRTY=$(git status --porcelain 2>/dev/null | head -20)
AHEAD=0
if git rev-parse --verify origin/main >/dev/null 2>&1; then
  AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
fi

if [[ -z "$DIRTY" && "$AHEAD" == "0" ]]; then
  exit 0
fi

{
  echo "⛔ AGENTS.md 불변 규칙 1: 미머지 작업이 남아 있어 세션을 끝낼 수 없다."
  if [[ -n "$DIRTY" ]]; then
    echo "— 미커밋 변경:"
    echo "$DIRTY"
  fi
  if [[ "$AHEAD" != "0" ]]; then
    echo "— origin/main 대비 미푸시 커밋: ${AHEAD}개 (git push origin main)"
  fi
  echo "조치: HARNESS.md 게이트 통과 → 커밋 → push → CI 확인. 사용자가 명시적으로 보류를 지시한 경우에만 'touch .git/FCE_STOP_OK' 후 종료."
} >&2

exit 2
