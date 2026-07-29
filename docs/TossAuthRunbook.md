# Toss Open API 인증 진단 런북

## 먼저 실행

설정 화면의 **Toss 인증 진단** 또는 `GET /api/system/toss/auth-diagnosis`를 실행한다. 결과에는 토큰·클라이언트 시크릿이 포함되지 않는다. `status_code`, Toss 원문 `error_code`·`error_message`, `request_id`만 저장한다.

## 단계별 해석

| 실패 단계 | 확인할 운영 항목 |
|---|---|
| `token` | Client ID/Secret 짝, 운영·샌드박스 키와 Base URL 혼용 여부 |
| `market_calendar` | API 사용 신청 승인 상태, 기본 시장정보 상품 권한 |
| `market_data` | 실시간 시세 상품 권한, 허용 공인 IP |
| `market_chart` | 차트/과거시세 상품 권한 |
| `stock` | 종목정보 상품 권한 |
| `ranking` | 랭킹 상품 권한 |

401인데 토큰 단계만 성공하면 코드가 아니라 데이터 API 권한/환경 혼용 가능성이 높다. Toss 개발자 콘솔에서 다음을 순서대로 확인한다.

1. Open API 사용 신청이 `승인 완료`인지 확인한다.
2. 발급 키의 환경(운영/샌드박스)과 `TOSS_BASE_URL` 환경이 일치하는지 확인한다.
3. 시세·차트·종목·랭킹 상품 권한이 각각 활성인지 확인한다.
4. IP 제한을 사용한다면 현재 서버의 공인 IP가 등록됐는지 확인한다.
5. Toss 문의 시 실패 단계, 원문 코드/메시지, `request_id`, 발생 시각만 전달한다. 키/토큰은 전달하지 않는다.

인증 실패 중에는 해당 시장 수집과 검증 시계를 시작하지 않는다. 복구 뒤 첫 `status=observed` 수집 시점이 독립 4주 시계의 시작점이다.


---

## 자동 재시도·백오프 (WO-FCE-TOSS-US-STALL-01, 2026-07-29)

> **모든 차단에는 자동 재시도 경로가 있어야 한다. 재시도하지 않기 때문에 해제될 수 없는 구조는
> 차단이 아니라 영구 정지다.**

### 사고: 자기잠금 래치로 20.8시간 정지

`_authentication_blocked` 는 401 을 받으면 시장을 등록하고, 이후 `collect_market` 은 **진입 즉시
반환**했다(API 호출 자체를 안 함). 그런데 해제는 "수집을 성공적으로 마쳤을 때"뿐이었다.

```
401 → 차단 등록 → 호출 안 함 → 성공 못 함 → 차단 유지 → …
```

실측(2026-07-28 13:51 UTC ~ 07-29 10:39 UTC):

| 관측 | 값 |
| --- | --- |
| KR·US `market-calendar` 호출 | **0건** (20.8시간) |
| 유실 세션 | 07-28 미국 정규장 잔여 6시간 + 07-29 한국 정규장 6.5시간 |
| `toss_stock_scout` | runs 8,076 · 오류 0 · 10초마다 "ok" |
| 프로세스 | 22.7시간 무중단 (재시작으로도 안 풀림) |
| 실제 해제 계기 | 사람이 `/api/system/toss/auth-diagnosis` 호출 |

잡은 정상, 하트비트 정상, 오류 0. **잡 단위 감시로는 잡 내부 분기의 죽음을 볼 수 없다.**

### 현재 정책 — 차단 3종 통일 구조

모든 차단은 `{status, reason, blocked_until, attempt_count, last_error}` 로 통일된다
(`backend/app/toss/blocks.py`). `blocked_until` 이 지나면 **반드시 실호출로 재시도**한다.

| 사유 | 백오프(초) | 비고 |
| --- | --- | --- |
| `authentication_failed` | 60 → 120 → 240 → 480 → 900 | 재시도 직전 캐시 토큰 폐기(폐기된 토큰으로 재시도하면 즉시 재차단) |
| `edge_blocked` | 60 → 300 → 900 → 1800 → 1800 | IP 미등록은 사람이 고쳐야 하므로 간격을 길게(과거엔 래치 없이 10초마다 재호출) |
| `maintenance` | 900 고정 | 기존 정책 유지 — 원래 TTL 이 있어 자기잠금이 아니었다 |

- 상한에 도달해도 재시도는 **멈추지 않는다**.
- 사유가 바뀌면 `attempt_count` 를 초기화한다(다른 고장이면 다시 빠르게 확인).
- 수집 성공 시 차단 해제 + 복구 로그 1건(`toss <시장> 수집 복구`).
- KR·US 는 독립 차단 — 한쪽 차단이 다른 쪽을 막지 않는다.

### 수동 개입이 필요한 조건

자동 재시도가 있으므로 **대부분은 개입이 불필요하다.** 아래일 때만 사람이 붙는다.

1. `attempt_count` 가 계속 늘고 `authentication_failed` 가 30분 이상 지속 → 자격증명·권한 문제.
   위의 단계별 해석표로 진단한다.
2. `edge_blocked` 지속 → 공인 IP 등록 필요(재시도로는 절대 안 풀린다).
3. `blocked` 가 있는데 `retry_in_seconds` 가 줄지 않음 → 래치 회귀. 이 문서 위반이므로 코드를 본다.

### 관측

```bash
curl -s localhost:8875/api/system/paper/diagnosis | python3 -m json.tool | grep -A 30 toss_collection
```

`toss_collection.blocks` 가 현재 차단, `last_outcomes` 가 시장별 마지막 반환 사유다.
정지 알림에도 사유가 함께 실린다 — 예: `authentication_failed 330분 지속 (재시도 60초 후 · 5회차)`.

### 세션 판정 비대칭은 버그가 아니다

`_session_state` 의 KR/US 분기는 **토스 응답 스펙 차이**다(2026-07-29 원본 실측).

```
KR: {"date", "integrated": {"preMarket", "regularMarket", "afterMarket"}}
US: {"date", "dayMarket", "preMarket", "regularMarket", "afterMarket"}   ← 평면
```

분기를 "정리"해서 한쪽으로 통일하면 **양쪽 모두 `holiday` 로 오판**한다
(`test_swapping_the_branch_breaks_both_markets` 가 고정).
