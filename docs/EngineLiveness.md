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

감시 대상 트랙: `paper_engine`(크립토) · `toss_stock_scout`(주식) · `polymarket_paper`(폴리) · `sync_positions`.

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
