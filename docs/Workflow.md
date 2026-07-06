# Workflow

## Principles

- Local is the workbench. Remote is the source of truth.
- Push at least once per working day when a WO is active.
- Start each new WO from a clean understanding of current remote state.
- CI must be green before merge.
- No real API keys are required or allowed in CI.

## Branches

Use WO-scoped branches:

```text
wo/fce-XX-short-description
```

Codex-local branches may use `codex/` while work is still exploratory, but PR branches should be renamed or pushed under the WO prefix.

## Before Starting A WO

1. Confirm current branch.
2. Confirm `git status`.
3. Pull/rebase from the remote base branch if needed.
4. Read `docs/Architecture.md` for file placement.
5. Identify whether the WO touches API contracts, DB schema, or frontend UI.

## Before Opening A PR

Run:

```bash
cd backend
python -m ruff check .
python -m ruff format --check .
python scripts/check_import_cycles.py
python scripts/check_mypy_baseline.py
python -m pytest --cov=app --cov-report=xml --cov-report=json:coverage.json --cov-fail-under=70
python scripts/check_quality_baseline.py

cd ../dashboard
npm run lint
npm run typecheck
npm run build
```

## CI Gate

The GitHub Actions gate runs three jobs:

- `backend`: ruff, format check, import cycle check, pytest, coverage baseline
- `backend` also runs a mypy baseline on `app/services`, `app/marketdata`, and `app/notify`; the current error count must not increase.
- `frontend`: eslint, TypeScript, Next build
- `e2e`: placeholder until WO-FCE-27 replaces it with demo-mode Playwright smoke tests

CI uses mock/local configuration only:

- `FCE_MARKET_DATA_PROVIDER=mock`
- no Bitget secrets
- no OpenAI key
- no Telegram token
- no Coinglass key

## Quality Baseline

`backend/quality-baseline.json` tracks:

- total coverage minimum
- combined core coverage minimum
- known modules below target
- exception comment count for `type: ignore`, `noqa`, `ts-ignore`, and `eslint-disable`
- mypy error count for the first typed backend surface

The exception count must not increase. Reduce it when touching related code.
