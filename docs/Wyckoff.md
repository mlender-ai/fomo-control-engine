# Wyckoff Engine v2

FOMO Control Engine의 와이코프 엔진은 주문 실행 신호가 아니라 보유 포지션의 구조 점검 근거를 만든다. 결과는 모두 결정론 계산이며, 마커의 `confidence`는 고정 상수가 아니라 아래 컴포넌트 합산으로 산출한다.

## Trading Range

이벤트 감지는 먼저 거래 레인지가 있을 때만 수행한다.

- 레벨 소스: 구조적 지지/저항 레벨 엔진의 상위 support/resistance
- 조건: 최근 60캔들 중 최소 20캔들 또는 45% 이상이 박스 안에서 종가 형성
- 폭 제한: 레인지 폭이 ATR 대비 너무 작거나 크면 제외
- 레인지가 없으면 `phase = trending`, 이벤트 목록은 빈 배열

## Display Contract

- `wyckoff_range=null`이면 임의 기간의 고가·저가로 관찰 박스를 만들지 않는다. `거래 레인지 없음`과 현재 추세만 표시한다.
- 레인지가 있어도 Phase A-E를 시간축에 균등 분할하지 않는다. 실제 판정된 phase 하나와 `evidence_event_ids`에 연결된 실제 이벤트 시각만 표시한다.
- Spring, UTAD, SOS, SOW 등은 감지된 이벤트가 있을 때만 마커로 그린다. 미검출 상태를 후보 패턴처럼 보이게 만들지 않는다.

## Event Set

Accumulation side:

- `selling_climax` / SC: 지지선 근처의 큰 거래량 매도 클라이맥스 후보
- `spring_candidate` / Spring: 레인지 하단 이탈 후 같은 캔들에서 박스 안으로 복귀
- `test_candidate` / Test: Spring 이후 지지선 위에서 약한 거래량 재확인
- `sos_confirmed` / SOS: 거래량을 동반한 레인지 상단 돌파
- `lps_candidate` / LPS: SOS 이후 돌파 레벨 재지지 후보

Distribution side:

- `buying_climax` / BC: 저항선 근처의 큰 거래량 매수 클라이맥스 후보
- `utad_candidate` / UTAD: 레인지 상단 돌파 후 같은 캔들에서 박스 안으로 복귀
- `sow_confirmed` / SOW: 거래량을 동반한 레인지 하단 이탈
- `lpsy_candidate` / LPSY: SOW 이후 이탈 레벨 재저항 후보

## Confidence Formula

`confidence = depth_significance + return_speed + volume_confirmation + level_strength + liquidity_confirmation`

각 컴포넌트 범위:

- `depth_significance` 0-30: 이탈/돌파 깊이를 ATR 대비로 평가
- `return_speed` 0-25: 박스 안으로 복귀하거나 리테스트가 확인되는 속도
- `volume_confirmation` 0-25: 실체결 delta가 있으면 방향 정렬, 없으면 상대 거래량
- `level_strength` 0-20: 구조 레벨 엔진의 level score x 0.2
- `liquidity_confirmation` 0-15: WO-FCE-28 유동성 스윕 교차 확인. Weak +10, Mid +12, Strong +15

UI와 API는 `components`를 함께 제공해야 한다. `62`, `58` 같은 고정 신뢰도 값은 사용하지 않는다.

`liquidity_confirmation`은 동일 캔들 또는 동일 경계에서 Spring/UTAD와 스윕이 같은 가격 행동을 설명할 때만 추가한다. 이 경우 UI는 와이코프 이벤트를 1차 표기로 유지하고, 스윕은 근거 상세로만 표기한다.

## Phase

이벤트 시퀀스가 부족하면 `undetermined`로 둔다.

- Accumulation Phase C: Spring/Test 중심
- Accumulation Phase D: SOS 중심
- Accumulation Phase E: LPS 중심
- Distribution Phase C: UTAD 중심
- Distribution Phase D: SOW 중심
- Distribution Phase E: LPSY 중심

`evidence_event_ids`는 phase 판정에 사용된 최근 이벤트 ID 목록이다.

## MTF Alignment

기본 분석 타임프레임은 현재 차트 타임프레임이고, 상위 타임프레임은 4h 캔들을 1D로 집계해 별도 분석한다.

- `aligned`: 현재 국면과 상위 국면이 같은 방향
- `conflicting`: 롱 관점 매집 신호가 상위 분산/하락과 충돌하거나, 숏 관점 분산 신호가 상위 매집/상승과 충돌
- `neutral`: 판정 근거 부족 또는 방향 없음

Health Score v2는 포지션 방향과 상위 국면이 충돌하면 thesis 점수에 감점을 적용한다.
