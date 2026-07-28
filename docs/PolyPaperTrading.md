

## macro 카테고리 처리 (WO-FCE-PAPER-ENTRY-REALITY-01, 2026-07-28)

**결정: 옵션 B — macro 를 유니버스에서 정직하게 제외한다.** (`client.SUPPORTED_CATEGORIES`)

### 왜 제외인가
`estimator.py` 는 `category == macro` 이면 베이스레이트 제공자가 없어 즉시
`macro_base_rate_provider_unavailable` 로 거부한다. 근거 없는 확률을 발행하는 것은 C2(정직성)
위반이므로 추정을 억지로 만들지 않는다. 그렇다고 남겨두면 **매 틱 거부 카운트만 오염**시킨다.

실측(2026-07-28): 유니버스 crypto 534 / macro 99. macro 99개가 매 틱 거부 40~44건의 대부분을
차지해 최다 거부 게이트가 영구히 `macro_base_rate_provider_unavailable` 로 고정됐고,
**crypto 트랙의 진짜 거부 사유가 가려졌다.** 평가 대상이 아닌 것을 거부로 세면 지표가 오염된다.

### 제외 후 유니버스
crypto 534개 유지(84.4%). 최근 24시간 2,904건 평가 · 전부 `estimate_quality=high` ·
edge 평균 0.0203 · 최대 0.49. 폴리 트랙의 검증 가치는 유지된다.

### 재도입 조건 (후속 WO 백로그)
macro 를 되살리려면 **근거 있는 베이스레이트 소스**가 선행되어야 한다:
과거 FOMC 결정 분포 · CPI 서프라이즈 분포 · 공개 선물/스왑 내재 확률 등.
반드시 `base_rate` + `evidence[]{claim, source, observed_at}` + `confidence_band` 를 동반하고,
근거가 부족하면 `estimate_quality=low` 로 분류해 진입 후보에서 빼되 **평가는 수행**한다
(거부와 품질 미달은 다른 상태다).
