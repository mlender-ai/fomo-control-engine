# WO-FCE-NET-EDGE-01 — 페이퍼 순 우위 수리 (마찰·보상·체결)

우선순위: P0 — 재판정 N=427 에서 netR **−67.2R**. 비용이 gross 우위의 **2.9배**다.
선행: `WO-FCE-REPLAY-DEPTH-01`(재판정 하네스) · `WO-FCE-RISK-SIZING-01`(리스크 기준 사이징)

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` — `claude/paper-trading-performance-t58oul` 외 같은 주제 브랜치 없음
- [x] `git log --oneline -20` + `git status --short` — 기존 구현 없음(워킹트리 청결)
- [x] `grep -r "NET-EDGE" docs/ backend/ dashboard/` — 기존 산출물 없음

## 진단 (코드 확정)

```python
# app/paper/service.py::_paper_target_plan  (수리 전)
execution_risk = min(structural_risk, atr_value)          # 구조가 멀면 조용히 1 ATR 로 좁힌다
staged_reward  = tp1*0.5 + tp2*0.5 = atr * 1.5            # 보상은 ATR 에 고정
```

이 두 줄에서 세 결함이 **동시에** 나온다.

1. **RR 게이트가 항등식이다.** `structural_risk ≥ ATR` 이면 `RR = 1.5` — 산술적으로 항상.
   실측 20/24 건이 정확히 1.5000, `RR<1.5` 는 0건. 한 번도 거른 적이 없다.
2. **스톱이 구조가 아니라 변동성에 놓인다.** 노이즈에 털리고, 종가 체결과 겹쳐
   계획 −1.000R 이 실제 −1.559R 이 된다(초과분의 82%가 체결 비대칭).
3. **좁은 스톱이 마찰을 곱한다.** 리스크 기준 사이징에서 1R 금액이 예산 상수이므로
   `비용R = 왕복 비용률 / 스톱거리%`. 재판정 N=427 에서 비용 56.5R vs gross −10.8R.

근거 문서: [`validation/NET_EDGE.md`](validation/NET_EDGE.md) ·
[`validation/STOP_EXECUTION.md`](validation/STOP_EXECUTION.md) ·
[`validation/EXECUTION_MODEL.md`](validation/EXECUTION_MODEL.md)

## 작업

### 1. 정책 축 신설 — `app/paper/policy.py`
`risk_mode` · `min/max_stop_atr_multiple` · `reward_mode` · `max_reward_atr_multiple` ·
`take_profit_*_r` · `max_entry_cost_r` · `min_net_rr` · `htf_conflict_blocks` ·
`stop_fill_mode`. **기본값은 전부 기존 동작**이며 옵트인이다.

### 2. 게이트 3종 — `evaluate_entry`
`stop_bounds`(상한 초과 거부 — 좁히지 않는다) · `cost_efficiency`(마찰 상한) ·
`htf_alignment`. 꺼져 있으면 항상 통과, 켜져 있으면 **거부만** 한다.

### 3. 목표 계획 재설계 — `app/paper/service.py::_paper_target_plan`
`_execution_risk` 와 `_staged_reward` 로 리스크·보상 산출을 분리하고 `cost_r` ·
`stop_atr_multiple` 을 원장에 남긴다. `nearest_structure` 는 `price_levels` 의
**손절 쪽 가장 가까운 유효 레벨**을 고른다(새 감지기 아님 — 기존 레벨 소비).

### 4. 손절 체결 — `policy.stop_fill_price`
`intrabar` 는 봉 중간 터치 시 **무효화가**에, 봉이 이미 넘어서 열렸으면 **시가**에 체결한다.
재판정 하네스의 중복 구현을 제거하고 정책 함수를 호출하게 했다.

### 5. 두 진입 경로에 같은 산술 게이트
정규 경로와 검증 부트스트랩 경로 모두에 `stop_bounds` · `cost_efficiency` 를 건다.

### 6. 로더 버전 승계 + `crypto-v3.json`
`params/` 에서 버전 번호가 가장 큰 파일을 고른다. 채택: `structural_extend` ·
`max_reward_atr_multiple 3.5` · `max_entry_cost_r 0.16` · `stop_fill_mode intrabar`.

### 7. 스윕 + 부트스트랩 CI 판정
`scripts/paper_replay_report.py --sweep` 에 축 13개 추가.
`risk_sizing_replay.judge` 가 ImprovementProof 문법으로 판정하고,
`statistics.bootstrap_mean_ci` 가 거래당 netR 평균의 결정론 CI 를 낸다.

## 수용 기준
- [x] `tests/test_paper_net_edge.py` 25건 통과 — 항등식·비용 항등식·게이트·체결·갭·승계
- [x] `replay_fixture.close` 발표값 **소수점까지 불변**(기본 경로 회귀 0)
- [x] 픽스처 대조에서 v3 조합이 net +8.728 → +10.192 · PF 3.515 → 9.880 · MDD 8.675 → 2.869
- [x] 두 진입 경로에 같은 산술 게이트가 걸린다(소스 단언)
- [x] 재판정 하네스가 라이브와 같은 게이트 인자를 넘긴다(소스 단언)
- [x] HARNESS.md Gate 1 전부 통과

## 금지
- 비용률(`taker_fee_pct`·`slippage_pct`) 인하 — diff 0줄 (C8)
- 품질 임계(`min_rr`·`min_evidence`·`min_checklist_*`) 완화 — diff 0줄
- 무효화 임계 완화 — `intrabar` 는 체결 **시점**만 바꾼다
- 방향 판정 변경 — `app/analyst/` · `app/structure/` diff 0줄(테스트로 강제)
- 신규 감지기 추가 — 모라토리엄 준수
- 자동 승격 — 스윕 결과가 파라미터를 자동 적용하지 않는다
- 실주문 — 페이퍼는 read-only

## 문서
- 신규: `docs/validation/NET_EDGE.md`
- 갱신: `docs/PaperPolicy.md` · `docs/validation/STOP_EXECUTION.md`

## 남은 것 (이 WO 가 끝내지 못한 것)

이 컨테이너에는 운영 DB 도 거래소 접근도 없다(`api.bitget.com` 차단). 그래서
**임계값을 호스트 실캔들에서 정하는 일이 남는다.**

```bash
cd backend
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db \
    --sweep --out docs/validation/baselines/net_edge_sweep.json
```

"개선 (유의)" 등급을 받은 축만 `crypto-v3.json` 에 적는다. 우선순위는
`risk_mode` → `max_entry_cost_r` 조이기 → `rr_basis=net` → `htf_conflict_blocks` 다.

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
