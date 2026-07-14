# 품질 게이트 (Quality Gates)

> 모든 코드 변경은 push 전에 이 게이트를 **로컬에서** 통과해야 한다. CI(`.github/workflows/ci.yml`)는 같은 게이트를 원격에서 다시 강제한다.
> 앞 게이트 실패 시 뒷 게이트로 넘어가지 않는다. **실패한 채 push 금지.**

---

## Gate 1: 백엔드 (backend/ 변경 시)

```bash
cd backend
python3 -m ruff check .                     # 린트
python3 -m ruff format --check .            # 포맷 (실패 시: python3 -m ruff format .)
python3 scripts/check_import_cycles.py      # 임포트 순환
python3 scripts/check_mypy_baseline.py      # 타입 부채 래칫 (기준선 초과 금지)
FCE_MARKET_DATA_PROVIDER=mock python3 -m pytest --cov=app --cov-fail-under=70 -q -m "not live"
python3 scripts/check_quality_baseline.py   # 커버리지 코어 80%·예외 주석 래칫
```

## Gate 2: 프론트 (dashboard/ 변경 시)

```bash
cd dashboard
npm run lint        # ESLint (error 0)
npm run typecheck   # tsc --noEmit
npm run build       # Next.js 프로덕션 빌드
```

## Gate 3: E2E (플로우/화면 구조 변경 시 — 선택, CI는 항상)

```bash
cd dashboard && npm run test:e2e   # Playwright demo-mode 스모크·비주얼
```

## Gate 4: 도메인 불변 (트레이딩 안전 — 해당 영역 변경 시 자문)

체크리스트 — 하나라도 "예"면 머지 보류하고 재설계:
- [ ] 승격(candidate→validated)이 제안-거부권 없이 자동 적용되는 경로가 생겼나?
- [ ] 감지기/채점이 미확정 캔들·미래 데이터를 참조하나(룩어헤드)?
- [ ] 미검증 시그니처·고래가 방향 판정/자동 진입/컨플루언스에 섞였나?
- [ ] 실계좌 주문을 실행하는 코드가 생겼나? (페이퍼는 read-only)
- [ ] 표본 N·CI 없이 성적을 단정하는 화면/카피가 생겼나?
- [ ] 적용된 마이그레이션 SQL을 수정했나? (새 번호로 추가해야 함)

## Gate 5: 머지 확인 (세션 종료 전 — 불변 규칙 1)

```bash
git status --short                          # 비어야 함
git rev-list --count origin/main..main      # 0이어야 함
gh run list --branch main --limit 1         # 마지막 CI가 success인지
```

---

## 게이트 운용 원칙

- **재기준선은 내리기만**: mypy·예외주석 래칫 수치는 부채 상환으로만 갱신한다. 올려야 하는 상황 = 게이트가 죽어있었다는 뜻 — 원인부터 조사.
- **CI 생존 확인**: push 후 `gh run list`로 실제 실행됐는지 본다. 0초 실패 = 워크플로우 파일 자체가 깨진 것(2026-07-14 사건: YAML 오타로 5일간 전 푸시 무검증).
- **플레이키 금지**: 고정 sleep 기반 타이밍 테스트는 데드라인 폴링으로 작성한다(커버리지 계측·CI 부하에서 깨진다).
