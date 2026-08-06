# 표본 생성 능력 판정 (Sample Viability)

> WO-FCE-SAMPLE-VIABILITY-01 PHASE 1 정본.
> **관측일은 검증의 필요조건이지 충분조건이 아니다. 채점 가능한 표본이 나와야 검증이다.**

## 0. 왜 이 문서가 생겼나

직전 WO(`docs/ObservationIntegrity.md`)가 "유효 관측일"을 정직하게 세는 장치를 만들었다.
그 결과 **검증 진행에 대한 우리 인식이 틀렸다는 것**이 드러났다 — 크립토 14/28, 폴리 7/28,
KR 5/28, US 1/28.

그런데 더 나쁜 사실이 딸려 나왔다. **유효 관측일을 다 채워도 검증이 되지 않는 트랙이 있다.**

- **폴리**: 보유 9건 중 8건이 2027-01-01 만기(실측 2026-08-05). 검증 종료(08-19)보다 5개월 뒤다.
  검증 기간 내 정산 예정 **0건** — 28일을 다 채워도 채점 가능한 표본이 0이다.
- **크립토**: 관측 창에서 진입 4건. 선형 외삽해도 우리 자체 기준(N<30 = 표본 부족)에 못 미칠 수 있다.
- **주식**: 후보 기아는 해결됐으나 진입 실적이 아직 없다.

그래서 지금 필요한 것은 관측일을 더 쌓는 것이 아니라, **각 트랙이 검증 창 안에서 채점 가능한
표본을 만들 수 있는지**를 먼저 판정하는 것이다.

## 1. 무엇을 세는가

| 트랙 | 진입 (분자) | 채점 가능 표본 |
| --- | --- | --- |
| `crypto` | `paper_trades` 신규 | `status='closed'` (청산 완료) |
| `stock_kr` / `stock_us` | `stock_paper_fills` 매수 체결 | `stock_paper_fills` 매도 체결 (진입-청산 쌍 완결) |
| `poly` | `poly_positions` 신규 | `poly_resolutions` (만기 정산 = Brier 채점 가능) |

**진입률의 분모는 유효 관측일이다.** 달력일로 나누면 엔진이 멈춰 있던 날이 비율을 희석해
"선정 기준이 문제"라는 잘못된 결론이 나온다 — 그건 선정 문제가 아니라 정지 문제다.

정의: `app/validation/sample_viability.py` · 회귀: `tests/test_sample_viability.py`

## 2. 판정 등급

| 판정 | 조건 | 후속 |
| --- | --- | --- |
| `VIABLE` | D+28 도달 시 예상 표본 ≥ 30 | 그대로 진행 |
| `SLOW` | 도달 가능하나 시점이 검증 창 밖 | 창 연장 또는 기준 재검토 |
| `STRUCTURALLY_BLOCKED` | 선정 기준상 표본이 생성될 수 없음 | 선정 기준 수정 (관측일을 더 쌓아도 해결 안 됨) |
| `INSUFFICIENT_DATA` | 유효 관측일 < 3일 또는 진입 0건 | 관측 계속, 재판정 시점 명시 |

구조적 차단이 최우선 판정이다. 나머지 셋과 성격이 다르다 — **시간이 해결해 주지 않는다.**

## 2-1. 판정별 대응 규칙 (사전 확정)

> WO-FCE-VALIDATION-VERDICT-01 §1-2. **판정이 나오기 전에 정한다.**
> 판정이 나온 뒤에 규칙을 만들면 결과에 맞춰 규칙을 고르게 된다.

| 판정 | 의미 | 대응 |
| --- | --- | --- |
| `VIABLE` | D+28에 표본 ≥ 30 도달 예상 | **그대로 진행. 개입 없음** |
| `SLOW` | 도달하나 여유 없음 | 관측 유지 + 주간 재판정. **기준 완화 금지** |
| `STRUCTURALLY_BLOCKED` | 구조적으로 도달 불가 | 개별 처리 — 기회 표면 확대 / 검증 창 연장 / 트랙 유보 중 택일 |
| `INSUFFICIENT_DATA` | 판정 자체 불가 | **유효일 확보가 선행. 다른 조치 금지** |

`STRUCTURALLY_BLOCKED` 의 세 선택지에는 "기준을 낮춘다"가 없다. 셋 다 잣대를 유지한 채
표면을 넓히거나, 시간을 늘리거나, 정직하게 범위를 줄이는 것이다. **트랙 유보는 실패가 아니라
정직한 범위 축소다.**

`INSUFFICIENT_DATA` 에서 선정 기준을 손대는 것은 금지다 — 무엇이 문제인지 아직 모르는
상태에서 바꾸면 그 변경이 효과였는지 영원히 알 수 없다.

구현: `app/validation/verdict_watch.py::VERDICT_ACTIONS` · 회귀: `tests/test_verdict_watch.py`

## 2-2. 발행

판정은 두 경로로 사용자에게 도달한다. 대시보드를 열어야만 보이는 판정은 안 보이는 판정이다.

| 경로 | 주기 | 내용 |
| --- | --- | --- |
| 주간 성과 리포트 | 주 1회 | 트랙별 판정 + 유효일 + 표본 + **D+28 예상 표본** (상시) |
| 판정 전이 알림 | 전이 시 1건 | `VIABLE → SLOW` 처럼 판정이 **바뀔 때만** |

전이 알림은 나빠질 때만이 아니라 좋아질 때도 보낸다 — 좋아진 것을 조용히 넘기면 "언제부터
좋아졌는가"를 사후에 알 수 없다. 같은 판정이 유지되는 동안에는 0건이다(스팸 금지).

## 3. 실측 산출

이 표는 **운영 DB에서 직접 산출한다.** 추정 계수를 만들지 않는다.

```bash
cd backend
python3 scripts/sample_viability_report.py ~/fomo_control_engine.db          # 마크다운
python3 scripts/sample_viability_report.py ~/fomo_control_engine.db --json   # 기계 판독본
```

또는 진단 API로:

```bash
curl -s localhost:8875/api/system/paper/diagnosis | python3 -m json.tool | grep -A 60 sample_viability
```

### 산출 근거 쿼리

```sql
-- 유효 관측일 (분모)
SELECT COUNT(*) FROM observation_coverage WHERE track = :track AND valid = 1;

-- 유효 관측일에 발생한 진입 (분자) — 크립토
SELECT COUNT(*) FROM paper_trades t
WHERE substr(t.entry_bar_at, 1, 10) IN (SELECT day FROM observation_coverage WHERE track='crypto' AND valid=1);

-- 채점 가능 표본 — 크립토 / 주식 / 폴리
SELECT COUNT(*) FROM paper_trades WHERE status='closed' AND exit_at IS NOT NULL;
SELECT market, COUNT(*) FROM stock_paper_fills WHERE side='sell' GROUP BY market;
SELECT COUNT(*) FROM poly_resolutions;

-- 폴리 구조적 차단 — 보유 만기가 검증 종료 이후인가
SELECT p.market_id, m.end_at, (SELECT ends_at FROM poly_paper_track WHERE id=1) AS deadline
FROM poly_positions p JOIN poly_markets m ON m.market_id = p.market_id
WHERE p.status = 'open' ORDER BY m.end_at;
```

`D+28 예상 표본 = (유효일당 진입 × 남은 유효일 + 지금까지 진입) × 청산 완료율`

청산 완료율은 진행 중 포지션 때문에 아래로 편향된다(우측 절단). 그래서 예상 표본은
**보수적으로** 나온다 — 판정에서는 그 방향이 안전하다.

유효일당 진입에는 95% 신뢰구간(Poisson, Byar 근사)을 함께 낸다. 관측 창이 짧아 비율이
불안정하다는 사실을 숫자로 남기기 위해서다.

## 4. 산출 시점의 확정 사실

아래는 실행 없이도 확정된 판정 근거다. 나머지 수치는 §3의 스크립트가 채운다.

| 사실 | 근거 | 함의 |
| --- | --- | --- |
| 폴리 보유 9건 중 8건이 2027-01-01 만기, 검증 창 내 정산 예정 0건 | 실측 2026-08-05 (직전 WO Phase 4) | 폴리 = `STRUCTURALLY_BLOCKED` |
| 유니버스에 검증 종료 이전 만기 시장 4,847개 존재 | 실측 2026-08-05 | **데이터 문제가 아니라 선정 기준 문제** |
| KR 정규장(00:00~06:30 UTC)은 절전 구간(17:00~20:00 UTC)과 겹치지 않는다 | 구조적 — `tests/test_coverage_axes.py` 가 고정 | KR 유실을 절전으로 귀인하면 오귀인 |
| 검증 창(2026-07-15~08-06)에 확정 KR·US 휴장일이 없다 | `app/worker/market_calendar.py` | 휴장 오계상은 54.2%의 원인이 아니다 |
| 2026-07-17(제헌절) 공휴일 재지정 시행 여부 미확인 | 미확정 후보로 표시 | **사람 확인 필요** — 휴장이면 그날은 유실일이 아니다 |

> 마지막 항목은 **분모를 바꾸지 않은 채** 진단에 올라간다. 추정으로 유실일을 지우면 검증
> 진행도가 부풀려지므로, 확인될 때까지 나쁜 숫자를 그대로 둔다.

## 5. 진술 규칙

검증 진행을 말할 때는 **유효 관측일 + 채점 가능 표본**으로만 말한다.

- ✕ "3주 검증 중"
- ✕ "거의 다 됐다"
- ○ "크립토 유효 관측일 14/28, 채점 가능 표본 4/30 — 표본 부족"

`track_sample_viability()["statement"]` 가 이 형식을 생성한다. 화면·리포트는 이 문자열을 쓴다.

## 6. 금지

- 실적이 0인 트랙을 유니버스 크기로 추정하기 (**0은 0으로 적는다**)
- N<30 에서 승률·수익률을 계산하기 (표본 부족이라고 쓴다)
- 판정을 통과시키려고 목표 표본 수(30)를 낮추기
- 캘린더 일수로 검증 진행을 말하기
- 구조적 차단을 "조금 느린 것"으로 표기하기 — 시간이 해결하지 않는다
