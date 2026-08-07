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


## 알림 정책 개정 (WO-FCE-PAPER-ENTRY-REALITY-01, 2026-07-28)

> **거부는 조회 대상이지 알림 대상이 아니다.**

선행 WO는 거부를 "개별 발송 대신 집계 1건"으로만 규정하고 **발송 빈도**를 빠뜨렸다.
그 결과 폴리는 60초 폴링마다 집계 1건 = **일 1,440건**을 발송했고(2026-07-28 실측),
스팸 → 사용자 뮤트 → 침묵 재발이라는 금지된 악순환의 문턱까지 갔다. 명세 결함이므로 정정한다.

| 이벤트 | 정책 |
| --- | --- |
| `opened` · `closed` | **즉시 발송** (사용자가 원하는 것 = 무엇이 일어났는가) |
| 사망/복구 경보 · 생존 신호 | 즉시 발송, 뮤트 관통 (WO-ENGINE-LIVENESS-01 유지) |
| `rejected_summary` | **최다 거부 게이트(`top_reject_gate`)가 바뀔 때만 1건** + 일 1회 리마인더 |
| `skipped` | 사유 전이 시 1건 + 일 1회 리마인더 |
| 상세 거부 분포 | 알림 없음 — `/api/system/paper/diagnosis` · 대시보드에서 조회 |

구현 주의: `suppression_key` 는 `rejected_summary` 에 대해 **거부 건수가 아니라 top_reject_gate** 를
상태로 쓴다. 건수(40→41→42)는 매 틱 흔들리므로 키에 넣으면 스팸이 그대로 유지된다.
또한 `clear_paper_skips_for_track()` 은 `rejected_summary` 상태를 지우지 않는다 —
거부 집계는 평가가 정상일 때 매 틱 발생하므로 effective_run 마다 리셋하면 정책이 무력화된다.

**기대 결과**: 평상시 텔레그램은 조용하고, 진입이 생기면 울린다. (지금까지의 정반대)

---

## 성과 리포트 (WO-FCE-PERFORMANCE-REPORT-01, 2026-07-30)

> **생존 라인은 "살아있음"을, 성과 리포트는 "무엇을 하고 있음"을 보고한다.**

역할 분리가 이 WO의 핵심이다. 선행 WO들은 트랙이 **죽었는지**를 관측 가능하게 만들었지만
(생존 라인 · 데드맨 스위치), **무엇을 하고 있는지**는 대시보드를 열어야만 볼 수 있었다.
사용자가 원한 승률·PnL은 알림 경로에 존재하지 않았다.

| 표면 | 담당 | 배선 |
| --- | --- | --- |
| 생존 라인 | 살아있음 / 마지막 실제 평가 / 최다 거부 게이트 | `worker/liveness.py::daily_liveness_lines` |
| 일일 성과 | 보유·청산·승률·평균 R (4트랙 전부) | `notify/performance_report.py::format_paper_performance` |
| 주간 성과 | 4주 검증 진행도 · N≥30 도달 예상 여부 · 벤치마크 | `format_weekly_performance` (잡 `weekly_performance_report`) |
| 청산 즉시 알림 | 결과 + **트랙 누적 승률·N** | `format_track_record_suffix` (`_send_paper_events`) |

### 주식 침묵의 실제 원인 (진단 정정)

WO 본문의 D1("주식 진입이 0이므로 `opened` 이벤트가 없다")은 **실측과 다르다.**
2026-07-30 실측: 주식은 KR 3건·US 3건을 실제로 진입했다(KR 07-22, US 07-23 체결).
침묵의 원인은 진입 부재가 아니라 **그 이후 새 이벤트가 없다는 것**이다 —
`opened` 은 이미 발송됐고, `rejected_summary` 는 최다 게이트가 안정적이어서 억제됐다.

따라서 수리는 "거부 알림을 되살리는 것"이 아니라 **매일 반드시 도착하는 성과 리포트로
그 자리를 채우는 것**이다(C1 유지: 거부 상세는 여전히 텔레그램 금지).

### 계산 정의 (원장 단일 경로 — 별도 집계 테이블 없음)

| 지표 | 정의 | 원장 |
| --- | --- | --- |
| 승률 | 청산 완료 중 **R>0 비율** (미청산 제외) | 트랙별 원장 |
| 평균 R | 청산 완료의 R 평균 | 〃 |
| 실현 R | `실현 손익 / (|진입가 − 무효화가| × 수량)` | 〃 |
| 미실현 | 보유 포지션의 원가 가중 수익률 | 〃 |
| 폴리 브라이어 | 정산 완료 추정의 `(p − outcome)²` 평균 | `poly_positions` / `poly_resolutions` |

**실현 R 의 두 함정** (둘 다 실측에서 걸렸다):

1. **분모는 `stop_price` 가 아니라 `invalidation_price` 다.** stop 은 본전으로 끌어올려져
   진입가와 같아질 수 있다(`breakeven_stop`) — 그러면 분모가 0이 된다. 14건 중 6건이 그랬다.
2. **분자는 최종 청산가가 아니라 실현 손익이다.** 부분청산이 있으면 최종 청산가가 진입가보다
   불리해도 실현 손익은 양수다(HYPEUSDT 롱 진입 58.623 → 최종 청산 57.94, 실현 +1.19).
   종가 기준 R 은 이 거래를 손실로 오판한다.

`duplicate_bootstrap_suppressed` 처럼 진입이 성립하지 않은 원장 행은 분모에서 제외한다 —
넣으면 없는 패배를 만든다. 제외 건수는 payload 에 함께 싣는다.

### 표본 규칙 (C3 — 계산부에 내장)

`review/paper_performance.py` 가 규칙을 **계산 단계에서** 강제한다. 표시부가 실수로 숫자를
만들 수 없게 하는 것이 설계 의도다.

| 표본 | `state` | 계산부 | 표시 |
| --- | --- | --- | --- |
| N = 0 | `none` | 승률·평균 R = **`None`** | "표본 없음 — 청산 완료 0건" |
| 0 < N < 30 | `insufficient` | 값 산출 | 값 + "표본 부족 (N<30) — 판정 유보" |
| N ≥ 30 | `sufficient` | 값 산출 | 값 |

승률·평균 R 은 **항상 N과 함께** 표기한다. 트랙 성과를 단일 숫자로 합치지 않으며(C4),
실계좌 성과(`app/performance/metrics.py`)와도 섞지 않는다.

### 크립토에는 검증 시계가 없다

주식(`stock_paper_tracks`)·폴리(`poly_paper_track`)는 유실일 제외 경과일을 갖지만
**크립토는 그 원천이 없다.** 없는 값을 만들지 않고 `clock: None` 으로 두며, 리포트는
크립토의 "검증 D+N"을 생략한다. 크립토 검증 시계가 필요하면 별도 WO 대상이다.

## 고아 포지션 제외 표기 규격 (WO-FCE-STOCK-EXIT-01)

청산 경로가 없던 구간에 진입한 포지션은 엔진 성과가 아니다. 통계에서 제외하되 **침묵하지 않는다** — 제외 사실과 사유가 항상 보여야 한다.

- 저장: `stock_paper_positions.excluded_from_stats=1`, `exclusion_reason='void_no_exit_path'`
- 이벤트: 청산 시 `closed` 이벤트 `detail.excluded_from_stats=true`
- 표기: 성과 화면·리포트는 "승률 N% (제외 M건: 청산 경로 부재 구간)"처럼 **제외 건수를 함께** 표시한다. 제외를 숨기면 표본이 실제보다 많아 보인다.
- 도구: `scripts/void_orphan_stock_positions.py`(기본 dry-run)

폴리 정산도 같은 원칙을 따른다: 정산되지 않은 사유를 `settlement_skips`로 집계해 "만기 미도래"인지 "가격이 확정 극단값에 미달"인지 구분 가능하게 한다.

## 구조 컨텍스트 알림 규격 (WO-FCE-STRUCTURE-CONTEXT-01)

`position_structure_event` — **보유 포지션이 있을 때만** 발화하는 구조 관계 알림.

- **화이트리스트 등록**: `RULE_LABELS`·`RULE_SEVERITY`(info)·`alert_rules_enabled` 기본값에 명시 등록됐다. 거부 알림은 추가하지 않는다(진입 중심 알림 원칙 유지).
- **전이 시에만 발송**: 같은 구조 상태가 100틱 반복돼도 0건이다. 최초 관측은 전이가 아니므로 첫 틱에 알림이 쏟아지지 않는다. 상태 키는 `{종목}:{이벤트유형}:{레인지ID}`.
- **직전 상태 영속화**: `NotificationState.structure_contexts`에 심볼별로 저장되어 재기동에도 억제가 유지된다.
- **메시지 마지막 줄 고정**: `관측 정보이며 매매 신호가 아닙니다.` — 구조는 관측이지 예측이 아니다.
- **시장 이벤트와 분리**: 시장 전체 와이코프 이벤트는 기존 `wyckoff_event`가 담당하며 변경하지 않았다.

진입·결과 알림의 구조 1줄은 `structure.context.verdict_line`(관측 서술)을 그대로 싣는다. 인과 단정 문구는 회귀 테스트가 금지한다.

정본: [`docs/StructureContext.md`](StructureContext.md)

## 텔레그램 발송 통합 관문 (WO-FCE-ALERT-WHITELIST-02 → WO-FCE-WHALE-ALERT-DEMOTE-01)

> **관문은 하나다.** 두 개면 그중 하나는 반드시 잊힌다.

### 왜 통합했나 (2026-08-08)

화이트리스트가 **경로마다 하나씩** 있었다. `TELEGRAM_SENDABLE_KINDS` 는 페이퍼 트랙 이벤트
(`kind` 를 가진 dict)에만 적용됐고, `notify/alerts.py::_fire_if_allowed` 경로(`AlertCandidate`)는
그 화이트리스트를 **아예 호출하지 않았다.** 그래서 고래 다중체결 알림(`whale_entry`)이 원칙을
우회한 채 발송됐다. 한 건의 버그가 아니라 구조의 문제였다 — 새 알림이 다른 경로로 들어오면
원칙이 적용되지 않는다.

정본: `app/notify/delivery_gate.py`. 두 경로를 같은 모듈이 판정한다.

| 공간 | 식별자 | 판정 함수 | 예 |
|---|---|---|---|
| 페이퍼 트랙 이벤트 | `kind` | `evaluate_event()` | `opened` · `closed` · `rejected_summary` |
| 포지션·시스템 알림 | `rule_id` | `evaluate_rule()` | `invalidation_breach` · `whale_entry` |

**둘은 다른 이름 공간이다.** 페이퍼 이벤트 허용 목록을 `rule_id` 공간에 그대로 적용하면
무효화 이탈·청산 접근 같은 critical 포지션 경보가 전부 끊긴다.

### 기본 차단 (default-deny)

`PUSH_ALLOWED_RULES` 에 없는 `rule_id` 는 **차단**된다. "새 알림이 다른 경로로 들어오면 원칙이
적용되지 않는다"가 결함의 본질이었고, 기본 차단이 그 구멍을 막는다. 새 알림을 푸시하려면 명시적
등록이 필요하며 **그 등록 행위가 곧 검토 지점**이다.

### 강등 (`DEMOTED_RULES`)

| rule_id | 텔레그램 | 사유 |
|---|:---:|---|
| `whale_entry` | ❌ **미도달** | 미검증 신호 · 사후 채점 표본 부족(N=5). 수집·저장·조회는 그대로 |

강등된 신호는 **사유와 함께** `NotificationState.blocked_alerts` 에 남고, 원장에도
`delivered=False` 로 기록된다. 발송하지 않은 것과 발생하지 않은 것은 다르다. 24h 집계는 일 1회
요약에 1줄로만 나간다(개별 건·지갑명 없음, N<30 이면 "표본 부족" 병기).
승격 조건: [`docs/validation/WHALE_ALERT_PROMOTION.md`](validation/WHALE_ALERT_PROMOTION.md) —
자동 승격은 없다.

### 페이퍼 이벤트 kind

> **거부는 알림이 아니라 조회 대상이다.** 여기 없는 kind 는 텔레그램에 도달하지 않는다.

| kind | 텔레그램 | 근거 |
|---|:---:|---|
| `opened` | ✅ 발송 | 무엇이 **일어났는가** |
| `closed` | ✅ 발송 | 결과 + 누적 승률 |
| `rejected_summary` | ❌ **미도달** | 무엇이 **안 일어났는가** — 조회 대상 |
| `skipped` | ❌ **미도달** | 동일 |
| `error` | ❌ **미도달** | 엔진 오류는 생존 감시 계열(`engine_liveness` 등)이 담당 |

정본: `app/notify/paper_events.py::TELEGRAM_SENDABLE_KINDS` / `is_telegram_sendable()` —
관문 진입점은 `delivery_gate.evaluate_event()` 다.

### 왜 억제가 아니라 화이트리스트인가

선행 WO들은 발송 **빈도**를 계속 좁혀 왔다: "개별 → 집계 1건" → "전이 시에만". 그러나 유니버스가 오염되면 최다 거부 게이트가 계속 뒤바뀌어(`unsupported_crypto_question` → `마켓 한도` → `resolution_time_invalid`) **전이 자체가 반복 발생**해 사실상 스팸이 됐다.

**빈도를 조이는 접근은 오염된 입력 앞에서 반복 실패한다.** 그래서 발송 경로에서 원천 제외한다. 거부가 100회 발생해도 텔레그램은 0건이며, 회귀 테스트가 이를 강제한다(`tests/test_alert_whitelist.py`).

### 거부는 어디서 보는가

- `GET /api/system/paper/diagnosis` — `telegram_sendable_kinds`·`rejection_policy`와 트랙별 최다 거부 게이트, 그리고 `whale_observations`(미발송 고래 이벤트 + 미발송 사유)·`gate`(관문 레지스트리 스냅샷)
- 일 1회 성과 요약 — 트랙별 평가·진입·거부 집계

**침묵 금지(C4)와 거부 미발송(C1)은 양립한다**: 조회는 되고 알림만 안 간다. 생존·사망·복구 알림은 이 경로가 아니라 `notify/rules.py` 계열이므로 영향받지 않는다.

---

## 상태 알림 원칙 (WO-FCE-BREACH-ALERT-FIX-01, 2026-08-04)

> **상태 알림은 상태 진입 시 1회 + 상태 변화 시에만 발송한다.
> 쿨다운 만료는 재발송 사유가 아니다.**

무효화 이탈·상태 악화는 **상태**지 반복 이벤트가 아니다. "이탈했다"는 최초 1회 알리면
되고, 그 뒤로는 상태가 변할 때만 알린다.

| 발송 | 조건 |
| --- | --- |
| 최초 이탈 | 반드시 발송 (C1 — 포지션 위험 신호를 없애지 않는다) |
| 추가 발송 | 이탈 폭이 `alert_breach_reescalate_pct`(기본 1.0%p) 이상 **추가 악화** |
| 복귀 | 무효화선 안으로 복귀 시 재무장 + 깊이 기억 초기화 |
| 청산 | `position_closed` 가 담당 |
| 지속 | 일일 요약에 건수만. 개별 알림 금지 |

### 반복의 실제 원인은 쿨다운이 아니었다 — 상태 축출이었다

2026-08-04 실측: `DRAMUSDT 무효화 이탈`이 01:46/02:22/03:22/04:22 로 4회 발송됐고
내용이 완전히 동일했다(50.1710 이탈·현재 49.9100·-0.52%). 새 정보가 0인 반복이다.

추정 원인은 "쿨다운 30분 만료 시 재발송"이었지만 코드는 그렇지 않았다 —
`invalidation_breach` 는 `setup_*` 가 아니므로 쿨다운 만료로 재무장되지 않고,
`_generic_rearm_allowed` 도 False 를 준다. 진짜 원인은 **영속 상한**이었다:

```python
for key, rule in list(self.alert_rule_states.items())[-500:]   # 삽입 순서 절단
```

저장 키가 정확히 **500 포화**였고 그중 `whale_entry` 가 **288개(58%)** 를 점유했다.
삽입 순서로 자르므로 오래 전에 생성된 **활성 이탈 쿨다운이 축출**되고, 재로드 시
`setdefault` 가 `status="armed"` 로 되살려 같은 알림이 다시 나갔다.

수리: 절단 기준을 **① 쿨다운이 아직 유효한 키 ② 최근 발사 순**으로 바꿨다(상한 2000).
살아있는 쿨다운은 절대 버리지 않는다. 여기에 이탈 깊이 기억(`last_breach_pct`)을 더해
상태가 부활해도 한 번 더 막는 이중 방어를 뒀다.

**교훈**: 상태를 영속할 때 "무엇을 버릴지"는 삽입 순서가 아니라 **아직 필요한지**로
정해야 한다. 삽입 순서 절단은 조용히 알림 스팸을 만든다.
