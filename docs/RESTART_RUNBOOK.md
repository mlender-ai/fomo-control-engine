# 검증 재시작 런북 (Restart Runbook)

> WO-FCE-WINDOW-ANCHOR-01 산출물. **재시작 자체는 이 문서가 실행하지 않는다** —
> 실제 재시작은 `WO-FCE-VALIDATION-RESTART-01` 소관이고, 여기는 **앵커가 제대로 걸렸는지
> 확인하는 절차**다.

## 0. 왜 이 런북이 생겼나

재시작을 실행해도 **검증 카운터가 리셋되지 않았다.**

```
paper/service.py:620   repo.upsert_paper_engine_state(BENCHMARK_SYMBOL, ...)   ← 한 행만 갱신
validation/ 전체에서 paper_engine_state·started_at 를 읽는 코드 = 0건
```

`start_paper_benchmark(reset=True)` 가 쓰는 것은 스코어보드용 한 행뿐이었고, 검증 판정
(`유효일 / 표본 / 국면`)에는 **창 개념 자체가 없었다.** `effective_days` 는 트랙의 역사상 모든
유효일 개수였고 표본 SQL 8종 전부 시각 조건이 없었다.

> 재시작한 다음 날 대시보드를 보면 스코어보드는 0인데 검증은 "유효일 28/28"이 그대로 뜬다.
> **두 화면이 서로 다른 창을 말하는 분열 상태**가 되고, 어느 쪽이 진실인지 표시로 구분할 수 없다.

## 1. 창은 필터지 삭제가 아니다

| 원칙 | 구현 |
|---|---|
| **행 삭제 0건** | 앵커는 `WHERE day >= ?` · `WHERE t >= ?` 필터다. `observation_coverage` · `paper_trades` · `stock_paper_fills` · `poly_positions` 어느 행도 지우지 않는다 |
| **과거 조회 가능** | 앵커 이전 데이터는 그대로 남는다. 창 밖으로 나가는 것이지 사라지는 것이 아니다 |
| **창 밖 제외 건수 노출** | `window_excluded` 에 진입·표본·관측일별 제외 수 — "0건"과 "창 밖 40건"은 다른 상태다 |
| **앵커 이력 보존** | `validation_windows` 는 append-only. 회차마다 새 행이라 과거 창 앵커가 남는다 |

정본 코드: `backend/app/validation/window_anchor.py`
스키마: `backend/app/db/migrations/0035_validation_windows.sql`

```sql
CREATE TABLE validation_windows (
    track TEXT NOT NULL,          -- crypto | stock_kr | stock_us | poly
    window_seq INTEGER NOT NULL,  -- 창 회차. 재시작마다 +1
    anchored_at TEXT NOT NULL,    -- 표본 필터 하한 (UTC datetime)
    anchor_day TEXT NOT NULL,     -- 관측일 필터 하한 (UTC date)
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (track, window_seq)
);
```

## 2. 앵커 미설정 = 현행 동작

앵커가 `None` 이면 **어떤 조건도 걸지 않는다.** 이 WO 자체는 기존 판정을 한 숫자도 바꾸지
않으며, `test_absent_anchor_reproduces_current_numbers` 가 그것을 강제한다.

**지금 운영 DB 에는 앵커가 없다.** 따라서 배포 직후 숫자는 배포 전과 동일하다 — 화면이 바뀌는
것은 재시작을 실제로 실행한 시점부터다.

## 3. 재시작 후 확인 절차

`start_paper_benchmark(reset=True)` 는 스코어보드 한 행 + **4트랙 앵커**를 같은 시각으로 연다.
실행 후 아래를 순서대로 확인한다.

### 3.1 4트랙 앵커가 전부 열렸는가

```sql
SELECT track, window_seq, anchored_at, anchor_day, reason
FROM validation_windows
WHERE (track, window_seq) IN (SELECT track, MAX(window_seq) FROM validation_windows GROUP BY track);
```

**4행이 나와야 한다.** 한 트랙이라도 빠지면 그 트랙만 구 창에 남아 분열이 재발한다.

### 3.2 스코어보드 창과 검증 앵커가 같은 시각인가

`GET /api/paper/scoreboard` → `validation_window_alignment`

```json
{"aligned": true, "misaligned_tracks": [], "scoreboard_started_at": "..."}
```

`aligned: false` 면 `misaligned_tracks` 가 **어느 트랙이 뒤처졌는지 이름으로** 알려준다.
불린 하나로 뭉치지 않는 이유가 이것이다.

### 3.3 창 밖 제외 건수가 예상과 맞는가

`GET /api/paper/diagnosis` → `sample_viability.tracks.<track>.window_excluded`

```json
{"applied": true, "entries": 42, "scored_samples": 18, "coverage_days": 24}
```

제외 건수가 **0인데 재시작을 했다면** 앵커가 안 걸린 것이다. 반대로 제외 건수가 전체와 같으면
앵커가 미래로 잘못 잡혔다.

### 3.4 행 삭제가 없었는가

```sql
SELECT
  (SELECT COUNT(*) FROM observation_coverage) AS coverage,
  (SELECT COUNT(*) FROM paper_trades)         AS crypto,
  (SELECT COUNT(*) FROM stock_paper_fills)    AS stock,
  (SELECT COUNT(*) FROM poly_positions)       AS poly;
```

재시작 **전후 값이 같아야 한다.** 하나라도 줄었으면 창 이동이 아니라 삭제가 일어난 것이고,
그 경우 과거 검증을 영원히 재구성할 수 없다.

### 3.5 화면에 창 회차가 표기되는가

검증 한 줄이 다음 형태여야 한다:

```
[창 2회차 · 앵커 2026-08-17] 크립토 페이퍼  유효일 3/28  ·  표본 0/30  ·  국면 0/2     [미달: ...]
```

회차·앵커가 안 보이면 어느 창의 숫자인지 알 수 없다.

## 4. 창 상태 3종

`D+28에 아무 일도 일어나지 않는다.` 목표 유효일을 넘겨도 카운터는 계속 올라갔고, 그 상태를
표현할 이름이 없어 화면은 계속 "진행 중"으로 보였다.

| 상태 | 조건 | 표시 |
|---|---|---|
| `running` | 목표일 이내, 조건 미충족 | 진행 중 |
| `complete` | 유효일·표본·국면 3조건 전부 충족 | 조건 충족(완료) |
| `overrun` | **목표 유효일 경과, 조건 미달** | 목표일 경과·조건 미달 |

유효일은 목표값에서 **상한 처리하지 않는다** — `31/28 (목표일 경과)` 로 낸다. `28/28` 에서
멈춰 보이면 창이 이미 지났다는 사실이 화면에서 사라진다.

## 4-2. 생존 판정 문턱 2종 (WO-FCE-LIVENESS-VERDICT-01)

재시작을 하기 전에 **감시 지표가 사실을 말하는지** 먼저 봐야 한다. 이 지표가 틀린 채로
재시작하면 새 창의 유효 관측일이 무엇 때문에 깎였는지 영원히 알 수 없다.

### 왜 문턱이 둘인가

예전에는 사망과 복구가 **같은 900초**를 썼다. 901초 사망, 899초 복구 — 히스테리시스가 없어
문턱 근처에서 진동했다. 실측 2026-08-13 17:04, **709초 묵은 하트비트가 "다시 갱신되고
있습니다"로 발송됐다.** 12분 가까이 갱신이 안 된 상태인데 정상 판정을 받았다.

| 판정 | 문턱 | 근거 | 상수 |
|---|---:|---|---|
| **사망** | `age > 900초` | 워커 기대 하트비트 주기(300초)의 3배 | `FCE_STALE_LIMIT` |
| **복구** | `age ≤ 300초` **가** `180초` 지속 | 복구는 한 주기 안에 찍혀 있어야 하고, 단발이면 복구가 아니다 | `FCE_FRESH_LIMIT` · `FCE_RECOVERY_SUSTAIN_SECONDS` |

두 문턱 사이 **300~900초 구간은 중간 상태**다. 사망도 복구도 아니며 알림을 만들지 않고
로그로만 남긴다 — `복구 미달 — 하트비트 N초 (복구 문턱 300초 초과, 사망 문턱 900초 이내)`.

> **`FCE_STALE_LIMIT` 은 올리지 않는다.** 900을 늘리면 사망 판정이 줄어들 뿐 매달림은 그대로다.
> 그건 수리가 아니라 은폐다. 이 문턱들의 변경 방향은 복구를 **더 늦게** 선언하는 쪽뿐이다.

지속 조건은 **시간 기준**이라 감시 루프 주기와 무관하게 같은 의미다 — deadman 은 60초 주기라
3틱, supervisor 는 15초 주기라 12틱에 해당한다.

### 포기 래치 해제 조건

`자동 복구 포기`는 "재시작으로 풀리지 않는 문제"라는 판정이다. 예전에는 그 래치가
**`age ≤ 900` 한 틱만으로 풀렸다** — 16:52의 단발 하트비트 하나가 16:20의 포기를 지웠다.

이제 래치는 **복구와 같은 기준**(신선 300초 이내가 180초 지속)으로만 풀린다. 래치를 더 오래
유지하는 방향이므로 복구 폭주 금지(쿨다운 10분·1시간 3회 상한)를 약화시키지 않는다.

### 재시작 카운터가 무엇을 세는가

예전 알림의 `최근 재시작 27회`는 `supervisor.log` 에서 `"down → restart"` 를 grep 한 값이었다.
그 문자열은 **포트 다운 경로에만** 있고 매달림 재시작에는 없다 — **카운터가 매달림 재시작을
한 건도 세지 않았다.** 27이 8시간 동안 얼어 있던 것은 "재시작이 없었다"가 아니라 "포트 다운
재시작이 없었다"만 뜻한다.

이제 원장(`logs/restarts.jsonl`) 하나만 보고, 사유·대상·창을 문구에 박는다:

```
최근 24h 재시작 5회 (매달림 4 · 포트다운 1 / backend 5 · frontend 0)
```

포기 판정도 **같은 원장·같은 함수**를 쓴다(`scripts/local/lib/liveness.sh`). 예전에는 알림이
`supervisor.log` grep, 포기 판정이 `restarts.jsonl` 이라 두 숫자가 서로를 설명할 수 없었다.

### 사전 점검 항목 (재시작 전 반드시)

- [ ] 최근 `복구` 알림의 `경과 N초`가 **복구 문턱(300초) 이하**인가 — 초과면 A1이 재발한 것이다
- [ ] `logs/restarts.jsonl` 의 `heartbeat_stale` 건수가 알림 문구의 `매달림 N` 과 일치하는가
- [ ] `자동 복구 포기` 직후 60초 안에 중복 `사망 감지` 가 오지 않는가
- [ ] `pgrep -fa run-backend.sh` 가 **1개**인가 (이중 기동 없음)

증거 수집은 한 번에:

```bash
bash scripts/local/collect-liveness-evidence.sh > ~/liveness-evidence.txt 2>&1
```

### 스크립트 반영 — 머지 ≠ 서버 반영

`supervisor.sh` 는 **세션에 상주하는 루프**다. 머지해도 **돌고 있는 프로세스는 옛 코드 그대로**다.
반드시 재기동한다:

```bash
cd ~/fomo-control-engine && git pull
bash scripts/local/stop-supervisor.sh
bash scripts/local/start-supervisor.sh
# 반영 확인 — 기동 로그가 새로 찍히는지
tail -5 logs/supervisor.log
grep -c 'liveness.sh' scripts/local/supervisor.sh    # 1 이상이면 새 코드
```

`deadman.sh` 는 매 틱 새로 실행되므로 별도 재기동이 필요 없다 — 다음 60초 틱부터 새 코드다.

## 5. 재시작 전 확인 — 이 런북이 닫지 않는 것

`VALIDATION-RESTART-01` §0 의 확정 2건은 **여전히 미해결**이며 이 WO 가 해결하지 않는다.

| 항목 | 상태 | 미해결 시 결과 |
|---|---|---|
| 목표 N · 통계 방법 | **미정** | 미정 상태로 재시작하면 D+28에 **다시 판정 불가** |
| 호스트 지속성 | **미정** | 새 창 **첫날부터** US 정규장 후반 유실 |
| 폴리 처리 방침 | **미정** | 재시작해도 `STRUCTURALLY_BLOCKED` 그대로 |

> **결정이 안 닫힌 채로 날짜를 다시 잡으면 같은 일이 반복된다.** 날짜보다 결정이 먼저다.

미결 항목은 `pending_decisions` 를 통해 대시보드·주간 리포트에 상시 노출된다.

## 6. 관련 문서

- [`validation/COMPLETION_DEFINITION.md`](validation/COMPLETION_DEFINITION.md) — 창 앵커 정의·창 상태 3종
- [`validation/SAMPLE_RATE.md`](validation/SAMPLE_RATE.md) — 계수 범위가 생애 누적에서 창 기준으로 바뀐 사실
- [`validation/REPLAY_HARNESS.md`](validation/REPLAY_HARNESS.md) — 앵커가 재판정 범위에 미치는 영향
- [`validation/REQUIRED_SAMPLE.md`](validation/REQUIRED_SAMPLE.md) — 목표 N 미확정 상태의 근거


## 매달림 덤프 (WO-FCE-WORKER-HANG-02)

매달림 감지 시 `kill -9` **직전에** 증거가 자동으로 저장된다. 사람이 붙어 있을 필요가 없다.

- 위치: `logs/hang-dumps/<UTC타임스탬프>-<pid>.txt` (누적 원본 `faulthandler.log`)
- 도구: `faulthandler` + SIGUSR1(주, 파이썬 파일:라인) · `sample`(보조, 네이티브 프레임)
  - `py-spy` 는 macOS 에서 root 가 필요해 쓰지 않는다(실측).
  - 설치 불필요 — 둘 다 기본 제공(`sample` 은 macOS 내장, `faulthandler` 는 표준 라이브러리).
- 판독 순서·기준선·보존 정책: [`WorkerHangEvidence.md`](WorkerHangEvidence.md) §3

**스크립트는 머지만으로 반영되지 않는다.** `supervisor.sh` 변경 후에는 반드시 재기동하고
`GET /api/system/worker` 의 `hang_dump.registered == true` 를 확인한다.
