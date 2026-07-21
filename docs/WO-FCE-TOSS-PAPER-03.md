# WO-FCE-TOSS-PAPER-03 — 주식 페이퍼 진입 0건: 자산군 신호 정합 수리

우선순위: P0  
선행: WO-FCE-TOSS-PAPER-02 (`70e77681`)  
정본: `docs/TossStockPaperTrading.md`, `docs/DirectionalEngine.md`

## 원칙

목표는 거래 수를 만드는 것이 아니라 주식에서 구조적으로 성립하지 않는 요구만 교정하는
것이다. RR 1.5, 무효화가, 진입 점수 75, evidence 4, checklist 5, 데이터 신선도와 롱 전용
제약은 낮추지 않는다. 수리 뒤 진입 0건도 정당한 결과로 인정한다.

## 착수 전 확인

- [x] `git branch -a`에 TOSS-PAPER-03 계열 없음
- [x] 기존 TOSS-PAPER-03 구현 없음
- [x] WO-02가 origin/main에 반영된 상태 확인

## 작업 1 — 운영 표본 범인 확정

`stock_paper_analysis_snapshots`의 stock-v2 69건(5종목, 2026-07-21 06:44:45~10:06:05
UTC)을 `scripts/audit_stock_entry_gates.py`로 재생했다. 당시 후보는 실행과 동시에 저장되므로
과거 `data_fresh`는 true로 재생한다. 거부 원장 자체는 28행이며 5분 중복 억제가 있어,
게이트 비율은 모든 분석 스냅샷을 정책에 다시 통과시킨 값을 정본으로 사용한다.

### 수리 전 stock-v2

| 게이트 | 통과 | 거부 | 거부율 |
|---|---:|---:|---:|
| analysis_available | 69 | 0 | 0.0% |
| confirmed_flip | 0 | 69 | 100.0% |
| evidence | 69 | 0 | 0.0% |
| checklist | 67 | 2 | 2.9% |
| entry_score | 0 | 69 | 100.0% |
| liquidation_safety | 69 | 0 | 0.0% |
| risk_reward | 16 | 53 | 76.8% |
| data_fresh | 69 | 0 | 0.0% |

결론: `confirmed_flip`의 순간 플래그 충돌은 실측 병목이다. 반면 evidence와 checklist는
각각 100%, 97.1% 통과해 펀딩·OI 부재나 checklist 절대수가 현재 진입 0건의 원인이
아니다. 따라서 B/C 임계와 evidence 수집기는 변경하지 않았다. 진입 점수 75는 전량
거부지만 안전 하드 게이트이므로 유지한다.

## 작업 2 — stock-v3

stock-v3는 숫자 임계를 한 개도 바꾸지 않는다. 유일한 정책 변경은 `stance_gate_mode`다.

| 항목 | stock-v2 | stock-v3 |
|---|---|---|
| stance 자격 | `long_leaning && flipped && !transitioning` | `(long_leaning 또는 long) && !transitioning` |
| flipped | 진입 필수 | 측정값·분석 스냅샷·주문 evidence에 기록 |
| evidence/checklist | 4 / 5·5 | 4 / 5·5 |
| RR / entry_score | 1.5 / 75 | 1.5 / 75 |
| 무효화가 / data_fresh / long-only | 필수 | 필수 |

주식 분석은 `signal_availability`에 펀딩·OI가 unavailable이며 evidence에 사용되지 않는다고
명시한다. 공용 confluence는 `analysis.derivatives.signals`가 실제로 존재할 때만 파생 evidence를
생성하므로 주식의 파생 evidence는 0건이다. 값이 없는 파생 신호를 합격 evidence로 만들지 않는다.

## 완화-아님 대응표

| 변경 조건 | 구조적 근거 | 보존 장치 |
|---|---|---|
| `flipped=True` 필수 → 기록 | stance 히스테리시스의 flip은 전환 순간 한 캔들 플래그이며 운영 표본 0/69 | `transitioning=False`와 롱 stance 필수 유지 |
| stock-v3 신규 버전 | 정책 의미 변경을 기존 v2에 덮어쓰지 않음 | v2 파일 불변, 숫자 diff 0 |
| 파생 가용성 명시 | Toss 주식에는 펀딩·OI 원천 없음 | unavailable, `used_by_evidence=false`, 파생 evidence 생성 금지 |

evidence/checklist/RR/entry_score/무효화가/data_fresh는 제거하거나 낮추지 않았다.

## 작업 3 — 수리 후 동일 표본 재생

| 게이트 | stock-v2 거부율 | stock-v3 거부율 |
|---|---:|---:|
| confirmed_flip/stable long | 100.0% | 11.6% |
| evidence | 0.0% | 0.0% |
| checklist | 2.9% | 2.9% |
| entry_score | 100.0% | 100.0% |
| liquidation_safety | 0.0% | 0.0% |
| risk_reward | 76.8% | 76.8% |
| data_fresh | 0.0% | 0.0% |

stock-v3의 안정 롱 stance는 61/69건 통과했지만 진입 성립은 0건이다. 남은 병목은
entry_score 69/69와 RR 53/69이며 둘 다 원래 안전 게이트다. 현재 표본의 점수가 53~65라
75 기준을 정당하게 통과하지 못했다. 진입 수를 만들기 위한 추가 임계 변경은 하지 않는다.

stock-v2와 stock-v3는 판정 정책이 다르므로 기존 4주 시계를 이어 붙이지 않는다. 각 시장의
첫 stock-v3 정상 관측에서 시작일·종료일을 다시 고정하고
`validation_clock_restarted(parameter_version_changed:stock-v2->stock-v3)`를 남긴다.

## 재현 명령

```bash
cd backend
python3 scripts/audit_stock_entry_gates.py fomo_control_engine.db \
  --source-version stock-v2 \
  --observed-to 2026-07-21T10:06:05.524349+00:00 \
  --policy app/stock_paper/params/stock-v2.json \
  --policy app/stock_paper/params/stock-v3.json
```

## 수용 기준

- [x] 수리 전 게이트별 거부율 실측
- [x] 실측 확인된 confirmed_flip 충돌만 수리
- [x] flipped는 필수에서 기록으로 전환, 안정 롱·비전환 요구 존치
- [x] 주식 파생 신호 unavailable 및 evidence 미사용 명시
- [x] stock-v3와 v2/v3 diff 표
- [x] RR·무효화·entry_score·data_fresh·evidence 임계 diff 0
- [x] 수리 전후 동일 표본 재생
- [x] 수리 후 진입 0건의 남은 정당 병목 명시
- [x] stock-v3 첫 정상 관측에서 검증 시계 재시작 및 사유 기록
- [x] HARNESS 게이트, origin/main, CI success
