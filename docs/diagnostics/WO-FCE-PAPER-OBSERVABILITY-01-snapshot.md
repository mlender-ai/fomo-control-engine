# 작업 1 — 실측 진단 스냅샷

WO-FCE-PAPER-OBSERVABILITY-01. 실사 기준 main @ a5023b6 (2026-07-23).

> **정직성 고지 (C4).** 이 PR은 프로덕션 DB·워커 하트비트·`.env` 실효값·`notify` 상태가 없는
> 신규 클론에서 작성했다. 따라서 작업 1의 5종 중 **런타임 상태에 의존하는 항목(2·3·4·5)은
> 프로덕션 서버에서 채워야 한다.** 아래는 코드로 확정 가능한 진단과, 런타임에서 확인할
> 정확한 쿼리·명령을 함께 제시한다. 유실 구간을 정상 경과로 계산하지 않는다.

## 코드로 확정된 진단 (D1~D3)

| 결함 | 코드 근거 | 확정 |
|---|---|---|
| **D1** 주식·폴리 텔레그램 경로 부재 | `worker/manager.py`의 `_send_paper_events` 호출은 `_sync_positions`(크립토) 1곳뿐이었음. `_collect_toss_stocks`·`_collect_polymarket` 미호출. `stock_paper/service.py`·`poly_paper/service.py` 반환 payload에 `events` 키 자체가 없었음 | ✅ 확정 — 계약 부재 |
| **D2** 스플릿 플래그 함정 | `core/config.py`: `toss_stock_scout_enabled` 기본 **False**, `stock_paper_engine_enabled` 기본 **True**. `_ready_to_start`가 `toss_stock_scout_enabled`를 요구 → 구동 잡이 꺼지면 엔진은 조기 반환 | ✅ 확정 |
| **D3** 조용한 스킵 | `stock_paper/service.py`의 비활성/미구성 조기 반환은 예외가 아님 → 하트비트 success로 기록. `last_effective_run_at` 같은 구분 필드가 없었음 | ✅ 확정 |

D1이 문제의 핵심이다: **호출 누락이 아니라 계약(contract) 부재** — 배선을 해도 `events`가 없어 전송량 0이었다.

## 런타임에서 채울 5종 (프로덕션 실행)

1. **`.env` 실효값** (시크릿 제외, 값만):
   ```bash
   env | grep -E 'FCE_(TOSS_STOCK_SCOUT|STOCK_PAPER_ENGINE|POLYMARKET_PAPER|TELEGRAM_ALERTS|PAPER_TELEGRAM_ALERTS)_ENABLED'
   ```
   코드 기본값: scout=False, stock_engine=True, poly=True, telegram_alerts=True, paper_telegram_alerts=True.

2. **워커 하트비트 전량** — `toss_stock_scout`·`polymarket_paper`의 `last_success_at` / `last_effective_run_at`(신규) / `consecutive_failures` / `status`:
   ```sql
   SELECT job_name, status, last_success_at, last_effective_run_at, consecutive_failures
   FROM worker_heartbeat ORDER BY job_name;
   ```
   또는 `GET /api/system/worker`. **`last_effective_run_at`이 NULL이면 엔진이 한 번도 평가하지 않은 것.**

3. **뮤트 상태** — `GET /api/system/paper/diagnosis` → `tracks.*.mute_state`, 또는 `notification_state` 파일의 `muted_until`.

4. **주식/폴리 원장 행수 + 최근 시각**:
   ```sql
   SELECT COUNT(*), MAX(ts) FROM stock_paper_entry_rejections;
   SELECT COUNT(*), MAX(filled_at) FROM stock_paper_fills;
   SELECT COUNT(*), MAX(observed_at) FROM poly_estimates;
   ```

5. **최근 7일 keepalive 재시작 횟수** — 로컬 keepalive 감시자(`9bd05bc`) 로그 기반. 재시작 가시화·상태 API 노출은 작업 6 후속.

## 이 PR이 수리한 범위

- **D1·D2·D3 전부**: 3트랙 이벤트 계약 통일 + stock/poly 텔레그램 배선, 스플릿 플래그 기동 경고, `last_effective_run_at` 도입 + skipped 관측화·억제, `/api/system/paper/diagnosis` 진단 표면.
- **후속으로 남김**: 작업 5(트랙별 생존 라인·뮤트 관통·stale 감지 포함), 작업 6(DB 성장 감시·keepalive 가시화·검증 시계 유실일 보정). 근거와 다음 액션은 PR HANDOFF에 명시.

## `/api/system/paper/diagnosis` 응답 형태

```json
{
  "principle": "침묵 금지 — 모든 미발생은 사유와 함께 관측 가능해야 한다.",
  "flag_warnings": [{"track": "stock", "message": "...구동 잡(toss_stock_scout)이 꺼져..."}],
  "tracks": {
    "crypto": {"enabled_flags": {"engine": true, "driver_job": true}, "last_effective_run_at": null, "telegram_wired": true, "mute_state": {"is_muted": false, "muted_until": null}, "ready_to_start": true, "ready_to_start_reason": null},
    "stock":  {"enabled_flags": {"engine": true, "driver_job": false}, "last_effective_run_at": null, "top_reject_gate": null, "telegram_wired": true, "ready_to_start": false, "ready_to_start_reason": "toss_observation_not_configured ..."},
    "poly":   {"enabled_flags": {"engine": true, "driver_job": true}, "last_effective_run_at": null, "telegram_wired": true, "ready_to_start": true, "ready_to_start_reason": null}
  }
}
```
