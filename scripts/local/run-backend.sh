#!/usr/bin/env bash
# FCE 로컬 백엔드(8875) 실행 래퍼 — launchd 가 KeepAlive 로 감시.
# launchd 는 최소 PATH 만 주므로 여기서 런타임 경로를 명시한다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$REPO_DIR/backend"   # .env·상대 DB 경로가 cwd 기준이라 필수

exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875
