# Toss 주식 페이퍼 트레이딩 운영·검산

주식 분석 스냅샷은 리포트가 포함한 `datetime`·UUID·Enum을 JSON 안전 값으로 변환해
저장한다. 이 저장 단계가 실패하면 해당 수집 tick은 성공으로 기록하지 않으며 워커
heartbeat의 오류에서 확인한다.

## 경계

- 크립토 페이퍼는 `app/paper`와 기존 USDT 시계를 그대로 사용한다.
- 주식 페이퍼는 `app/stock_paper`의 KRW/USD 계정과 KR/US 4주 시계를 사용한다. 두 성적은 합산하지 않는다.
- 주식 진입·리스크 임계값은 `app/stock_paper/params/stock-v4.json`에서 버전 관리하며 v1~v3는 감사·재생 기준으로 보존한다. 크립토 페이퍼 파라미터를 읽거나 변경하지 않는다.
- 주문 경로는 `PaperBroker`뿐이다. `LiveBroker`는 Protocol이며 구현체·레지스트리가 없다. `FCE_STOCK_LIVE_TRADING_ENABLED=true`는 설정 검증에서 기동 실패한다.
- Toss 클라이언트 허용 경로에는 주문·계좌 API가 없다.
- 독립 4주 시계는 자격정보 존재가 아니라 시장별 첫 인증 성공 `status=observed`에서 시작한다. 인증 실패·순환 봉쇄 기간은 무효 사유와 함께 제외한다.

## 체결 정직성

체결 순서는 정규장 → warnings/VI/정지 → 가격제한 잠김 → 당분 거래량 5% → 반스프레드 → 호가단위 → 당분 고저 invariant다. 장외 주문은 `session_closed`로 큐잉되고 다음 정규장 첫 관측 시가를 사용한다. 첫 시가, 1분 OHLCV, 호가 중 하나라도 없으면 `market_data_missing`이며 체결하지 않는다. Toss가 아직 거래가 없는 현재 분봉을 거래량 0으로 반환하면 체결 근거로 쓰지 않고, 가장 최근의 거래량 있는 확정 1분봉을 사용한다.

세션 판정은 KR `today.integrated.regularMarket`, US `today.regularMarket`만 사용한다. 미국 day/pre/after market은 수집은 가능하지만 PaperBroker 체결 세션으로 보지 않는다.

KR 체결은 원화 수수료와 매도 거래세, US 체결은 달러 수수료를 저장한다. USD→KRW 환율은 Toss의 1분 유효 참고 환율이 실제로 응답한 경우에만 fill에 관측 시각과 함께 저장한다. 환율이 없으면 빈칸이다.

## 벤치마크 정직성

Toss 시장지표 API의 현재 공식 카탈로그는 KOSPI/KOSDAQ과 국내 국채만 제공하며 KOSPI100·Nasdaq-100 지수 심볼은 제공하지 않는다. 따라서 같은 Toss 가격 소스 안에서 다음 비레버리지 ETF를 명시적 프록시로 사용한다.

- KOSPI100: KODEX 코스피100 `237350`
- Nasdaq-100: Invesco QQQ `QQQ`

화면과 API는 `benchmark_method=unlevered_etf_proxy_close`와 프록시 심볼을 항상 노출한다. ETF 보수·추적 오차 때문에 “지수 자체”로 표기하지 않는다. 프록시 가격이 없으면 벤치마크 수익률도 빈칸이다.

## TPS 검산 (200종목 + 프록시 2종목)

KR/US 각 100종목과 시장별 프록시 1개는 200건 배치 한도 안에서 각각 1콜이다. KR/US 수집기는 동일 client/API-group 토큰 버킷을 공유한다.

| 그룹 | 호출 | 정상상태 환산 | 공식 한도 | 판정 |
|---|---:|---:|---:|---|
| MARKET_DATA | 현재가 2콜/10초 + 후보 36종목×3콜/15초 + 비후보 보유종목 최대 10종목×3콜/15초 | 최대 9.4 TPS | 10 TPS | 통과 |
| MARKET_DATA_CHART | 후보 36종목 + 비후보 보유종목 최대 10종목의 1분봉/15초 + 후보 일봉 36콜/일 + 일봉 백필 2콜/10초 | 정상 최대 3.27 TPS | 5 TPS | 통과 |
| STOCK | 종목 메타 2콜/10초, warnings는 종목별 24시간 캐시 | 상시 0.2 TPS | 5 TPS | 통과 |
| RANKING | 시장별 6콜/60초 | 0.2 TPS | 5 TPS | 통과 |
| MARKET_INFO | 캘린더 2콜/10초 + USD/KRW 1콜/10초 | 0.3 TPS | 3 TPS | 통과 |
| AUTH | 토큰 캐시(만료 60초 전 갱신) + 401 강제 재발급 | 상시 0.0 TPS · 최악 0.03 TPS | 5 TPS | 통과 |

**차단 재시도 반영 검산 (WO-FCE-TOSS-US-STALL-01, C4).** 자동 재시도를 도입해도 호출량은
**늘지 않고 줄어든다** — 차단 중에는 네트워크 호출이 0이기 때문이다.

| 상태 | 이전 | 현재 | 증감 |
| --- | --- | --- | --- |
| 인증 차단 중 | 0콜 (영구 정지, 20.8시간 실측) | 백오프당 1콜 (최악 60초당 1콜 = 0.017 TPS) | +0.017 TPS |
| `edge_blocked` 중 | **10초당 1콜 = 0.1 TPS** (래치 없어 무한 반복) | 최악 60초당 1콜, 이후 300→900→1800초 | **−0.083 TPS** |
| 점검(maintenance) 중 | 900초당 1콜 | 동일 | 0 |
| 정상 수집 | 표 위 그대로 | 동일 (재시도 경로는 차단 시에만 작동) | 0 |

인증 재시도 1회당 토큰 재발급 1콜이 추가될 수 있으나(AUTH 그룹, 5 TPS 한도), 백오프 하한이
60초이므로 최악 0.017 TPS다. 재시도는 시장별 독립이라 KR·US 동시 차단 시에도 0.034 TPS.

엄격 후보는 시장별 상위 18개로 제한한다. 별도 coverage 스캐너는 시장별 2종목씩 버전 고정 유니버스를 순환하며 현재가·warnings·호가·체결·가격제한·1분봉을 모두 받은 종목만 실행 표본 후보로 만든다. 후보 일봉 200개는 종목별 하루 한 번만 갱신하고 나머지 15분 주기는 저장본을 공용 분석에 사용한다. 최초 백필 버스트도 5 TPS 공유 버킷이 직렬화한다. 시장별 최대 5개 포지션이 모두 후보 밖인 최악 조건까지 계산한 값이다. 비후보 유니버스는 시장별 한 종목씩 순환하며 일봉 200개를 백필해 약 17분에 100종목을 한 번 순회한다. `X-RateLimit-Remaining`이 20% 아래로 내려가면 공유 버킷이 선제 감속하고 429는 Retry-After와 지수 백오프로 재시도한다.

## 구동 잡·엔진 플래그 관계 (침묵 금지)

주식 페이퍼는 두 플래그로 나뉜다.

- `FCE_TOSS_STOCK_SCOUT_ENABLED` (구동 잡 `toss_stock_scout`, 기본 **False**): Toss 시장 수집 + `run_stock_paper_engine` 실행 주기를 만든다. 이 잡이 꺼지면 엔진은 **한 번도 호출되지 않는다**.
- `FCE_STOCK_PAPER_ENGINE_ENABLED` (엔진, 기본 **True**): 엔진 내부 활성 여부. 상태 화면·설정에는 이 값이 노출된다.

두 값이 어긋나면(엔진 True · 구동 잡 False) 상태 화면엔 "활성"으로 보이지만 실행되지 않는 **스플릿 플래그 함정**이 된다. `run_stock_paper_engine`의 `_ready_to_start`도 `toss_stock_scout_enabled`를 요구하므로, 구동 잡이 꺼진 상태에서 엔진을 직접 호출해도 `toss_observation_not_configured`로 조기 반환한다.

이 불일치는 이제 **조용히 유지되지 않는다**. `WorkerManager`가 기동 시 명시적 경고를 발행하고(`worker flag inconsistency`), `status()["flag_warnings"]`와 `/api/system/paper/diagnosis`에 노출한다. 주식 페이퍼를 실제로 돌리려면 두 플래그를 모두 켜야 한다. 자세한 계약은 [`docs/PaperObservability.md`](PaperObservability.md).

## 검증 시계 유실일 처리

주식 트랙의 검증 경과일은 달력일이 아니라 **엔진이 정상적으로 평가를 수행한 날**(effective run)로 세는 것이 정직하다. 워커가 죽었거나 구동 잡이 꺼져 있던 날 = 원장 미축적 = 검증일 유실이며, 이를 정상 경과로 계산하면 안 된다.

이 WO에서 관측 기반을 놓았다: 워커 하트비트 `last_effective_run_at`(마이그레이션 `0031`)이 엔진이 실제로 평가한 마지막 시각을 기록한다. `last_success_at`과의 괴리가 유실 구간을 드러낸다. 검증 시계를 유실일 제외 기준으로 재계산하는 계산부는 **WO-FCE-ENGINE-LIVENESS-01에서 구현 완료**했다 — `app/worker/liveness.py::elapsed_excluding_gaps()` 가 `{calendar_days, effective_days, lost_days, label}` 을 반환하며 라벨은 "경과 N일 (유실 M일 제외)" 형식이다. 대시보드 표기 연결은 화면 작업이라 별도 UI WO로 남긴다.

## RR 정의 — 분할 청산 가중 (WO-FCE-PNF-TARGET-01)

기존 RR의 보상 분자는 `take_profit[0]`(가장 가까운 목표) 하나뿐이었다. 실제 운용은 분할 청산인데 RR은 TP1만 셌으므로 보상이 **체계적으로 과소평가**됐고, 구조 레벨이 촘촘한 종목은 첫 목표가 손절폭보다 가까워 RR<1이 항상 발생했다(실측: US `risk_reward` 거부 22건, NVDA RR 0.53/임계 1.5). RR 게이트가 종목을 거른 게 아니라 목표 산출 방식이 스스로를 거른 기아 상태였다.

수리: 보상을 **분할 청산 배분 가중 기대값**(기본 40/35/25, 사용 가능한 목표 수로 정규화)으로 산출한다. 임계 `min_rr`은 **1.5로 불변**이다 — 분자를 정합화할 뿐 기준을 낮추지 않는다.

두 정의를 **병기**해 어느 쪽으로 통과했는지 항상 추적한다.

| 키 | 의미 |
|---|---|
| `rr_ratio` | 게이트가 쓰는 정본 = 가중 RR |
| `rr_ratio_first_target` | 기존 TP1 단독 기준(회귀 비교 기준선) |
| `rr_detail` | 위험·보상·목표 수·사용된 배분비율 |
| `rr_ab` | `{with_pnf, structure_only, first_target_only}` A/B 기록 |
| `target_source` | `structure_levels` 또는 `structure_levels+pnf_measured_objective` |

**정직한 고지**: 가중평균은 TP1 이상이므로 통과율이 오르는 방향인 것은 산술적으로 당연하다(민감도 그리드 105조합: 31.4%→48.6%, 새로 거부 0). 정당화 근거는 "기준을 낮췄다"가 아니라 "분자가 틀렸었다"이다. 다만 무차별 통과는 아니다 — NVDA 사례는 TP2가 3배여도 RR 1.02로 여전히 거부된다. **이 변경이 성과를 개선하는지는 청산 표본이 0이라 판정 불가하다.**

PNF 측정 목표의 산출·편입 규칙은 [`docs/PNFTarget.md`](PNFTarget.md)가 정본이다.

## 청산 경로 부재 (미해결 · 최우선)

**주식 페이퍼에는 매도(SELL) 주문을 생성하는 코드가 전 코드베이스에 없다.** `StockOrder` 생성은 `service.py`의 `side=Side.BUY` 한 곳뿐이며, `Side.SELL`은 accounting(거래세)·execution(하한가 잠김)·broker(수량 검증)에만 등장한다 — 전부 "SELL 주문이 왔을 때"의 처리기이고 그 주문을 만드는 코드가 없다.

따라서 청산 0건은 청산 조건이 까다로워서가 아니라 **구조적으로 매도가 불가능**하기 때문이다. 이 상태에서는 승률·실현 R·목표 도달률을 채점할 수 없고, 어떤 진입 개선안도 효과를 측정할 수 없다. 청산 경로 신설이 후속 WO의 최우선 항목이다([`docs/WinRateImprovementBacklog.md`](WinRateImprovementBacklog.md) 선결 0-1).

## 관측·복기

GE의 `주식 트랙`은 시장별 시작일/28일, 원통화 NAV, 프록시 수익률, 미체결 사유, 최근 fill의 수수료·세금을 표시한다. GR 요약은 같은 주식 트랙으로 연결한다. `stock_paper_events`가 세션·가격제한·VI·유동성·데이터 누락을 실제 발생 건수로 보존한다. 3트랙 공통 이벤트 계약(`opened/closed/rejected_summary/skipped/error`)과 텔레그램 배선은 [`docs/PaperObservability.md`](PaperObservability.md)를 정본으로 한다.

### stock-v4 커버리지 레인

stock-v4는 엄격 신호 정책의 evidence 4, checklist 5/5, RR 1.5, entry score 75,
무효화가, 안정된 롱 방향을 바꾸지 않는다. 대신 순위 격차 후보가 0건인 날에도
PaperBroker의 실제 체결·수수료·유동성·복기 표본을 만들기 위해 별도 `coverage` 레인을 둔다.

- `strict_signal`: 기존 게이트를 전부 통과한 전략 검증 표본. 전략 성과 집계 대상이다.
- `coverage`: warnings·유니버스·정규장·관측 신선도·일손실·최대 포지션과 모든 체결
  invariant는 유지하되, 미통과 신호 게이트는 주문 evidence에 측정값과 함께 기록한다.
  실행 모델·회계·복기 커버리지용이며 전략 적중 성적에 합산하지 않는다.
- 두 레인은 별도 현금·포지션·NAV로 표시한다. 전체 계정은 하위 호환 조회용이다.
- 목표 배분은 원금의 0.5%이며, 1주 단위 제약 때문에 고가주는 최소 1주가 목표 비중을
  초과할 수 있다. 실제 주문 수량과 비용은 원장에 그대로 남긴다.

2026-07-22 KR 정규장에서 coverage 3건을 실제 관측 데이터로 확인했다: `005930`
261,000원, `000660` 1,836,000원, `012330` 514,000원. 각 체결은 Toss 호가와 가장
최근의 거래량 있는 확정 1분봉 고저 안에 있었고, 수수료·환율 관측 시각이 fill에 저장됐다.
US는 정규장 개장 전에는 `closed`로 남고 다음 `today.regularMarket`에서 같은 경로를 실행한다.

### 체결 시점 차트

GE `주식 트랙`의 **언제 진입했나**는 `stock_paper_fills`의 실제 체결과 `toss_candles`의 실제 관측 OHLCV를 종목·시장으로 조인한다. 매수는 `진입`, 매도는 `청산` 마커이며 하단 원장에 초 단위 체결 시각·원통화 가격·수량을 함께 표시한다. 기본은 체결 주변 1분봉이고, 보존된 1분봉이 없을 때만 일봉으로 폴백한다. 사용한 시간봉과 소스는 차트 상단에 항상 노출한다.

`GET /api/stock-paper/entry-chart?market=US&symbol=AAPL`은 저장된 fill이 있는 종목만 반환한다. fill이 없으면 `paper_fill_missing`, Toss 캔들이 없으면 `observed_candles_missing`을 반환하며 화면은 가격이나 캔들을 합성하지 않는다. 이 차트는 매매 신호나 신규 지표가 아니라 PaperBroker 체결 invariant를 사람이 시간축으로 감사하기 위한 읽기 전용 뷰다.

![주식 트랙 분리 뷰와 미체결 사유 분포](assets/WO-FCE-TOSS-PAPER-01-dashboard.png)

참고 출처:

- [Toss Securities Open API](https://developers.tossinvest.com/docs)
- [Nasdaq-100 companies](https://www.nasdaq.com/solutions/global-indexes/nasdaq-100/companies)
- [KRX 정보데이터시스템](https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?locale=ko)
- [KODEX 코스피100](https://www.samsungfund.com/etf/product/view.do?id=2ETF57)
- [Invesco QQQ](https://www.invesco.com/us/financial-products/etfs/product-detail?productId=QQQ&ticker=QQQ)
# stock-v2 진입 파이프라인 (WO-FCE-TOSS-PAPER-02)

`stock-v1.json`은 감사 기준으로 불변 보존한다. 기본 정책은 `stock-v2.json`이며 변경점은 아래뿐이다.

| 항목 | stock-v1 | stock-v2 |
|---|---|---|
| 분석 입력 | 스카우트 단일 신호 필드 | 저장 Toss 캔들 → 공용 차트/합류/리포트 엔진 |
| 검증 시그니처 | 필수(순환 봉쇄) | 상태 기록만, 진입 필수 아님 |
| 실적 일정 | `earnings_clear=true` 필수 | 소스 부재를 `not_evaluable`로 명시, 조용한 통과 아님 |
| 진입 점수 | 없음 | 공용 리포트 점수 75 이상 |
| RR·무효화·확정 flip·증거 | 필수 | 동일하게 필수 |

랭킹 스캔은 관측 목적의 전시장을 유지한다. 후보에는 `tradable`과 `role`을 붙이며 PaperBroker는 `tradable=true`인 분기별 정본 200종목만 받는다. QQQ와 237350은 벤치마크 프록시라 진입 대상이 아니다.

기존 인증 실패/순환 봉쇄 기간은 검증 표본이 아니다. 시장별 시계는 인증 성공 뒤 첫 정상 관측에서 시작한다. 실적 일정 소스(DART/미국 실적 캘린더)는 후속 백로그이며 연결 전까지 `not_evaluable`을 유지한다.

# stock-v3 stance 정합 (WO-FCE-TOSS-PAPER-03)

stock-v2 운영 스냅샷 69건 재생에서 순간 `flipped=true` 조건은 0건, 안정된
`long_leaning && !transitioning`은 61건이었다. stock-v3는 순간 flip을 진입 필수에서
제외하되 값을 분석 스냅샷과 주문 evidence에 계속 기록한다. 안정된 롱 방향과 전환 중 배제는
그대로 필수다.

정책 의미가 달라졌으므로 stock-v2 검증 시간과 합치지 않는다. KR/US 각각 첫 stock-v3
정상 관측에서 4주 시계를 재시작하며 이벤트 사유에 이전·새 파라미터 버전을 함께 저장한다.

v2와 v3의 evidence 4, checklist 5/5, RR 1.5, entry score 75, 무효화가, data freshness,
long-only는 동일하다. 운영 표본에서 evidence는 69/69, checklist는 67/69 통과했으므로
펀딩·OI 부재를 이유로 이 임계를 낮추지 않았다. 주식 분석은 파생 신호를 unavailable 및
`used_by_evidence=false`로 기록하고 실제 `derivatives.signals`가 없으면 공용 confluence가
파생 evidence를 만들지 않는다.

재생 감사 명령과 전후 거부율은 [`WO-FCE-TOSS-PAPER-03.md`](WO-FCE-TOSS-PAPER-03.md)에
고정한다. 수리 후 동일 표본의 진입은 여전히 0건이며, entry score 75가 69/69를 정당하게
거부했다. 거래 수를 만들기 위한 임계 인하는 금지한다.


## 미장(US) 세션 정의와 실측 (WO-FCE-PAPER-ENTRY-REALITY-01, 2026-07-28)

- **세션 범위: 정규장 09:30~16:00 ET 만.** 프리·애프터 마켓은 범위 외.
- 생존 감시는 `stock_us` 가상 트랙이 담당한다(시장별 독립 — docs/EngineLiveness.md 참조).

### 실측 결과: 미장은 정상 동작 중이며, 진입 불능 원인은 게이트 미달이다

| 항목 | US | KR |
| --- | --- | --- |
| 분석 스냅샷 | **379건** | 14건 |
| 최종 관측 | **2026-07-27 14:26 UTC (당일 정규장)** | 2026-07-22 06:23 |
| 주문 | 3건 | 3건 |

게이트별 거부(US 누적): `entry_score` 31 · `confirmed_flip` 24 · `risk_reward` 22 ·
`checklist` 8 · `evidence` 5.
실측 예: AAPL entry_score 66 / 임계 75, NVDA RR 0.53 / 임계 1.5, 체크리스트 4/11 (요구 5/5).

**"왜 미장은 안 들어가느냐"의 답: 평가는 정상 수행되고 있고, 기준을 넘는 후보가 없었다.**
임계 완화는 하지 않는다(C1) — 이것은 고장이 아니라 설계된 보수성이며, 완화하면 검증의 의미가 사라진다.

---

## US·KR 수집 유실 구간 (WO-FCE-TOSS-US-STALL-01, 2026-07-29)

### 기록된 유실

| 구간(UTC) | 시장 | 사유 | 유실 |
| --- | --- | --- | --- |
| 07-28 13:51 ~ 07-28 20:00 | US 정규장 | 인증 자기잠금 래치 | 6.2시간 |
| 07-29 00:00 ~ 07-29 06:30 | KR 정규장 | 같은 래치(양 시장 동시 차단) | 6.5시간 |
| 07-28 00:03 ~ 07-28 11:55 | 전 트랙 | 하트비트 직렬화 정지(선행 WO) | 11.7시간 |

원시 수집 최종 기록: `toss_quotes` US `2026-07-28T13:50:56Z` · KR `2026-07-28T06:29:36Z`.
분석 스냅샷 최종: US `2026-07-28T13:48:06Z` · KR `2026-07-22T06:23:48Z`.

**이 구간은 검증 기간에서 제외된다(C5).** `stock_paper_tracks` 응답의 `elapsed_days` 는
유실 제외값이고, `calendar_days`·`lost_days`·`elapsed_label` 이 함께 실린다.
대시보드는 "경과 N일 (유실 M일 제외)"로 표기한다.

### 표본에 대한 정직한 진술

07-23 이후 주식 트랙은 **정지·래치 구간이 반복 포함**되어 있다. 따라서 이 기간의
평가 횟수·거부 분포로 4주 표본을 외삽하면 안 된다. 재외삽은 무중단 관측이 확보된 뒤에 한다.

### 세션 판정 기준

정규장만 수집한다(프리·애프터 제외). 판정은 토스 `market-calendar` 응답의 정규장 창을 그대로 쓴다.

- KR 정규장 09:00~15:30 KST — 응답에서 `today.integrated.regularMarket`
- US 정규장 09:30~16:00 ET — 응답에서 `today.regularMarket` (**평면 구조**)

이 비대칭은 토스 응답 스펙이며 통일하면 양쪽 다 `holiday` 로 오판한다.
판정 근거(파싱된 창의 UTC 시각)는 조기 반환 사유에 함께 기록되므로,
"미국 정규장인데 holiday" 같은 상황을 진단 API 에서 즉시 식별할 수 있다.

---

## KR·US 분리 집계 (WO-FCE-PERFORMANCE-REPORT-01, 2026-07-30)

성과 리포트는 주식을 **단일 트랙으로 합치지 않는다.** 시장별로 세션·유니버스·통화·
검증 시계가 모두 다르므로 합산 숫자는 어느 쪽도 대표하지 못한다.

| 항목 | KR | US |
| --- | --- | --- |
| 트랙키 | `stock_kr` | `stock_us` |
| 통화 | KRW | USD |
| 검증 시계 | `stock_paper_tracks.market='KR'` | `market='US'` |
| 최다 거부 게이트 | `stock_paper_entry_rejections` 의 `market='KR'` 집계 | `market='US'` |

`/api/system/paper/diagnosis` 의 `tracks.stock.top_reject_gate` 는 **시장 구분이 없는
단일 값**이다. 성과 리포트는 그 값을 쓰지 않고 `rejection_distribution()` 의
`(market, gate)` 집계에서 시장별 최다 게이트를 따로 뽑는다.

청산 완료 표본은 **매도 체결**(`stock_paper_fills.side='sell'`)이다. 매수만 있으면 청산
표본은 0이고 승률은 `None` 이 된다 — 미실현 손익을 승률로 환산하지 않는다.

### 2026-07-30 실측

| | KR | US |
| --- | --- | --- |
| 보유 | 3건 (미실현 **-25.53%**) | 3건 (미실현 **-13.86%**) |
| 청산 완료 | **0건** (매도 체결 0) | **0건** |
| 승률 | 표본 없음 | 표본 없음 |
| 검증 경과 | **D+1** (달력 8일 중 **유실 7일**) | **D+4** (달력 7일 중 유실 3일) |
| 최다 거부 게이트 | `confirmed_flip` | `confirmed_flip` (28회) |
| 커버리지 레인 발동 | `coverage_entry_attempt` 3건 (07-22) | 3건 (07-23) |

**KR 은 달력 8일 중 7일이 유실됐다** — 검증 시계가 사실상 1일만 돌았다. 4주 검증은
유실일을 제외한 실측일 기준이므로 이 속도로는 기한 내 표본이 모이지 않는다.
KR 의 거부 이벤트가 07-22T02:08 이후 한 건도 없는 것도 같은 원인을 가리킨다(평가 미수행).
