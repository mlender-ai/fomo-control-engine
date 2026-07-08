# WyckoffAudit — 와이코프 엔진 정확성 감사 (WO-FCE-43 Part B)

사용자 보고: "와이코프가 제대로 나오는 건지 모르겠음" + NBISUSDT에서 "매집 Phase A"와
"UTAD 예" 동시 출력(자기모순). 골든 케이스 6종 + 모순 재현 케이스로 감사했다.

테스트: `backend/tests/test_wyckoff_golden.py` (전부 통과, 회귀 고정)

## 골든 케이스 대조표

| # | 픽스처 | 기대 | 수정 전 실제 | 수정 후 |
|---|---|---|---|---|
| 1 | SC→AR→ST→Spring→SOS 완전 매집 | side=accumulation, phase D/E, spring·sos 플래그 | ✅ 통과 (단, 허위 Test 이벤트 포함 — 결함 ① 참조) | ✅ 통과 |
| 2 | BC→AR→ST→UTAD→SOW 분산 대칭 | side=distribution, phase D/E, UTAD 라벨 유지 | ✅ 통과 | ✅ 통과 |
| 3 | 상승 추세 무레인지 | phase=trending, 이벤트 0 (억지 감지 금지) | ✅ 통과 | ✅ 통과 |
| 4 | 하락 추세 무레인지 | phase=trending, 이벤트 0 | ✅ 통과 | ✅ 통과 |
| 5 | Spring+UTAD 대칭 혼합 | **undetermined / neutral** (억지 판정 금지) | ❌ **accumulation_phase_c 오판** | ✅ 통과 |
| 6 | SC/AR/ST만 (Phase A 초기) | side=accumulation, phase A | ✅ 통과 | ✅ 통과 |
| 회귀 | 매집 우세 + 저항 상단 스윕 (NBISUSDT류) | 매집 유지 + 스윕은 UT 재명명 + 혼합 명시 | ❌ "UTAD" 그대로 (자기모순 표기) | ✅ 통과 |
| 회귀 | 이벤트 부족 레인지 | 억지 phase 없이 판정 보류 | ✅ 통과 | ✅ 통과 |

## 발견 결함과 원인 분류

### ① 허위 파생 Test — 매집 과대판정의 근원 (분류: 이벤트 파생 논리)

`_derived_retest_events`의 Spring 후속 Test 감지에 **지지선 근접 조건이 없었다**.
스프링 뒤 7캔들 내 아무 저볼륨 양봉이나 Test로 승격됐고, `depth_significance`가
"레벨과의 거리"를 그대로 가산해 **지지선에서 3.6pt 떨어진 일반 캔들이 신뢰도 74**
(원본 Spring 59보다 높음)를 받았다.

- 결과: 매집 측 최고 신뢰가 인위적으로 부풀어 혼합 신호(골든 #5)가 매집으로 오판되고,
  실데이터에서 "매집 Phase A/C + UTAD 예" 같은 동시 출력이 만들어졌다.
- 수정: 파생 Test에 직접 감지기와 동일한 근접 조건 부여 —
  `support ≤ low ≤ support + threshold` (`engine.py _derived_retest_events`).

### ② UTAD 명칭의 맥락 오류 (분류: 이벤트 방향/명명 배정)

이벤트 감지기는 "저항 상단 스윕 후 복귀"를 무조건 `UTAD`로 명명했다. UTAD는
**분산 국면을 전제**하는 명칭(UpThrust After Distribution)이므로, 매집 우세로 판정된
레인지에서는 교과서적으로 **UT(업스러스트)**다. "매집 Phase A + UTAD"는 이 명명이
만든 자기모순이다.

- 수정: `_contextualize_event_labels` — side=accumulation일 때 utad_candidate의 표시
  라벨을 `UT`로 재명명 + `context_note` 부착. **type은 유지**(시그니처·계보 안정성).
  분산 판정에서는 UTAD 유지. 컨플루언스 방향 매핑에 UT→short 추가 (하락 경계 유지).

### ③ 혼합 신호 은폐 (분류: phase 시퀀스 논리)

side 판정은 우세 측 이벤트만으로 phase를 정하지만, 반대측 고신뢰 이벤트는 마커로
그대로 노출됐다 — 사용자에겐 "매집인데 UTAD?"로 보인다.

- 수정: `wyckoff.conflict_note` — 반대측 신뢰도 ≥65 이벤트 공존 시
  "매집 우세 판정이나 반대측 UT 72점 공존 — 혼합 신호" 명시.
- 1줄 판정(WO-43 Part A)도 같은 조건에서 confidence_class를 "약"으로 강등.

## 판정 보류 재검증 (억지 phase 금지)

- 레인지 미형성(trending) → 이벤트 0, phase=trending ✅
- 레인지 있으나 이벤트 근거 부족 → undetermined/neutral ✅
- 1줄 판정: undetermined → "레인지 미형성 — 판단불가"로 발행 ✅ (`test_undetermined_wyckoff_reports_hold`)

## 남은 관찰 항목 (결함 아님, 추적)

- `_phase_from_events`의 ±8 신뢰도 마진은 결함 ① 수정 후 골든 전부에서 올바르게
  동작. 실데이터에서 경계 오판이 재발하면 마진을 이벤트 수 가중으로 확장 검토.
- ST(secondary_test)가 매집/분산 양측에 같은 type 문자열을 쓰는 것은 side 필드로
  구분되므로 판정에는 무해 — 시그니처 집계에서도 direction으로 분리됨.
