# 워커 매달림 증거 포착 (Worker Hang Evidence)

> WO-FCE-WORKER-HANG-02 Phase 0·1 정본.
> **사람이 그 순간에 붙어 있어야 하는 진단은 진단이 아니다.**

## 0. 왜 수 주째 원인을 못 잡았나

supervisor 는 매달림을 감지한 **직후 곧바로 `kill -9`** 했다. 감지와 파괴 사이에 아무것도
없었으므로, 증상이 잡힐 때마다 증거가 함께 사라졌다(D5).

소급 실측 (2026-08-14, `logs/restarts.jsonl` 122건 · `logs/supervisor.log`):

| 항목 | 값 |
| --- | --- |
| `heartbeat_stale` 재시작 (**매달림**) | **101회** |
| `port_down` 재시작 | 13회 |
| 초기(사유 미기록) | 8회 |
| 자동 복구 포기 | 6회 |
| `HEARTBEAT WRITE FAILED` | **0건** |
| 최근 재발 간격 | **17~30분** |

`HEARTBEAT WRITE FAILED` 가 0이라는 것이 중요하다. 하트비트 **쓰기가 실패한 게 아니다** —
쓰는 코드에 도달하지 못했다. D1~D3(이벤트 루프 블로킹) 가설이 유지된다.

감지 경과 초가 900~1063 에 몰려 있는 것도 같은 방향이다. 몇 시간 매달렸다면 값이 흩어진다.
이 분포는 **하트비트 정지 → 900초 초과 첫 틱에 감지 → 재시작 → 다시 정지**의 짧은 주기 반복이다.

## 1. 포착이 파괴에 선행한다 (C2)

```
supervisor.sh  check_heartbeat_hang()
   ├ log "heartbeat stale …"
   ├ record_restart
   ├ pids=$(lsof -ti :8875)
   ├ ★ capture-hang.sh  ← 여기가 새로 들어간 자리
   └ kill -9
```

`capture-hang.sh` 는 **절대 0이 아닌 코드로 끝나지 않는다**(`exit 0` 고정). 전 단계에 타임아웃이
있고 모든 실패를 삼킨다. 덤프가 실패하거나 느려도 재시작은 그대로 진행된다 — 감시자가
새로운 장애 원인이 되는 것을 금지한다(C3).

### 포기 경로에서도 포착한다

`자동 복구 포기`(1시간 재시작 3회 초과) 분기는 재시작 없이 `return` 한다. 포착을 넣지 않으면
**"재시작으로 해결되지 않는다"고 판단한 바로 그 시점**에 스택을 뜰 기회가 영영 없다.
실측에서 포기가 6회 있었다. 포착은 프로세스를 죽이지 않으므로 이 경로에 두어도 안전하며,
`HB_GIVEUP_NOTIFIED` 가드 안에 있어 15초마다 반복되지 않는다.

## 2. 도구 선택 — 실측으로 정했다

| 도구 | sudo | 파이썬 프레임 | 루프가 막혀도 | 채택 |
| --- | --- | --- | --- | --- |
| `py-spy dump` | **필요**(macOS) | 예 | 예 | ✗ |
| `sample <pid>` | 불필요 | 아니오(C 프레임만) | 예 | 보조 |
| **`faulthandler` + SIGUSR1** | 불필요 | **예(파일:라인)** | **예** | **주** |

- `py-spy dump --pid` → `This program requires root on OSX` (실측 2026-08-14). 쓸 수 없다.
- `sample` 은 `_PyEval_EvalFrameDefault` 까지만 보여 어느 파이썬 함수인지 모른다. 보조로 남긴다.
- `faulthandler.register()` 는 **C 레벨 시그널 핸들러**로 설치되어 프레임 객체를 직접 순회한다.
  인터프리터가 파이썬 코드를 실행하지 않는 상태(동기 C 호출에 갇힘)에서도 덤프가 나온다.

> **일반 `signal.signal` 핸들러를 쓰면 안 된다.** 그것은 바이트코드 사이에서만 실행되므로
> **블로킹 중에는 발화하지 않는다** — 정확히 우리가 필요한 순간에 침묵한다.

### ⚠️ SIGUSR1 은 등록된 프로세스에만 보낸다

`SIGUSR1` 의 **기본 동작은 프로세스 종료**다. faulthandler 를 등록하지 않은 프로세스에 보내면
그 자리에서 죽는다(회귀 테스트에서 `returncode -30` 으로 실측). 그래서 `capture-hang.sh` 는
**하트비트 파일이 자기 pid 라고 밝힌 워커에게만** 보낸다. pid 재사용이나 오인(프론트 8876 등)으로
엉뚱한 프로세스를 죽이면 포착이 파괴가 된다(C3).

## 3. 덤프 판독

위치: `logs/hang-dumps/<UTC타임스탬프>-<pid>.txt` (누적 원본은 `faulthandler.log`)

```
=== FCE hang capture ===   captured_at_utc / pid / reason
--- heartbeat ---          written_at, age_seconds  ← 몇 초째 멎었나
--- process ---            %cpu %mem etime state, open_files, threads
--- sqlite lock ---        *.db-wal / *.db-shm 크기 ← WAL 비대는 긴 트랜잭션 신호
--- last jobs ---          job-trace.jsonl tail  ← **start 만 있고 ok 가 없는 잡이 용의자**
--- loop lag ---           loop-lag.jsonl tail
--- python stack ---       전 스레드 파일:라인 (faulthandler)
--- native stack ---       sample 상위 60줄 (어떤 C 호출에 갇혔나)
```

**판독 순서**

1. `--- last jobs ---` 에서 `start` 만 있고 `ok` 가 없는 잡을 찾는다. 그것이 1순위 용의자다.
2. `--- python stack ---` 의 `Current thread`(메인 = 이벤트 루프)를 본다.
   - 정상이면 `asyncio/runners.py` → 셀렉터에서 대기 중이고 `app/` 프레임이 없다.
   - **`app/` 프레임이 보이면 그 파일:라인이 루프를 막고 있는 코드다.**
3. `--- native stack ---` 으로 어떤 C 호출인지 확인한다(sqlite3_step / SSL_read / recv 등).
4. `--- sqlite lock ---` 의 WAL 크기가 크면 긴 쓰기 트랜잭션을 의심한다.

건강한 순간의 기준선(2026-08-14 실측): 메인 스레드는 `asyncio/runners.py:118 run` 에서 대기,
`app/` 프레임 0개, 스레드 13개, open files 213개.

## 4. 이벤트 루프 지연 계측 (Phase 1)

`app/worker/hang_probe.py::LoopLagMonitor` — 1초 주기로 자고 일어나 예정 대비 초과분을 잰다.
5초 이상이면 ERROR 로그 + `logs/loop-lag.jsonl` 기록.

- **잡이 아니라 독립 태스크**다. 스케줄러 잡으로 만들면 측정 대상(루프)에 측정기가 함께 갇혀
  정작 정체 구간에서 기록이 끊긴다.
- 임계 미만이면 파일을 만들지 않는다 — 정상 구간의 기록은 노이즈이고 임계를 무의미하게 만든다.
- 오버헤드는 `GET /api/system/worker` → `loop_lag.write_overhead_seconds` 로 조회한다.
  배포 직후 실측: 57 샘플 · 쓰기 오버헤드 0.0초.

**가설 판정 기준**: 하트비트 정체 구간에서 루프 지연이 **동반 상승하면** D1~D3 확증(루프 블로킹),
상승하지 않으면 **반증**이며 Phase 2 설계를 다시 잡는다.

## 5. 잡 실행 추적 (Phase 1-2)

`logs/job-trace.jsonl` — 잡마다 `start` / `ok` 를 append-only 로 남긴다.
`kill -9` 로 프로세스가 죽으면 메모리 상태는 사라지므로 즉시 디스크로 내린다.

> **`start` 는 있는데 `ok` 가 없는 잡** = 그 틱에서 끝나지 못한 잡 = 매달림 용의자.

## 6. 보존 정책

DB 12.8GB 비대 선례가 있어 관측물에도 상한을 둔다.

| 대상 | 상한 | 설정 |
| --- | --- | --- |
| 덤프 파일 | 40개 (초과분 오래된 순 삭제) | `FCE_HANG_DUMP_KEEP` |
| `faulthandler.log` | 20MB 초과 시 뒤 5MB만 보존 | — |
| `loop-lag.jsonl` | 4MB 초과 시 회전 | — |
| 포착 전체 예산 | 10초 (supervisor 호출은 20초) | `FCE_HANG_DUMP_BUDGET_SECONDS` · `FCE_HANG_CAPTURE_TIMEOUT` |

## 7. 스크립트 재기동 (머지 ≠ 반영)

`supervisor.sh` · `capture-hang.sh` 는 **쉘 스크립트**다. 머지만으로는 실행 중인 감시 루프에
반영되지 않는다. 반드시 재기동한다.

```bash
scripts/local/stop-supervisor.sh
nohup bash scripts/local/start-supervisor.sh >> logs/supervisor.log 2>&1 &
```

확인:

```bash
pgrep -f scripts/local/supervisor.sh
curl -s localhost:8875/api/system/worker | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['hang_dump'], d['loop_lag'])"
```

`hang_dump.registered` 가 `true` 여야 SIGUSR1 경로가 살아 있다. `false` 면 증거가 남지 않는다.

## 8. 금지

- `kill -9` 앞의 포착 단계 제거·이동
- 포착 실패가 재시작을 지연·차단하는 구현
- 문턱(`FCE_STALE_LIMIT` 900초) 상향 — 감지를 늦추는 것은 수리가 아니라 은폐다
- faulthandler 를 일반 signal 핸들러로 교체
- 하트비트 pid 확인 없이 SIGUSR1 전송
- 증거 없이 원인 수리 착수 (Phase 2 는 Phase 0·1 증거로 파일:라인이 확정된 뒤)
- 새 푸시 알림 추가 — 관측은 로그·파일로 한다

---

## 9. 이관 접수 — 하트비트 테스트 플래키 (RISK-SIZING-01 D4)

`WO-FCE-RISK-SIZING-01` Phase 3 이 발견하고 Phase 4 가 재확인한 **선재 결함**이다.
RISK-SIZING 소관이 아니므로 여기로 이관한다(그 WO 의 금지 항목: "워커 플래키 테스트 수정").

```
tests/test_worker_scheduler.py::test_worker_three_ticks_create_snapshots_and_heartbeat
```

| 실사 | 결과 |
| --- | --- |
| Phase 3 (클린 `origin/main`) | 6회 중 **2회 실패** |
| Phase 4 (2026-08-19 · 단독 실행 6회) | 6회 중 **2회 실패** (40.9s·29.7s 실패 / 31.8s·33.5s·36.6s·33.3s 통과) |
| Phase 4 전체 스위트 | 1226 통과 · **이 1건만 실패** |

**두 실사에서 실패율이 같다(33%).** 워커 코드 변경 없이 재현되므로 테스트 타이밍 문제이거나
하트비트 경로 자체의 경합이다.

### 왜 이 문서 소관인가

실패하는 대상이 **하트비트**다. 이 WO 가 추적하는 매달림과 같은 영역이며,
`HARNESS.md` 의 "플레이키 금지 — 고정 sleep 기반 타이밍 테스트는 데드라인 폴링으로 작성한다"
규율에 걸린다. 실패 시각이 29~41초로 흔들리는 것도 고정 대기 흔적이다.

> ⚠️ **우연으로 넘기지 말 것.** 감지 신호(하트비트)를 다루는 테스트가 불안정하다는 것은
> 감지 자체가 경합에 취약할 수 있다는 뜻이다. 2026-07-28 11.7시간 사고는 "포트만 보는 감시가
> 매달림을 놓친" 사건이었다. 테스트가 잡는 것과 운영이 잡는 것이 같은 경로인지 확인이 필요하다.

착수 시 볼 것: 고정 sleep 여부 · 하트비트 기록과 검증 사이의 경합 · 커버리지 계측 부하에서만
깨지는지(CI 부하 의존).

---

## 10. 이관 접수 — 잡 큐 포화 · 잡 기아 (RISK-SIZING-01 Phase 4 후속)

실사 2026-08-18T23:25Z. **하트비트는 살아 있는데 잡이 실행되지 않는다.**

| 잡 | status | runs | 간격 | next_run_at | 상태 |
| --- | --- | --- | --- | --- | --- |
| `universe_scan` | idle | **0** | 1800s | 22:47:46Z | **38분 과거인데 미실행** |
| `scout_scan` | idle | **0** | 900s | 22:47:01Z | **38분 과거인데 미실행** |
| `toss_stock_scout` | **error** | 0 | — | — | `timeout after 120s` · 실패 3회 |

기동 후 40분 기준, 다른 잡들도 간격 대비 실행이 미달한다:

| 잡 | 간격 | 기대 | 실제 | 비율 |
| --- | --- | --- | --- | --- |
| `collect_whale_positions` | 30s | ~80 | 11 | 14% |
| `weekly_performance_report` | 60s | ~40 | 5 | 13% |

### 왜 이 문서 소관인가 — 감시가 볼 수 없는 정지다

`EngineLiveness.md` 의 stale 판정은 `last_effective_run_at` 기준이다. 그런데 **일부 잡은
정상 실행 중이고 하트비트도 신선하다.** 그래서 전체 워커는 "살아 있음"으로 판정되고,
`universe_scan` 이 한 번도 안 돌았다는 사실은 **어떤 신호도 내지 않는다.**

2026-07-28 사고의 교훈("포트만 보는 감시는 매달림을 놓친다")과 같은 구조다 —
이번에는 **워커 단위 감시가 잡 단위 기아를 놓친다.**

### 실제 피해

`universe_scan` 미실행 → `gate_passed` 발견 24시간 0건 → `paper_universe()` 가 watchlist
3종으로 축소 → **페이퍼 진입 사실상 0건**(2026-08-17T08:00 이후 신규 0). 표본 수집이 임계
경로인데 표본이 안 쌓인다. 정본: [`validation/SAMPLE_RATE.md`](validation/SAMPLE_RATE.md)

### 착수 시 볼 것

- `toss_stock_scout` 120초 타임아웃이 실행 슬롯을 점유하는지 (executor 워커 수 · 직렬화 여부)
- `next_run_at` 이 과거인데 `idle` 로 남는 조건 — 스케줄러가 미스파이어를 버리는지
- **잡별 기아 감지 신호가 없다** — 워커 생존과 별개로 "이 잡이 N주기 연속 미실행"을 낼 수 있어야 한다

---

## 11. Phase 2 — 잡 기아 수리 (2026-08-19)

> §10 이 이관받은 문제를 수리했다. **간격·타임아웃 무변경 · 잡 비활성화 0건**(C1·C2·C3).

### 2-1 판정: D3/D5 가 원인, **D2 가 증폭기**

Phase 1 계측기를 먼저 읽었다. 두 데이터가 판정을 갈랐다.

`logs/loop-lag.jsonl` (`LoopLagMonitor` · 표본 860건):

```
평균 11.66s · 중위 10.09s · 최대 49.73s
5초 초과 860건 (100%) · 20초 초과 97건
```

**이벤트 루프가 상시 막혀 있었다.**

`logs/job-trace.jsonl` (24시간 · 16004 이벤트) — `start`/`ok` 쌍으로 잡별 실행 시간:

| 잡 | 평균 | 최대 | 미완(start만) |
| --- | --- | --- | --- |
| `sync_positions` | 51.6s | 1299.6s | 61/414 |
| `refresh_calibration_cache` | 99.6s | 1295.6s | 2/56 |
| `discover_whale_leaderboard` | 175.4s | 983.4s | 4/48 |
| `polymarket_paper` | 59.6s | 1005.6s | 15/315 |
| `universe_scan` | 86.3s | 122.5s | 1/19 |
| `scout_scan` | 85.4s | 616.2s | 3/17 |
| **`toss_stock_scout`** | 46.8s | 102.1s | **111/576** |

`start` 자체가 0회인 잡은 4개뿐이고 **전부 일일 잡(정상)** 이었다 —
즉 **순수 misfire 기아는 없었고**, 문제는 실행 점유였다.

실행기 포화 확인:

```
cpu_count 10          → 기본 ThreadPoolExecutor 14 워커
동시 실행 잡 실측 최대  11개
leaderboard.py        자체 12스레드 풀 추가 생성
```

**풀 포화 + GIL 경합 → 이벤트 루프 기아 → 예정 시각 지연.**

그리고 **D2 가 그 지연을 소실로 바꿨다.** APScheduler `misfire_grace_time` 기본값은 **1초**다.
1초를 넘겨 지나간 발화는 지연이 아니라 **건너뛴다**. 루프 지연이 평균 11.66초이므로
거의 모든 발화가 사라졌고, 그것이 `runs=0` + `next_run_at` 과거 조합의 정체다.
`app.log` 에 `was missed` 경고 **1689건**이 남아 있었으나 잡 이름이 없어 추적 불가였다.

D5 확인: `asyncio.wait_for` 는 코루틴만 취소하고 **스레드 안 동기 코드는 취소되지 않는다.**
`toss_stock_scout` 은 간격 **10초**인데 평균 46.8초 — 주기 내 완료가 원리적으로 불가능하며
미완 111건이 슬롯 누수의 결과다.

### 2-2 수리: misfire 정책 명시 — **실행률 14% → 74%**

잡 성격별로 grace 를 나눴다(`manager._misfire_grace_seconds`):

| 종류 | grace | 근거 |
| --- | --- | --- |
| 주기 관측 잡 | 자기 간격 (상한 30분) | 늦어도 다음 주기 전이면 관측 가치가 있다 |
| 시각 민감 잡 (알림·펄스) | 30초 | 늦은 발송은 무의미하거나 해롭다 |
| 일일 잡 | 상한 30분 | 하루치 grace 는 "언제 돌아도 됨" = 기아 은폐 |

`job_defaults` 에도 안전망을 둬 등록 누락 시 1초로 되돌아가지 않게 했다.

**적용 전후 실측** (관측 창 12분 · 표본 7개):

| 잡 | 수리 전 | 2-2 후 |
| --- | --- | --- |
| `collect_whale_positions` (30s) | **14%** | **96%** |
| `heartbeat` (60s) | 미달 | **100%** |
| `paper_engine` (90s) | 48% | **100%** |
| `sync_positions` (90s) | 미달 | **100%** |
| `detect_closures` · `sync_and_analyze` | 미달 | **100%** |
| **전체** | **14~33%** | **74%** |
| **misfired** | 1689건(로그) | **0건** |
| **굶은 잡** | `universe_scan`·`scout_scan` | **0개** (건강 34) |

`universe_scan`·`scout_scan` 이 **처음으로 실행됐다**(이전엔 영구 `runs=0`).
그 결과 universe discovery 가 **최근 1시간 0건 → 667건**으로 살아났다.

> ⚠️ **그러나 평가 유니버스는 여전히 3종이다.** discovery 는 살아났지만 `gate_passed` 가
> 0건이라(`backtest_sample` 0<30 · `backtest_win_1r_ci_low` · `stage2_template`)
> `paper_universe()` 에 도달하지 못한다. **큐는 더 이상 병목이 아니고, 남은 제약은
> 발견 품질 게이트다** — 이 WO 범위 밖이다(C5). 정본:
> [`validation/SAMPLE_RATE.md`](validation/SAMPLE_RATE.md)
>
> (최초 보고에서 "3종 → 5종 회복"이라고 썼으나 **틀렸다** — 퍼널 40행을 봉 구분 없이
> 센 값이었다. 봉별로는 3종이고 NBISUSDT 는 08-17, BASEDUSDT 는 08-14 가 마지막 평가다.)

남은 미달은 `toss_stock_scout` 15% 하나이며 **misfire 가 아니다** — 간격 10초 < 실행 46.8초의
과다 스케줄이다. 간격 조정은 C1 금지이므로 격리로 다뤘다(2-3).

### 2-3 실행 격리

실측으로 특정한 무거운 잡을 전용 풀(4워커)로 옮겼다:
`toss_stock_scout` · `refresh_calibration_cache` · `discover_whale_leaderboard` ·
`collect_derivatives`.

`sync_positions` 는 **제외했다** — 평균 51.6초는 훅들의 합계이고 그 안에 `paper_engine`
(표본 생산자)이 있다. 격리하면 표본 생산자를 좁은 풀에 넣는 셈이라 방향이 반대다.

> ⚠️ **격리는 누수를 막지 못하고 범위를 제한한다.** 근본 수리는 동기 코드에 취소 지점을
> 두는 것이며 별건이다. 그 사실을 `_run_in_thread` docstring 에 명시했다.

### 2-4 잡 단위 기아 판정 — 감시 단위를 실패 단위에 맞춘다

`liveness.job_starvation()` 이 두 형태를 잡는다:

| 판정 | 조건 |
| --- | --- |
| `never_ran_and_overdue` | `runs=0` 인데 `next_run_at` 과거 — **이번 사고의 형태** |
| `interval_overrun` | 유효 실행이 자기 간격의 3배를 초과 |

`last_effective_run_at` 기준이다 — 조기 반환을 성공으로 세면 기아가 정상으로 보인다
(§`EngineLiveness.md` D3 와 같은 이유). `disabled` 잡은 제외한다(오탐이 쌓이면 신호가 무시된다).

`GET /api/system/worker` → `job_starvation` 으로 조회 가능하며 **워커 생존과 분리 표시**된다.
신규 푸시 0건. 배선 직후 실측에서 `toss_stock_scout` 을 `never_ran_and_overdue` 로
즉시 지목했다 — 판정이 실제로 동작한다.

### C8 침묵 금지

`EVENT_JOB_MISSED` 리스너로 잡별 `misfired`·`last_misfire_at`·`misfire_grace_seconds` 를
기록하고 마이그레이션 `0037` 로 영속화했다.

**`skipped` 와 분리해서 센다** — `skipped` 는 잡이 느린 것, `misfired` 는 스케줄러가 아예
실행하지 않은 것이다. 한 칸에 합치면 misfire 가 정상 스킵에 묻힌다(그래서 두 달 숨었다).

`misfire_grace_seconds` 를 영속화한 이유는 **정책이 조용히 1초로 되돌아갔는지 조회로 확인**할
수 있어야 한다는 것이다. 실제로 첫 배선에서 이 값이 0 으로 보이는 결함이 있었고
(`status()` 가 영속 행을 우선하는데 스키마에 칼럼이 없었다) 그 덕에 잡아냈다.

### 부수 확인 — 선재 플래키 테스트

`test_worker_three_ticks_create_snapshots_and_heartbeat` 가 **단독 6회 전부 통과**했다
(§9 기준선: 6회 중 2회 실패). misfire 로 소실됐던 실행이 실제로 일어나면서 안정화된 것으로
보인다 — **수리가 실재한다는 방증**이다. 다만 커버리지 계측 부하에서는 여전히 흔들리므로
§9 항목으로 남긴다.

### 2-5 이월 항목 종결 (2026-08-19)

Phase 2 가 미완으로 남긴 "실동작 증명"을 `DISCOVERY-UNBLOCK-01` 이 닫았다.

| 2-5 수용 기준 | 결과 |
| --- | --- |
| 유니버스 종목 수 복구 | **3종 → 10종** |
| `gate_passed` > 0 | **미달 — 그리고 의도적이다.** 알림 자격은 바꾸지 않았다. 급유 경로를 분리해 우회했다 |
| **진입 1건 이상** | **2건** (2026-08-19T08:00 · DELLUSDT·INTCUSDT) |
| 호가 관측 > 0 | **2건** |
| 사이징·잠금 첫 라이브 발동 | **사이징 발동 확인** (계획 리스크 2.50 정확) · 잠금은 아직 조건 미발생 |

Phase 2 의 큐 수리가 선행 조건이었다 — `universe_scan` 이 안 돌면 발견 자체가 없고,
발견이 없으면 급유 경로를 고쳐도 넣을 것이 없다. **두 수리가 직렬로 필요했다.**

정본: [`validation/DISCOVERY_GATE.md`](validation/DISCOVERY_GATE.md) ·
[`validation/SAMPLE_RATE.md`](validation/SAMPLE_RATE.md)
