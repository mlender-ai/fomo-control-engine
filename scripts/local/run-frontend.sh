#!/usr/bin/env bash
# FCE 로컬 프론트(8876) 실행 래퍼 — launchd 가 KeepAlive 로 감시.
# ⚠️ AGENTS.md 불변 규칙 4: 여기서 절대 build 하지 않는다. 이미 만들어진 .next 를 serve 만.
#    빌드는 사람이 서버 중지 후 `npm run build`로 별도 수행한다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_BIN="/Users/cocteau/.nvm/versions/node/v24.11.1/bin"
export PATH="$NODE_BIN:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$REPO_DIR/dashboard"

# 빌드 산출물이 없으면 serve 불가 — thrash 재시작 방지 위해 명확히 실패.
if [[ ! -f .next/BUILD_ID ]]; then
  echo "[run-frontend] .next/BUILD_ID 없음 — 먼저 'npm run build' 필요. 종료." >&2
  exit 1
fi

exec npm run start:local
