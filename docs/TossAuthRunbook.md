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

