# 폴리마켓 트랙 (WO-FCE-POLY-STATUS-01)

구현: `backend/app/poly_paper/track_status.py` · 테스트: `backend/tests/test_poly_track_status.py`

## 결론

이 트랙은 **두 겹으로 막혀 있다.** 어느 하나만 풀려도 검증이 되지 않는다.

| 층 | 상태 | 재시작으로 풀리는가 |
| --- | --- | --- |
| 수집 | **451 지역 차단** | **아니다** — 정책 차단이다 |
| 표본 | **`STRUCTURALLY_BLOCKED`** | **아니다** — 만기가 검증 창 밖이다 |

화면은 이제 `구조적 검증 불가 + 수집 차단` 으로 그것을 말한다. 이전에는 원시 예외
문자열을 뱉고 나머지는 정상 트랙처럼 렌더했다.

---

## D1 · 451 은 지역 차단이다 — 코드 결함이 아니다

```
HTTPStatusError: Client error '451 Unavailable For Legal Reasons'
  for url 'https://gamma-api.polymarket.com/events?...'
```

`451` 은 **정책 차단**이며 재시도·백오프로 풀리지 않는다. 그런데 화면이 이 문자열을 그대로
보여줘서 "되는 건가 안 되는 건가"를 판단할 수 없었다.

이제 분류한다:

| 상태 | 라벨 | 재시도 |
| --- | --- | --- |
| `geo_blocked` | 수집 차단 (451) · 지역 제한 | **아니오** |
| `transient_error` | 수집 실패 (일시적일 수 있음) | 예 |
| `ok` | 수집 정상 | — |

원시 예외는 **상세 보기**로 내렸다. 헤드라인에 예외 클래스명이 나오지 않는다.

### 우회하지 않는다 (C1)

법적 차단이다. 프록시·VPN·우회 경로를 만들지 않았고, 조치 문구도 우회를 권하지 않는다 —
`test_geo_block_advice_does_not_suggest_a_bypass` 가 그것을 고정한다. 접근 가능한 경로 확보는
**코드 밖의 결정**이다.

### 발생 시점 (실측 2026-08-26)

`observation_coverage` 의 폴리 행에서 사유별로 갈린다:

| 구분 | 일수 | 구간 |
| --- | --- | --- |
| 수집 정지 (관측 0건) | 22 | 2026-07-07 ~ 08-26 |
| 커버리지 미달 | 20 | 2026-07-22 ~ 08-19 |
| **유효** | **9** | 2026-07-24 ~ **08-07** |

**마지막 유효 관측일이 2026-08-07 이다.** 그 뒤 19일간 유효일이 0이다. 최근 정지 구간은
**08-20 부터 7일 연속**이며 이것이 451 구간이다.

---

## D2 · 검증 시계 0/28 의 사유 분해

검증 창은 2026-08-13 에 열렸다. 창 안 14일이 정확히 갈린다:

| 사유 | 일수 |
| --- | --- |
| 커버리지 미달 (관측이 있었으나 부족) | **7** |
| 수집 정지 (451) | **7** |
| **유효** | **0** |

`유실 12일 제외` 만으로는 무엇을 고쳐야 하는지 알 수 없다. **두 사유의 조치가 다르다** —
커버리지 미달은 관측 간격 문제(호스트·절전)이고 정지는 지역 차단(코드 밖)이다. 합쳐서
"유실"로 부르면 그 구분이 사라진다.

---

## D3 · 숫자 셋이 다른 것을 센다

| 값 | 수 | 무엇인가 |
| --- | --- | --- |
| `poly_resolutions` | **12,774** | 시장 전체 확률 추정 채점(Brier) 관측 |
| `poly_positions` | 9 | **우리 포지션** |
| 검증 창 내 정산 예정 | **0** | **우리 검증 표본** |

화면이 12,774 를 `정산 표본 N` 으로 보여줬다. 보유가 8건인데 표본이 12,774 일 수 없다.

### 그리고 그 불일치가 판정 모듈에도 있다

`sample_viability` 의 폴리 스펙:

```python
entry_sql  = "SELECT opened_at AS t FROM poly_positions"       # 9
scored_sql = "SELECT resolved_at AS t FROM poly_resolutions"    # 12,774
```

그 결과:

```
exit_completion_rate: 1419.333      ← 청산 완료율 141,933%
sample_sufficient: true             ← 12,774 > 30 이므로 "표본 충분"
projected_samples_at_target: 4412
```

**청산 완료율 141,933% 가 분자·분모가 다른 것을 센다는 증거다.** `sample_sufficient: true`
는 거짓이다 — 우리 검증 표본은 0이다.

다만 최종 `verdict` 는 다른 근거(만기가 창 밖)로 `STRUCTURALLY_BLOCKED` 이므로 이 오류가
판정을 뒤집지는 않았다. **그래도 표시되는 값이 거짓인 것은 그대로다.**

`sample_viability.py` 는 판정 계층이고 C3 이 수정을 금지하므로 이 WO 는 고치지 않았다.
**화면에서 두 수를 갈라 놓는 것까지가 범위다.** 스펙 수정은 별건이다.

---

## D4 · `STRUCTURALLY_BLOCKED` 이 화면에 없었다

판정은 이미 있었다:

```
verdict: STRUCTURALLY_BLOCKED
verdict_reason: 보유 8건 전부 검증 종료(2026-09-10) 이후 만기 — 검증 창 내 정산 예정 0건
```

그리고 대시보드에는 `expiry.sample_possible: false` 도 이미 있었다. **둘 다 화면이 쓰지
않았다.** 판정을 만들지 않고 읽어서 표시한다(C3) — `track_status()` 가 그 일만 한다.

---

## 2-3 · 처리 방침 결정

`pending_decisions` 에 **차단 등급**으로 올렸다(`poly_track_disposition`).

| 안 | 내용 | 지금 실행 가능한가 |
| --- | --- | --- |
| A | 검증 대상에서 **명시적 제외** | **가능** |
| B | 만기 짧은 시장으로 **유니버스 교체** | **불가능** — 시장 목록 API 가 451 로 차단됐다 |
| C | 차단 상태로 **유지** | 가능 |

> **451 이 안 풀리면 B 도 불가능하다.** 유니버스를 갈려면 시장 목록을 받아야 하고 그
> API 가 막혀 있다. 즉 지금 선택지는 **A 와 C 뿐**이다.

이 항목은 막혀 있을 때만 노출된다 — 차단이 풀리면 자동으로 사라진다.

## 원장 보존 (C2)

기존 데이터를 지우지 않았다. `poly_resolutions` 12,774행·`poly_positions` 9행·
`poly_markets` 7,139행·`poly_estimates` 43,690행 그대로다. 화면도 "기존 원장은 보존됩니다"를
계속 표시한다.

---

## 처리 방침 확정 — A(검증 대상 제외) · 임시값 (WO-FCE-DEFAULTS-01 1-2 · 2026-08-27)

세 안 중 A 를 임시값으로 확정했다. 451 이 안 풀리면 B 가 불가능하고 C 는 판정을 영구
미결로 둔다.

| 항목 | 처리 |
| --- | --- |
| 검증 판정 범위 | **제외** |
| 수집 | **유지** — 451 이 풀리면 표본이 다시 쌓인다 |
| 원장 | **유지** — 삭제 0건 |
| 화면 | `검증 대상 제외 · 임시값` 배너 + 사유 + 원복 명령 |
| `pending_decisions` | **삭제하지 않고** `provisional_applied` 로 전이 |

### 실측 정정 — 폴리는 코드 판정을 막고 있지 않았다

WO 는 "트랙별 판정이 폴리 때문에 막히지 않게 한다"고 했다. 확인하니 `live_trading_gate` ·
`sample_rate` 둘 다 트랙별 행이고 AND 가 없어 **막고 있지 않았다.** 실제 차단은
`COMPLETION_DEFINITION` 서술과 결정 항목에 있었고 그 둘을 고쳤다.

그래서 판정 계층(`VERDICT_MODULES`)을 건드리지 않았다 — `validation/track_scope.py` 가
범위를 선언하고 보고 표면이 읽는다.

### 원복

```bash
FCE_VALIDATION_EXCLUDE_POLY=false
```

---

## 분모 정합 수리 (WO-FCE-DEFAULTS-01 1-5 · 2026-08-27)

**임시값이 아니라 버그 수리다.** 분자·분모가 다른 것을 세고 있었다.

```python
# 이전
scored_sql = "SELECT resolved_at AS t FROM poly_resolutions"          # 시장 전체 12,774
entry_sql  = "SELECT opened_at AS t FROM poly_positions"              # 우리 포지션 9

# 이후 — 조인으로 같은 모집단에 묶는다
scored_sql = ("SELECT r.resolved_at AS t FROM poly_resolutions r "
              "JOIN poly_positions p ON p.market_id = r.market_id "
              "GROUP BY r.market_id")
```

`GROUP BY` 가 필요한 이유: 한 시장에 확률 추정이 여러 개 있어 조인만 하면 9건이 221행으로
불어난다. 정산 표본은 **시장당 1건**이다.

### 5트랙 판정 불변 증명 (실측)

| 트랙 | 전 | 후 |
| --- | --- | --- |
| `crypto` | `VIABLE` | `VIABLE` |
| `stock_kr` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
| `stock_us` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
| `whale_follow` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
| `poly` | `STRUCTURALLY_BLOCKED` | `STRUCTURALLY_BLOCKED` |

폴리 수치만 정상화됐다:

| 값 | 전 | 후 |
| --- | --- | --- |
| `scored_samples` | 12,774 | **1** |
| 청산 완료율 | **1419.333** (141,933%) | **0.111** |
| `sample_sufficient` | **true** (거짓) | **false** |
| `projected_samples_at_target` | 4,412 | 0 |

**임계값은 건드리지 않았다** — `TARGET_SAMPLES=30` · `TARGET_EFFECTIVE_DAYS=28` ·
`MIN_EFFECTIVE_DAYS_FOR_RATE=3` 그대로다(§0).

### 판정 계층 가드를 우회하지 않았다

`sample_viability.py` 는 `VERDICT_MODULES` 다. 가드가 정당하게 물었고, 넓히는 대신
**불변 증명 테스트의 존재를 조건으로 묶었다** — 스펙 필드를 고치려면
`test_track_spec_change_keeps_every_verdict` 가 함께 있어야 통과한다. 그 테스트가 없으면
스펙 수정이 막힌다.

합성 diff 로 이빨을 확인했다:

| 시도 | 결과 |
| --- | --- |
| 임계 하향(`TARGET_SAMPLES 30 → 10`) | **걸림** |
| 판정 완화(`if sample_size >= 5`) | **걸림** |
| 스펙 밖 삭제 | **걸림** |
| 스펙 필드 수정 | 통과 (증명 테스트 존재 시) |
| 주석 추가 | 통과 |

---

## Phase 2 — 공급원 판정은 **호스트에서만 나온다** (2026-08-31)

### 개발 컨테이너에서 잰 값은 근거가 아니다

`451` 은 **지역 차단**이므로 어디서 부르는지가 결과를 바꾼다. 이 저장소의 개발 컨테이너는
이그레스 허용목록 뒤에 있어 폴리·칼시 **양쪽 모두 연결 자체가 안 된다**:

```
gamma-api.polymarket.com      → URLError: Tunnel connection failed: 403 Forbidden
clob.polymarket.com           → 같음
data-api.polymarket.com       → 같음
api.elections.kalshi.com      → 같음
api.github.com                → 200          ← 대조군. 네트워크 자체는 산다
```

**칼시까지 같은 실패를 낸다는 것이 결정적이다.** 칼시는 451 대상이 아니므로, 이 실패는
폴리의 지역 차단이 아니라 **컨테이너의 이그레스 정책**이다. 그것을 "차단 확인"으로 적으면
거짓이 된다.

> **그래서 Phase 2 의 결론(대체 채택 / 트랙 종료)은 이 커밋에서 확정할 수 없다.**

### 호스트가 돌릴 탐침

```bash
cd backend && PYTHONPATH=. python3 scripts/prediction_market_probe.py
```

엔드포인트별 상태 코드를 **있는 그대로** 적고, 200 인 소스마다 **28일 창 안에 만기가 오는
시장 수**를 센다. 우회하지 않는다(C3) — `451` 이 나오면 `451` 이라고 적는 것이 이 스크립트의
일이다.

### 만기가 접근성보다 먼저다

폴리가 `STRUCTURALLY_BLOCKED` 였던 진짜 이유는 차단이 아니라 **만기 2027-01-01** 이었다 —
검증 창(28일) 안에 정산이 없었다. **접근 가능해도 `within_window == 0` 이면 채택할 수 없다.**
만기 확인 없이 대체를 채택하면 같은 결론이 반복된다(금지 조항).

### 판정 분기

| 탐침 결과 | 결론 |
| --- | --- |
| 접근 가능 소스 0 | **트랙 종료** — `COMPLETION_DEFINITION.md` 에서 제거, 화면·리포트 `종료` 표기, **원장 보존**(C6) |
| 접근은 되나 `within_window == 0` | 채택 불가. 종료와 같은 처리 |
| `adoptable` 소스 존재 | 그 소스로 배선. 만기 분포를 근거로 첨부 |

**두 달째 표본 0인 트랙을 붙들지 않는다.** 다만 종료는 실측 위에서 선언해야 하고, 그
실측이 아직 없다.
