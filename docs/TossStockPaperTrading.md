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

엄격 후보는 시장별 상위 18개로 제한한다. 별도 coverage 스캐너는 시장별 2종목씩 버전 고정 유니버스를 순환하며 현재가·warnings·호가·체결·가격제한·1분봉을 모두 받은 종목만 실행 표본 후보로 만든다. 후보 일봉 200개는 종목별 하루 한 번만 갱신하고 나머지 15분 주기는 저장본을 공용 분석에 사용한다. 최초 백필 버스트도 5 TPS 공유 버킷이 직렬화한다. 시장별 최대 5개 포지션이 모두 후보 밖인 최악 조건까지 계산한 값이다. 비후보 유니버스는 시장별 한 종목씩 순환하며 일봉 200개를 백필해 약 17분에 100종목을 한 번 순회한다. `X-RateLimit-Remaining`이 20% 아래로 내려가면 공유 버킷이 선제 감속하고 429는 Retry-After와 지수 백오프로 재시도한다.

## 관측·복기

GE의 `주식 트랙`은 시장별 시작일/28일, 원통화 NAV, 프록시 수익률, 미체결 사유, 최근 fill의 수수료·세금을 표시한다. GR 요약은 같은 주식 트랙으로 연결한다. `stock_paper_events`가 세션·가격제한·VI·유동성·데이터 누락을 실제 발생 건수로 보존한다.

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
