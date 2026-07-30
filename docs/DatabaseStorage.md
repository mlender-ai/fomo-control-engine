# DB 용량 — 실측·리텐션 커버리지 (정본)

`backend/fomo_control_engine.db` 비대 사건이 세 번 반복됐다(12.8GB → 5.4GB → 9.4GB).
매번 "리텐션이 고장났다"고 추정했지만 **2026-07-30 실측 결과 리텐션과 회수는 정상이었다.**
추정 대신 이 문서의 실측 절차를 먼저 돌린다.

## 1. 실측 절차 (추정 금지)

운영 중 엔진(8875)이 상시 가동하므로 항상 **읽기 전용**으로 붙는다.

```bash
sqlite3 "file:fomo_control_engine.db?mode=ro" "SELECT name, SUM(pgsize)/1048576 AS mb, SUM(pgsize)*100.0/(SELECT SUM(pgsize) FROM dbstat) AS pct FROM dbstat GROUP BY name ORDER BY mb DESC LIMIT 15;"
```

`dbstat` 은 전 페이지를 읽으므로 9GB 기준 약 1분 40초가 걸린다. 인덱스도 별도 행으로
나오니 테이블 본체와 합산해야 실제 비중이 보인다.

회수 상태는 세 값을 함께 본다:

```bash
sqlite3 "file:fomo_control_engine.db?mode=ro" "PRAGMA page_count; PRAGMA freelist_count; PRAGMA auto_vacuum;"
```

- `auto_vacuum` = **2(INCREMENTAL)** 이어야 `incremental_vacuum` 이 파일을 실제로 줄인다. `0(NONE)` 이면 DELETE 를 해도 파일은 그대로다.
- `freelist_count` ≈ **0** 이면 회수가 이미 끝난 상태다. 즉 **파일 크기 = 살아있는 데이터**이고, 이때 문제는 회수가 아니라 리텐션 범위나 유입량이다.

리텐션이 실제로 돌았는지는 하트비트가 아니라 이벤트 로그가 정본이다(하트비트는 엔진
재시작 때 `runs=0` 으로 리셋돼 "안 돈다"는 오해를 준다):

```bash
sqlite3 "file:fomo_control_engine.db?mode=ro" "SELECT created_at, status FROM database_maintenance_events WHERE event_type='retention' ORDER BY created_at DESC LIMIT 5;"
sqlite3 "file:fomo_control_engine.db?mode=ro" "SELECT payload FROM database_maintenance_events WHERE event_type='retention' ORDER BY created_at DESC LIMIT 1;" | python3 -m json.tool
```

`payload.details` 에 테이블별 삭제 건수가 다 남는다 — "잡은 도는데 아무것도 안 지운다"
패턴은 여기서 0 으로 드러난다.

## 2. 2026-07-30 실측 결과 (9.35GB)

| 테이블 | MB | 비중 | 리텐션 |
|---|---|---|---|
| `bitget_trade_fills` (본체) | 2227 | 24.7% | 2일 ✅ |
| `reports` | 2368 | 26.2% | **없었음** → 30일 추가 |
| `position_snapshots` | 1741 | 19.3% | 닫힌 것만 ⚠️ → 열린 것도 추가 |
| `idx_bitget_trade_fills_symbol_timestamp` | 681 | 7.6% | (본체 종속) |
| `sqlite_autoindex_bitget_trade_fills_1` | 350 | 3.9% | (PK 종속) |
| `judgment_ledger` / `judgment_scores` | 218 / 204 | 4.7% | 영구 보존(정책) |
| `deriv_metrics` | 203 | 2.3% | 90일 + 다운샘플 ✅ |
| `toss_quotes` / `toss_candles` / `toss_rankings_snapshot` | 140 / 68 / 49 | **2.9%** | 없음(무해) |

`page_count` 2,312,840 × 4096 = 9.47GB, `freelist_count` **0**, `auto_vacuum` **2**.
→ 회수는 100% 정상. 파일 전체가 살아있는 데이터였다.

**유력 후보로 지목됐던 `toss_*` raw append 테이블은 합쳐서 257MB(2.9%)로 원인이 아니었다.**
리텐션이 없는 것은 사실이지만 용량 기여가 없어 손대지 않는다.

### 진짜 원인 세 가지

1. **`bitget_trade_fills` — 유입량이 2배로 늘었다.** 07-28 3.07M행, 07-29 4.17M행/일.
   `db_trade_fill_retention_days=2` 는 "하루 ~2M행 → 2일이면 ~1GB" 가정으로 정해졌는데,
   실제 유입이 그 2배가 되어 2일 정상상태가 3.26GB(본체+인덱스)가 됐다.
   **리텐션은 정상 동작 중이다**(07-29 실행에서 1,879,250행 삭제). 임계값 재검토 대상.
2. **`reports` — 2.37GB 화석.** 186,068행이 전부 07-03~07-10 구간(당시 39k행/일, 현재 ~30행/일).
   리텐션이 아예 없어 07-27 일회성 정리에서도 살아남았다.
3. **`position_snapshots` — 열린 포지션은 영구 누적.** 다운샘플이 `status != 'open'` 만
   대상이라 열린 4개 포지션의 78,954행/905MB 는 나이와 무관하게 계속 쌓였다.

즉 5.4GB → 9.4GB 반등은 **리텐션 무력화가 아니라** (1) 유입 2배 + (2)(3) 리텐션 사각지대다.

## 3. 리텐션 커버리지 맵

`backend/app/db/maintenance.py::_apply_sqlite_retention` 기준. 잡은 매일 04:00 KST
(`database_retention`, `manager.py`)에 돌고 끝에 `wal_checkpoint(TRUNCATE)` +
`incremental_vacuum` 으로 회수한다.

| 대상 | 정책 | 설정 |
|---|---|---|
| `derivative_snapshots` | 삭제 | `db_retention_days` (30) |
| `reports` | 삭제, **심볼별 최신 1건·`research_runs` 참조분 보존** | `db_retention_days` (30) |
| `position_snapshots` (닫힘) | `closed_at` 지난 뒤 버킷당 1건 | `db_closed_snapshot_retention_days` (30) / `db_snapshot_downsample_minutes` (60) |
| `position_snapshots` (열림) | cutoff **이전만** 버킷당 1건, 최근은 그대로 | 위와 동일 |
| `deriv_metrics` | 버킷당 1건 | `db_deriv_metrics_raw_days` (90) / `..._downsample_minutes` (1440) |
| `bitget_trade_fills` | 삭제 | `db_trade_fill_retention_days` (2) |
| `alerts` | 삭제, `judgment_ledger` 참조분 보존 | `db_alert_retention_days` (90) |
| `worker_heartbeat` | 삭제 | `db_worker_heartbeat_retention_days` (14) |
| `liquidation_events` | 삭제 | `db_liquidation_event_retention_days` |
| `PERMANENT_TABLES` | **영구 보존** — 삭제 시 예외로 롤백 | — |

`reports` 삭제가 안전한 근거(실측): 읽기 경로는 `latest_report(symbol)` ·
`recent_reports(limit)` · `get_report(id)` 뿐이고, 참조는 `research_runs.report_id`
1건 + `positions.entry_report_id` 0건이었다. 두 보존 규칙으로 조회 동작이 유지된다.

## 4. 함정

- **id 목록 `IN (...)` 은 청크로 나눈다.** `SQLITE_MAX_VARIABLE_NUMBER`(구버전 999)를
  넘으면 예외가 나고 `with connection` 이 리텐션 트랜잭션 **전체를 롤백**한다 —
  18만 행 규모인 `reports` 는 청크 없이는 한 행도 못 지운다. `_delete_by_ids` 사용.
- **전체 `VACUUM` 금지.** 엔진이 상시 쓰는 DB라 장시간 쓰기 잠금을 유발한다.
  `incremental_vacuum` 으로만 회수한다(`auto_vacuum=INCREMENTAL` 전제).
- **30일 창은 즉시 줄지 않는다.** 2026-07-30 기준 DB 이력이 07-03 시작(27일)이라
  `reports`·열린 스냅샷 모두 cutoff 이전 행이 0건 — 이번 수정의 즉시 회수량은 **0바이트**다.
  화석 2.37GB 는 08-02 부터 자연 소멸을 시작해 08-09 경 정리된다. 즉시 축소가 필요하면
  임계값 조정(사용자 판단)이 필요하다.
- **하트비트로 잡 생존을 판단하지 마라.** 재시작 시 리셋된다. `database_maintenance_events` 가 정본.
