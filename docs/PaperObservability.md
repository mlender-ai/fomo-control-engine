# 페이퍼 3트랙 관측성 (침묵 금지)

WO-FCE-PAPER-OBSERVABILITY-01 정본. 대원칙: **침묵은 금지된다. 모든 미발생은 사유와 함께 관측 가능해야 한다.**

사용자가 (a) 신호가 없어 조용한 건지 (b) 엔진이 안 도는 건지 (c) 뮤트인지 (d) 서버가 죽었는지를 구분할 수 있어야 한다. 이 문서는 crypto·stock·poly 3개 페이퍼 트랙이 그 구분을 가능하게 하는 계약·정책·표면을 정의한다.

## 공통 이벤트 계약

정본 헬퍼: `app/notify/paper_events.py`의 `track_event(track, kind, symbol, *, detail, ts)`.

이벤트는 `{track, kind, symbol, ts, detail}` 형태다.

- `track`: `"crypto" | "stock" | "poly"`
- `kind`: `"opened" | "closed" | "rejected_summary" | "skipped" | "error"`
- `symbol`: 종목/마켓 식별자. 집계·트랙 전역 이벤트는 `"*"`.
- `ts`: ISO8601 UTC.
- `detail`: 트랙별 부가 정보. 포맷터가 사람이 읽는 문장으로 렌더한다.

### 크립토 회귀 금지

crypto 트랙은 기존 `{kind, reason, trade}` 계약을 그대로 유지한다. `format_paper_event`는 `track` 키 유무로 경로를 분기하므로 crypto 이벤트는 이 모듈을 거치지 않고 기존 포맷을 그대로 사용한다. crypto 알림 포맷은 변경하지 않는다.

## 배선 (누가 무엇을 태우는가)

| 트랙 | 구동 잡 | 엔진 | 이벤트 → 텔레그램 |
|---|---|---|---|
| crypto | `sync_positions` | `run_paper_engine` | `_sync_positions` → `_send_paper_events` (기존) |
| stock | `toss_stock_scout` | `run_stock_paper_engine` | `_collect_toss_stocks` → `_send_paper_events` (신규 배선) |
| poly | `polymarket_paper` | `run_poly_paper_engine` | `_collect_polymarket` → `_send_paper_events` (신규 배선) |

이전에는 stock·poly가 `events` 키 자체를 반환하지 않았고 `_send_paper_events` 호출도 없어 **텔레그램 경로가 구조적으로 존재하지 않았다**(관측 불능의 핵심 원인). 이제 세 트랙 모두 같은 경로를 태운다.

## 거부는 집계, 스킵은 억제

- **거부(`rejected_summary`)**: 전 종목 거부를 개별 발송하면 스팸이 되고 사용자가 다시 뮤트하는 악순환이 된다. 한 주기의 거부는 **1건의 집계 이벤트**로 요약한다: `평가 N · 진입 M · 거부 K · 최다 거부 게이트 X`. 진입이 0이어도 이 요약이 오면 사용자는 시스템이 살아있음을 안다.
- **스킵(`skipped`)**: 비활성·미구성 등 미발생 사유. 연속 동일 사유는 스팸이므로 억제한다. 정책은 `NotificationState.register_paper_skip`:
  - 최초 등장(**상태 전이**) → 1회 발송.
  - 같은 날 같은 사유 반복 → 억제(억제 횟수를 `paper_skip_state[key].suppressed`에 누적).
  - 날짜가 바뀌면(**일 1회 리마인더**) → 다시 1회 발송.
  - 엔진이 실제로 평가를 수행하면(`effective_run=True`) 해당 트랙의 skip 상태를 지워, 다음 스킵을 새 상태 전이로 만든다(회복 후 재발을 놓치지 않기 위함).
  - 억제 상태는 `notification_state`에 영속화되어 재기동에도 유지된다.

`skipped`만 억제 대상(`SUPPRESSIBLE_KINDS`)이다. `opened`·`closed`·`rejected_summary`·`error`는 억제하지 않는다.

## effective run — "돌지만 안 돈다"의 탐지

워커 하트비트에 `last_effective_run_at`을 추가했다(마이그레이션 `0031_worker_effective_run.sql`).

- `last_success_at`은 조기 반환(비활성/미구성)에도 갱신된다 → 잡이 "돌고 있다"는 착시를 준다.
- `last_effective_run_at`은 엔진이 **실제로 평가를 수행한** 마지막 시각만 기록한다. 조기 반환(`effective_run=False`)은 갱신하지 않는다.
- 두 값의 괴리 = "잡은 success지만 한 번도 평가하지 않았다". 이 괴리가 조용한 스킵(D3)을 드러낸다.

## 스플릿 플래그 경고

주식 페이퍼는 두 플래그로 나뉜다: 구동 잡(`toss_stock_scout_enabled`)과 엔진(`stock_paper_engine_enabled`). 엔진이 켜졌는데 구동 잡이 꺼져 있으면 상태 화면엔 "활성"으로 보이지만 한 번도 실행되지 않는다.

`WorkerManager`는 기동 시 이 불일치를 감지해 **명시적 경고**를 발행한다:
- 워커 로그(`worker flag inconsistency: ...`).
- `status()["flag_warnings"]` (상태 API).
- `/api/system/paper/diagnosis`의 `flag_warnings`.

두 플래그를 자동 파생시키지 않고 경고로 노출하는 쪽을 택했다. 운영자가 의도적으로 구동 잡을 껐을 때 엔진 플래그만으로 잡을 몰래 켜면 놀라움이 되기 때문이다. **어느 쪽이든 "한쪽만 켜진 상태가 조용히 유지되는 것"은 금지**이며, 이 경고가 그 조용함을 깬다.

## 진단 표면 — `GET /api/system/paper/diagnosis`

"왜 조용한가"의 답을 한 화면에서 낸다. 레포 정밀 실사를 반복하지 않기 위한 산출물이다. 3트랙 각각에 대해:

- `enabled_flags`: 구동 잡 + 엔진 플래그.
- `last_effective_run_at`, `last_success_at`.
- `top_reject_gate`: 최다 거부 게이트(stock은 최근 1일 원장 기준).
- `telegram_wired`: 알림 경로가 켜져 있는지.
- `mute_state`: `{is_muted, muted_until}`.
- `ready_to_start` / `ready_to_start_reason`.

최상단에 `flag_warnings`와 침묵 금지 원칙 문구를 함께 반환한다.

## 남은 항목 → **WO-FCE-ENGINE-LIVENESS-01에서 청산 완료 (2026-07-27)**

이 WO에서 배선·계약·억제·진단 표면·effective run·스플릿 플래그 경고를 구현했고,
미완으로 남겼던 작업 5·6은 후속 WO에서 전부 완결했다. 정본: [`docs/EngineLiveness.md`](EngineLiveness.md).

| 미완 항목 | 상태 |
| --- | --- |
| 일일 요약 트랙별 생존 라인 + 뮤트 관통 생존 신호 (작업 5) | ✅ `liveness.daily_liveness_lines()` + `AlertEngine.maybe_send_daily_summary(liveness_lines=)` — 뮤트 중엔 생존 라인만 발송 |
| 페이퍼 잡 stale 감지 대상 포함 (작업 5 일부) | ✅ `liveness.TRACKED_JOBS` 에 3트랙 전부 포함, `worker_liveness` 잡(5분)이 상시 평가 |
| DB 성장 감시 (작업 6) | ✅ `liveness.infra_alerts()` — DB 10GB·디스크 20GB 임계 |
| keepalive 재시작 가시화 (작업 6) | ✅ `logs/restarts.jsonl` + `process_restarted` 알림 + 진단 응답 `liveness.restarts_24h` |
| 검증 시계 유실일 보정 (작업 6) | ✅ `liveness.elapsed_excluding_gaps()` — "경과 N일 (유실 M일 제외)" |

**미완 머지의 교훈**: 이 WO가 작업 5·6을 미완으로 두고 머지한 사이, 정확히 그 미완 항목이 필요했던
3트랙 4일 정지가 발생했고 아무도 알지 못했다. 관측성 항목은 부분 머지하지 않는다.
