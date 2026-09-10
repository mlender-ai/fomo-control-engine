# 포지션 가시성 (Position Visibility)

> 정본: 열린 포지션을 **"없다"고 말해도 되는 조건**. 2026-09-09 사건의 구조적 수리.
> 관련: [`EngineLiveness.md`](EngineLiveness.md) · [`AlertAudit.md`](AlertAudit.md) · [`ObservationIntegrity.md`](ObservationIntegrity.md)

## 0. 사건

2026-09-09 11:55 KST 일일 요약 상단:

```
밤새 알림 요약
억제된 알림은 없습니다.

열린 포지션이 없습니다.        ← 이 시각 계좌에는 ZECUSDT 숏이 열려 있었다
                                  (0.657 ZEC · 10x · 미실현 -86.75 USDT)
```

사용자 보고: "지금 포지션 잡혀있는데 열린 포지션 없다고 나오네."

## 1. 원인 — 근거는 있었고, 읽는 코드가 없었다

`format_positions_summary()` 는 `payload["positions"]` 가 비었다는 **사실 하나**로
"열린 포지션이 없습니다."를 단정했다. 그런데 그 빈 리스트에 도달하는 경로가 넷이다:

| 경로 | 실린 근거 | 읽던 곳 |
| --- | --- | --- |
| ① 정말 없다 | `open_count == 0` · `status == "ok"` | — |
| ② 동기화 주기가 죽었다 | `sync_failed` | **없음** |
| ③ 결과가 낡았다 | `sync_stale` · `sync_stale_note` | **없음** |
| ④ 원장엔 있는데 분석이 실패했다 | `positions_unavailable` · `open_count` | 정기 펄스만 |
| ⑤ 거래소를 못 읽었다 | `status`(`error`·`permission_error`·`not_configured`·`not_active`) | **없음** |

②③⑤를 ①로 표시하면 **침묵이 정상으로 위장한다.** 이 저장소가 이미 두 번 적은 원칙이고
(`ENGINE-LIVENESS-01` D1, `ALERT-SILENCE-01` 3-1), ④만 정기 펄스에서 반쯤 고쳐져 있었다.

증폭 요인이 하나 더 있었다. `WorkerManager._sync_positions` 는 동기화 훅이 실패하면
`_last_sync_payload` 를 **빈 페이로드로 교체**했다. 그래서 실패 한 번이 곧 "포지션 없음"이었고,
그 상태로 일일 요약·정기 펄스가 나갔다.

## 2. 규칙

> **빈 목록은 "없음"의 근거가 아니다.** 동기화가 성공했고, 신선하고, 원장의 열린 포지션이
> 전부 렌더된 경우에만 "없다"고 말한다.

판정은 `app/notify/position_visibility.py` 한 곳에서만 만든다.

| 함수 | 계약 |
| --- | --- |
| `observation_gap_lines(payload, rendered=)` | "없음"으로 읽으면 안 되는 사유 목록. 없으면 `[]` |
| `can_assert_empty(payload, rendered=)` | 위가 비었을 때만 `True` |
| `ledger_fallback_lines(rows)` | 원장 행만으로 만든 대체 목록(네트워크 없음) |

**게이트가 아니라 문장이다.** 이 모듈은 알림을 막지 않는다 — 막으면 그것이 곧 침묵이다.
사유를 붙여 **더 많이** 말하게 한다.

### 소비 경로

| 경로 | 동작 |
| --- | --- |
| 일일 요약 (`AlertEngine._positions_block`) | 사유를 싣고, 단정 불가면 **원장을 직접 읽어** 가진 것을 적는다 |
| `/positions` · 목록 콜백 (`format_positions_summary`) | 사유를 싣는다. 일부만 보이면 목록과 공백을 **함께** 보낸다 |
| 정기 펄스 (`pulse_candidate(gap_lines=)`) | 공백 위에서 "감시 정상"·"전부 정상"을 찍지 않는다 |

### 원장 대체 경로

동기화가 죽었을 때 "아무것도 없다"고 말하는 대신 **가진 것을 말한다.**
`service.open_positions_ledger()` 는 `minimal_position_payload` 와 같은 이유로 DB 한 번만
읽는다 — 동기화를 죽인 그 원인(거래소·차트 API)으로 같이 죽으면 대체가 아니다.
관측값(현재가·판정)은 없으므로 진입 시점 사실만 적고 그 출처를 명시한다.

## 2-1. 2차 보고 — 사유가 없어도 **출처**는 댄다

사용자 2차 보고(2026-09-09 13:16 정기 펄스): "여전히 없다고 나오는데 뭔소리야."

1차 수리는 **사유가 있을 때**만 말한다. 동기화가 성공하고 신선하며 원장도 0건이면
`can_assert_empty` 는 참이고 화면은 "보유 포지션 없음 — 감시 정상"을 찍는다. 우리 쪽
판정은 옳다. 그런데 **거래소 앱에는 포지션이 보인다.** 이 어긋남은 사유가 아니라
**거래소가 그 포지션을 응답에 담지 않았다**는 뜻이고, 1차 수리는 그것을 못 잡는다.

그래서 "없음"에 **출처**를 붙인다:

```
열린 포지션이 없습니다. (거래소 USDT-FUTURES 0건 · 원장 0건 · 동기화 4분 전)
보유 포지션 없음 (거래소 USDT-FUTURES 0건 · 원장 0건) — 감시 정상 동작 중입니다.
```

거래소 앱에 포지션이 보이는데 이 줄이 `거래소 … 0건` 이면, 문제는 **표시가 아니라 거래소
조회**다 — 계정 유형 변경(classic→통합), `productType`·`marginCoin` 불일치가 정확히 이
자리에서 0건을 만든다. `productType` 을 함께 적는 이유가 그것이다(없으면 0건을 해석할 수 없다).

`empty_evidence_line(payload)` 가 만들고 `synced`(거래소 응답 행 수) · `product_type` ·
`open_count` · `sync_age_seconds` 를 읽는다. `_sync_bitget_positions` 가 `product_type` 을
응답에 싣는다.

## 2-2. 머지 ≠ 서버 반영

이 수리는 **워커 프로세스를 재기동해야 반영된다.** `docs/RESTART_RUNBOOK.md` §4 가 같은
사실을 이미 적고 있다 — "머지해도 돌고 있는 프로세스는 옛 코드 그대로다."

```bash
cd ~/fomo-control-engine && git pull
bash scripts/local/stop-supervisor.sh && bash scripts/local/start-supervisor.sh
```

반영 확인 — 다음 펄스·요약의 "없음" 줄에 괄호 출처가 붙으면 새 코드다.

## 3. 부수 수리

- **실패 주기가 마지막 성공 스냅샷을 지우지 않는다** (`_sync_positions`). 실패 사실은
  `sync_failed` + `sync_failed_note` 로 따로 싣는다 — 낡은 값을 신선한 척 보내지 않는다.
  라이프사이클 큐도 성공 주기에서만 채운다(실패 페이로드로 재큐잉하면 진입 알림이 중복된다).
- **한 심볼의 분석 실패가 목록 전체를 삼키지 않는다** (`list_live_positions`). 이전에는
  리스트 컴프리헨션 안에서 `HTTPException` 이 그대로 올라와 `/positions` 응답 자체가
  사라졌고, 그 침묵은 "포지션 없음"과 구분되지 않았다. `sync_live_positions` 와 같은 규약으로
  `positions_unavailable` 에 남긴다.

## 4. 회귀 고정

`backend/tests/test_open_position_visibility.py` 가 네 명제를 고정한다:

1. 근거 없이 "없음"을 단정하지 않는다(동기화 실패·낡음·거래소 상태·관측 실패·설명 없는 공백)
2. 실패한 주기가 마지막 성공 스냅샷을 지우지 않는다
3. 단정할 수 없으면 원장을 읽는다 — 그 경로는 네트워크를 타지 않는다
4. **대조군** — 정상일 때 문구는 그대로다. 오탐도 거짓말이다

## 5. 이 문서가 답하지 않는 것

포지션이 **원장에서 사라지는** 경우는 다른 문제다. `_sync_bitget_positions` 는 거래소
목록에 `alert_closure_confirm_ticks` 회 연속 부재한 포지션을 auto-close 한다(WO-44 Part C).
거래소가 빈 목록을 정상 응답으로 돌려주는 상황(계정 유형 변경·productType 불일치)에서는
그것이 가짜 종료가 될 수 있다. 이번 수리는 그 판정을 바꾸지 않았다 — 표본 없이 트레이딩
안전 경로를 추측으로 바꾸지 않는다(`AGENTS.md` 게이트 4). 대신 **화면이 그 사실을 감추지
않도록** 했다. 실제로 가짜 종료가 관측되면 그때 별도 WO 로 다룬다.
