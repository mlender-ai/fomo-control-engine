#!/usr/bin/env bash
# Claude Code PreToolUse hook — Edit/Write 시 민감 파일 접근 차단 (FCE판).
# fomo-club protect-secrets.sh 이식. AGENTS.md "Stop Prompting, Start Governing".

set -euo pipefail

INPUT=$(cat)

if command -v jq >/dev/null 2>&1; then
  TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
else
  TOOL=$(echo "$INPUT" | grep -oE '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
  FILE_PATH=$(echo "$INPUT" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
fi

if [[ "$TOOL" != "Edit" && "$TOOL" != "Write" && "$TOOL" != "NotebookEdit" ]]; then
  exit 0
fi
if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

BLOCK_REASONS=()

# .env 계열 (단 .env.example 허용)
if [[ "$FILE_PATH" =~ (^|/)\.env($|\.local$|\.production$|\.development$|\.staging$) ]]; then
  BLOCK_REASONS+=(".env 계열 환경 파일 (거래소 API 키 등 시크릿 노출 위험)")
fi

# 적용된 DB 마이그레이션은 immutable — 새 번호로 추가
if [[ "$FILE_PATH" =~ (^|/)backend/app/db/migrations/[0-9]{4}_.+\.sql$ ]]; then
  if [[ -f "$FILE_PATH" ]]; then
    BLOCK_REASONS+=("적용된 마이그레이션 SQL (immutable — 새 번호의 마이그레이션 파일로 추가)")
  fi
fi

# 키 파일
if [[ "$FILE_PATH" =~ \.(pem|key|p12|pfx|jks)$ ]]; then
  BLOCK_REASONS+=("암호화 키 파일")
fi

# 로컬 DB 파일 직접 편집 금지
if [[ "$FILE_PATH" =~ \.(db|db-shm|db-wal|sqlite3?)$ ]]; then
  BLOCK_REASONS+=("SQLite DB 파일 직접 편집 (repository/마이그레이션 경유)")
fi

if [[ ${#BLOCK_REASONS[@]} -gt 0 ]]; then
  echo "🛡️  protect-secrets hook: 보호된 파일에 대한 ${TOOL} 차단" >&2
  echo "  파일: $FILE_PATH" >&2
  for r in "${BLOCK_REASONS[@]}"; do
    echo "    - $r" >&2
  done
  echo "  대안: .env.example 갱신 / 새 마이그레이션 추가 / 시크릿은 사용자가 직접 관리" >&2
  exit 2
fi

exit 0
