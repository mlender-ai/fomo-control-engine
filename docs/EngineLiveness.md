# 엔진 생존 감시 (Engine Liveness)

> WO-FCE-ENGINE-LIVENESS-01 정본. 3트랙 전면 정지가 **아무 소리 없이** 4일간 지속된 사고의 구조적 수리.

## 0. 제1원칙 — 감시자는 감시 대상 안에 살 수 없다

> **watchdog inside the watched process는 감시가 아니다.**
>
> 엔진이 멈췄음을 알려줄 모든 수단(펄스·일일요약·data_stall)이 **멈춘 그 워커 안에서** 실행되면,
> 워커가 죽는 순간 침묵을 알릴 주체도 함께 죽는다. **침묵이 스스로를 은폐한다.**

따라서 생존 감시는 반드시 **2계층**이다. 내부 감시만 추가하는 변경은 이 문서 위반이다.

## 1. 감시 토폴로지

```
┌─ 워커 프로세스(8875) ─────────────────────┐
│  worker_liveness 잡 (5분 주기)            │      ┌─ 외부(세션 상주) ───────────┐
│   ├ 트랙 stale·백오프·인프라 평가 → 알림   │      │ supervisor.sh (15초 루프)    │
│   └ logs/liveness.json 하트비트 기록 ─────┼─────▶│  └ deadman.sh (60초마다)     │
│                                            │ 읽기 │      하트비트 나이만 판독     │
│  ⚠ 프로세스가 죽으면 이 감시도 함께 죽는다 │      │      → 텔레그램 API 직접 호출 │
└────────────────────────────────────────────┘      └──────────────────────────────┘
```

| 계층 | 무엇을 잡나 | 한계 |
| --- | --- | --- |
| **내부** `worker_liveness` 잡 | "돌지만 아무것도 안 하는" 잡, 백오프 고착, DB/디스크 임계, 재시작 이력 | 프로세스가 죽으면 함께 죽음 |
| **외부** `scripts/local/deadman.sh` | **프로세스·스케줄러 사망** (하트비트 갱신 정지) | 감시자 자신이 죽으면 무음 → 주 1회 자가 점검 메시지로 완화 |

**외부 감시자는 워커·앱 코드를 경유하지 않는다.** 텔레그램 토큰을 `.env`에서 독립적으로 읽고
`api.telegram.org`를 직접 호출한다 — 죽은 경로 재사용 금지.

**왜 별도 데몬이 아니라 supervisor 확장인가**: 감시자가 늘면 "감시자의 감시자" 문제가 늘어난다.
이미 상주하며 프로세스를 되살리는 루프 하나에 통합해 실패 지점을 최소화했다(감시자는 감시 대상보다 단순해야 한다).

## 2. effective run — "성공"과 "실제로 일함"의 구분

이 구분이 이 문서의 존재 이유다.

- `last_success_at`: 잡이 예외 없이 끝난 시각. **조기 반환·무수확도 "성공"이다.**
- `last_effective_run_at`: 엔진이 **실제로 평가를 수행한** 시각.

2026-07-23~27 사고: `toss_stock_scout`가 **35,014회 "성공"** 하는 동안 실제 평가는 **0회**였다.
성공 기준으로 감시했다면 영원히 못 잡는다. **stale 판정은 반드시 `last_effective_run_at` 기준.**

## 3. 임계값

| 대상 | 기본값 | 설정 키 |
| --- | --- | --- |
| 내부 감시 주기 | 300초 | `FCE_WORKER_LIVENESS_INTERVAL_SECONDS` |
| 트랙 stale 배수 | 잡 주기 × **3** | `FCE_WORKER_LIVENESS_STALE_MULTIPLIER` |
| 외부 하트비트 사망 판정 | 900초(내부 주기 × 3) | `FCE_DEADMAN_STALE_SECONDS` |
| 사망 리마인더 | 3600초 | `FCE_DEADMAN_REMIND_SECONDS` |
| 감시자 자가 점검 | 7일 | `FCE_DEADMAN_SELFCHECK_SECONDS` |
| DB 용량 경고 | 10GB | `FCE_DB_SIZE_ALERT_GB` |
| 디스크 여유 경고 | 20GB | `FCE_DISK_FREE_ALERT_GB` |

감시 대상 트랙: `paper_engine`(크립토) · `polymarket_paper`(폴리) · `sync_positions` + 시장 단위 가상 트랙 `stock_kr`·`stock_us`(아래 "시장별 독립 생존 판정" 참조).

## 4. 뮤트 정책 (C2)

> **뮤트는 조건 알림을 끄는 장치지, 심장박동을 끄는 장치가 아니다.**

| 알림 계열 | 뮤트 |
| --- | --- |
| 조건 알림(트리거·무효화·건강도 등) | 억제됨 |
| **생존/사망 신호**(`engine_liveness`·`job_backoff_stuck`·`infra_capacity`·`process_restarted`) | **관통** |
| 일일 요약의 **트랙 생존 라인** | **관통** (뮤트 중엔 생존 라인만 발송) |
| 외부 데드맨 스위치 | 애초에 앱 뮤트 상태를 모른다(구조적 관통) |

쿨다운·중복 억제는 그대로 적용되어 스팸을 막는다. 뮤트가 사망 경보까지 끄면
"조용함"이 정상인지 고장인지 영원히 구분할 수 없다.

## 5. 사망 → 복구 시나리오

1. 워커 사망 → 하트비트 갱신 정지
2. 900초 경과 → 외부 감시자가 🚨 **사망 알림** 1회 (마지막 정상 시각·경과·프로세스 생존 여부·재시작 횟수 포함)
3. 지속 시 1시간 간격 리마인더 (그 사이 침묵 = 스팸 방지)
4. keepalive가 프로세스 부활 → 하트비트 재개 → ✅ **복구 알림** 1회
5. 재시작 사실은 `logs/restarts.jsonl`에 기록 → 내부 감시가 24시간 내 재시작을 알림·진단에 노출(조용한 자동 복구 금지)

## 6. 검증 시계 보정 (C4)

`liveness.elapsed_excluding_gaps()` — 경과일을 **달력일이 아니라 effective run이 있던 날**로 센다.
유실일은 사유와 함께 기록하고 `"경과 N일 (유실 M일 제외)"`로 표기한다.
숫자가 나빠져도 정직한 재계산이 우선이다.

## 7. 관측 표면

- `GET /api/system/paper/diagnosis` → `liveness` 블록: 토폴로지·트랙별 상태·stale 목록·백오프 고착·24시간 재시작 이력
- `GET /api/system/worker` → 잡별 `last_effective_run_at`·`current/base_interval_seconds`
- `logs/liveness.json` (하트비트) · `logs/deadman.log` (외부 감시자 발송 이력) · `logs/restarts.jsonl`

## 8. 운영

```bash
scripts/local/start-supervisor.sh      # keepalive + 데드맨 시작
tail -f logs/deadman.log               # 외부 감시자 판정·발송 이력
cat logs/liveness.json                 # 현재 하트비트
FCE_DEADMAN_STALE_SECONDS=30 bash scripts/local/deadman.sh   # 강제 점검(테스트)
```

**kill 테스트(회귀 시 반드시 재현):** supervisor 정지 → `lsof -ti :8875 | xargs kill -9` → 임계 초과 대기 →
`deadman.sh` 실행 → 텔레그램 사망 알림 도착. 2026-07-27 실측: HTTP 200 도착 확인(사망→복구→스팸억제 3종).

## 9. 금지

- 프로세스 **내부** 감시만 추가하고 종료 (동일 사고 재발)
- 뮤트가 생존/사망 신호를 억제하도록 되돌리기
- 사망 알림을 워커·앱 코드 경유로 발송 (죽은 경로 재사용)
- `last_success_at` 기준 stale 판정 (조기 반환을 정상으로 오인)
- 유실일을 정상 경과로 계산


## 시장별 독립 생존 판정 (WO-FCE-PAPER-ENTRY-REALITY-01, 2026-07-28)

`toss_stock_scout` **하나의 잡이 KR·US 를 함께 수집**한다(`_collect_toss_stocks` 가 두 시장을
`asyncio.gather`). 그런데 liveness 가 이 잡을 `market="KR"` 로만 판정해서, 미국 정규장
(KST 22:30~05:00)에는 항상 `market_closed` 로 분류됐다 →
**미장이 완전히 죽어도 경보가 구조적으로 불가능했다.**

해법: 잡 하트비트가 아니라 **시장별 실제 평가 흔적**으로 판정한다.

| 가상 트랙 | 시장 | 판정 근거 | 세션 |
| --- | --- | --- | --- |
| `stock_kr` | KR | `stock_paper_analysis_snapshots` 의 KR 최신 `observed_at` | 09:00~15:30 KST |
| `stock_us` | US | 같은 테이블의 US 최신 `observed_at` | 09:30~16:00 ET (정규장만) |

- `MARKET_DATA_TRACKS` 에 정의, 기대 주기 900초 × stale 배수(기본 3) = 2,700초 임계.
- 각 시장은 **자기 정규장에만** 평가된다. 상대 시장이 열려 있어도 자기 장이 닫혔으면 `market_closed`.
- 장중인데 관측 기록이 아예 없으면 그 자체를 정지로 본다(미장 침묵이 여기서 잡힌다).
- 프리·애프터 마켓은 범위 외.


## 심장박동의 제1 규칙 (WO-FCE-ENGINE-RESTORE-01, 2026-07-28)

> **심장박동은 가장 단순한 경로여야 한다.**
> **감지 신호와 조치 신호는 일치해야 한다.**

### 사고: 조용한 직렬화 실패로 11.7시간 정지

`build_liveness_snapshot` 에 `status()` 유래 `datetime`(muted_until)이 섞여 들어가
`json.dumps` 가 실패했다. 그런데 그 실패는 `try/except → logger.warning` 으로 삼켜졌고,
잡은 6밀리초 만에 **"ok"** 로 완주했다. 결과:

- 하트비트 파일 11.7시간 정지 (`Object of type datetime is not JSON serializable`)
- 잡 28종 전부 정상, DB·상태파일은 초 단위 갱신 → **엔진은 멀쩡했다**
- 외부 감시자는 하트비트만 보므로 **프로세스 사망으로 오판**하고 사망 알림 발송
- supervisor 는 **포트만** 봤으므로 개입하지 않음 → 11.7시간 방치

### 수리 3종

| 규칙 | 구현 |
| --- | --- |
| 심장박동 전용 잡 | `heartbeat` 잡(60초) — 파일에 타임스탬프만 쓰고 즉시 반환. 네트워크·앱 서비스 호출 0 |
| 실패는 크게 | 하트비트 쓰기 실패는 **ERROR + stack** (WARNING 으로 삼키지 않는다) |
| 직렬화 방어 | `json.dumps(..., default=str)` — 표현 불가 타입은 문자열로 낮춰서라도 기록 |

상세 진단 스냅샷은 `liveness-detail.json` 으로 **분리**했다. 진단이 실패해도 심장박동은 뛴다.

### 매달림(hang) vs 사망(death)

| 상태 | 포트 | 하트비트 | 판정 주체 | 조치 |
| --- | --- | --- | --- | --- |
| 정상 | 열림 | 갱신 | — | — |
| **매달림** | **열림** | **정지** | supervisor(`check_heartbeat_hang`) | 900초 초과 시 강제 재시작 |
| 사망 | 닫힘 | 정지 | supervisor(`listening`) + deadman | 즉시 재기동 |

**C4(감지-조치 일치)**: 과거엔 감지=하트비트, 조치=포트로 신호가 어긋나 매달림이 방치됐다.
이제 supervisor 가 **하트비트 나이로도** 재시작을 판단한다.

### 자동 복구 정책 (C5 · 폭주 금지)

| 항목 | 기본값 | 설정 키 |
| --- | --- | --- |
| 매달림 판정 임계 | 900초 | `FCE_SUPERVISOR_HB_STALE_SECONDS` |
| 재시작 쿨다운 | 600초 | `FCE_SUPERVISOR_HB_COOLDOWN_SECONDS` |
| 시간당 상한 | 3회 | `FCE_SUPERVISOR_HB_MAX_RESTARTS_PER_HOUR` |
| 잡 실행 예산 | 주기 × 5 (120~1800초) | `FCE_WORKER_JOB_TIMEOUT_*` |

상한 초과 시 자동 복구를 **중단**하고 "🛑 자동 복구 포기 · 수동 개입 필요"를 발송한다 —
재시작으로 안 고쳐지는 문제를 무한 재시작으로 덮지 않는다.

### 어댑터 타임아웃 점검표 (C3 · 누락 0)

| 어댑터 | 타임아웃 | 위치 |
| --- | --- | --- |
| Bitget | `self.timeout` (설정) | `exchange/bitget/client.py:85` |
| Toss | 10.0초 (`toss_timeout_seconds`) | `toss/client.py:88` |
| Polymarket | 10.0초 (`polymarket_timeout_seconds`) | `poly_paper/client.py:106` |
| Telegram | 10초 | `notify/telegram.py:48` |
| Hyperliquid | 10.0초 (`hyperliquid_request_timeout_seconds`) | `onchain/hyperliquid/client.py:36` |
| Coinglass | 10.0초 | `marketdata/coinglass.py:198` |

두 겹 방어: 어댑터 HTTP 타임아웃 + 잡 단위 `asyncio.wait_for`.
잡 타임아웃만으로는 `asyncio.to_thread` 내부 스레드가 계속 돌아 누수가 남는다.

### 검증 절차 (회귀 시 재현)

```bash
# 매달림 재현: 포트는 열어두고 하트비트만 낡게 만든다
python3 -c "import json,datetime;p='logs/liveness.json';d=json.load(open(p));\
d['written_at']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=20)).isoformat();\
json.dump(d,open(p,'w'))"
# → 15초 내 supervisor 가 재시작해야 한다
grep "heartbeat stale" logs/supervisor.log
```

2026-07-28 실측: 위조 1210초 → **10초 내 PID 교체 확인**(18264 → 18459),
`restarts.jsonl` 에 `reason="heartbeat_stale"` 기록.

## 자기잠금 금지 · 산출물 기준 감시 (WO-FCE-TOSS-US-STALL-01, 2026-07-29)

> **차단 상태는 자동 재시도 경로를 반드시 가져야 한다(자기잠금 금지).**
> **잡 단위 감시로는 잡 내부 분기의 죽음을 볼 수 없다 — 산출물 기준 감시가 필요하다.**

### 사고: 잡은 도는데 수집만 20.8시간 정지

`_authentication_blocked` 가 자기잠금 래치였다. 401 → 차단 등록 → 진입 즉시 반환(호출 안 함)
→ 성공할 기회가 없음 → 차단 영구. 해제 경로가 "수집 성공"뿐인데 차단 때문에 시도 자체가 없었다.

| 층위 | 상태 | 왜 못 봤나 |
| --- | --- | --- |
| 프로세스 | 22.7시간 무중단 | 죽지 않았다 |
| 하트비트 | 60초마다 정상 | 심장은 뛰었다 |
| `toss_stock_scout` | runs 8,076 · 오류 0 | 조기 반환은 "성공"이다 |
| `last_effective_run_at` | 계속 갱신 | 잡이 반환값을 주면 effective 로 친다 |
| 실제 수집 | **0건 (20.8시간)** | 아무 지표에도 안 나타난다 |

정지 알림은 `stock_us` 하나만 떴고, **이유를 몰라 사람이 코드를 뒤져야 했다.**

### 고정한 규칙

1. **자기잠금 금지(C2)** — 모든 차단은 `blocked_until` 백오프 후 실호출로 재시도한다.
   정본: [`docs/TossAuthRunbook.md`](TossAuthRunbook.md) "자동 재시도·백오프".
2. **조용한 조기 반환 금지(C3)** — 산출물 없는 반환 6개 지점(`authentication_failed`·
   `maintenance`·세션 비개장·`empty_universe`·`edge_blocked`)은 전부
   `{market, status, reason, blocked_until, observed_at}` 로 기록되고 진단 API 에 노출된다.
3. **사유 있는 정지 알림(작업 3)** — 트랙 stale 알림·일일 요약 생존 라인에 사유가 함께 실린다.
   `market_reasons` 로 `track_liveness`/`evaluate_liveness`/`daily_liveness_lines` 에 주입한다.
4. **로그가 사유를 버리지 않는다(D4)** — `_compact_result` 화이트리스트에 `KR`·`US` 를 넣었다.
   이게 빠져 있어서 "20.8시간째 authentication_failed" 가 로그에 단 한 줄도 없었다.

### 감시 증거의 층위 — 무엇을 보고 살아있다고 말하는가

| 증거 | 잡는 것 | 놓치는 것 |
| --- | --- | --- |
| 프로세스 포트 | 사망 | 매달림·내부 분기 정지 |
| 하트비트 파일 | 매달림 | 잡 내부 분기 정지 |
| 잡 `last_success_at` | 잡 예외 | 조기 반환("성공"으로 기록) |
| 잡 `last_effective_run_at` | 무수확 사이클 | 잡이 값을 반환하면 통과 |
| **산출물(DB 기록)** | **분기 단위 정지** | 게이트 하류면 후보 부재와 구분 불가 |

`stock_kr`·`stock_us` 트랙은 `stock_paper_analysis_snapshots` 를 근거로 삼는데,
이 테이블은 **진입 게이트 3개를 통과한 뒤에만** 기록된다. 따라서 "스냅샷 없음"은
"수집 정지"와 "후보 부재"를 구분하지 못한다 — 그래서 사유(`market_reasons`)를 함께 실어야 한다.
원시 수집 여부는 `toss_quotes`(시장별 `observed_at`)가 정본이다.

### 검증 시계 유실일 (C5 · 3트랙 완결)

`elapsed_excluding_gaps` 는 정의만 있고 **어디서도 호출되지 않는 죽은 코드**였다. 이제 배선됐다.

| 트랙 | 실측 근거 | 표기 |
| --- | --- | --- |
| 주식 KR·US | `stock_paper_analysis_snapshots` 의 날짜별 존재 | `경과 N일 (유실 M일 제외)` |
| 폴리 | `poly_markets.observed_at` 의 날짜별 존재 | 동일 |

API 는 `elapsed_days`(유실 제외) · `calendar_days` · `lost_days` · `elapsed_label` 을 함께 낸다.
대시보드 3곳(주식 트랙 카드·폴리 뷰·리뷰 개요)이 유실일을 표시한다.
숫자가 나빠져도 정직한 재계산이 우선이다.

### effective run 은 하드코딩될 수 없다

`run_stock_paper_engine` 은 `"effective_run": True` 를 **하드코딩**하고 있었다. 그래서 양 시장이
`closed` 이고 평가가 0건이어도 `toss_stock_scout.last_effective_run_at` 이 10초마다 갱신됐다.
20.8시간 수집 정지 동안에도 이 지표는 "실제 평가 중"이라고 말했다 —
**정지를 잡으라고 만든 지표가 정지를 가려줬다.**

현재: 한 시장이라도 `status=observed` 또는 `market_state=open` 일 때만 True.
무수확(후보 0개)은 여전히 True 다 — "살아있는데 조용한 것"과 "죽어서 조용한 것"은 다르다.

> 새 잡을 추가할 때 `effective_run` 을 상수로 두면 이 문서 위반이다.
> 반드시 "실제로 일했나"를 나타내는 런타임 값이어야 한다.
