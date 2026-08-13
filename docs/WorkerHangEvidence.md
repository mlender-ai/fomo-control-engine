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
