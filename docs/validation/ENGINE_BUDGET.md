# 엔진 실행 예산 (Engine Budget)

> WO-FCE-ASSET-CLASS-01 3-1 정본.
> 정본 코드: `backend/app/validation/engine_budget.py`
> 관련: [`ASSET_CLASS.md`](ASSET_CLASS.md) · [`../WorkerHangEvidence.md`](../WorkerHangEvidence.md) §12

---

## 0. 왜 예산을 먼저 세우는가

```
sync_positions   timeout after 450s · runs=0 · fail=3
paper_engine     status=running · runs=0
훅 잡            유효 실행 16.5~25.6시간 없음
```

`DISCOVERY-UNBLOCK-01` 이 유니버스를 3 → 15종으로 늘렸다. `paper_engine` 루프가 봉 변경
확인 **전에** 심볼당 약 30초짜리 분석을 무조건 호출했고, 15 × 30 = **450초**가 예산
450초를 정확히 넘겼다.

> **표본을 늘리려는 변경이 표본을 0으로 만들었다.**

`ASSET-CLASS-01` 3-2 는 심볼을 3 → 289 로 **다시 20배** 늘린다. 예산을 수식으로 세우지
않으면 같은 사고가 규모만 키워 재발한다. 그것이 C1 이고 이 문서가 그 근거다.

---

## 1. 예산 식

```python
worker/manager.py:303   _job_timeout_seconds(name)
budget = clamp(interval × multiplier, floor, ceiling)
```

| 항목 | 값 | 출처 |
| --- | ---: | --- |
| `sync_positions` 주기 | 90초 | `worker_sync_positions_interval_seconds` |
| 배수 | 5 | `worker_job_timeout_multiplier` |
| 하한 / 상한 | 120 / 1,800초 | `worker_job_timeout_{floor,ceiling}_seconds` |
| **실행 예산** | **450초** | 90 × 5 |
| 심볼당 분석 | **약 30초** | 실측 (`universe_needing_evaluation` docstring · §12) |

`engine_budget.py` 가 같은 식을 복제하고, 회귀 테스트가 워커와 갈라지면 실패시킨다
(`test_timeout_formula_matches_the_worker`).

---

## 2. 한계는 **둘**이다 — 하나로 보면 틀린 곳을 조인다

| 한계 | 무엇이 터지나 | 결정 변수 |
| --- | --- | --- |
| **실행당 예산** | 잡이 `timeout` 으로 죽는다 → 그 틱의 **모든** 심볼이 평가 안 됨 | 실행당 심볼 수 상한 |
| **순회 주기** | 죽지는 않지만 한 봉 안에 전 심볼을 못 돈다 → 일부가 봉을 건너뜀 | 유니버스 크기 |

`paper_engine_max_symbols_per_run` 이 **둘을 분리한다.** 상한이 있으면 유니버스가 커져도
실행당 비용은 고정이고, 커지는 것은 순회 주기다.

```
실행당 비용 = min(유니버스, 상한) × 30초        ← 상한이 있으면 유니버스와 무관
순회 시간   = ceil(유니버스 ÷ 상한) × 90초      ← 여기서만 유니버스가 커진다
```

---

## 3. 실행당 한계 (3-1 작업 2)

| 항목 | 값 |
| --- | ---: |
| 예산 | 450초 |
| 하드 상한 (예산 정확히 소진) | **15종** |
| 안전 상한 (여유 40%) | **9종** |
| 현행 설정 `paper_engine_max_symbols_per_run` | **6종** → 180초 (예산의 40%) |

> 하드 상한 15 는 **사고 당시의 유니버스 크기와 정확히 같다.** 우연이 아니라 그것이 터진
> 지점이다. 하드 상한에 맞추면 루프 지연 한 번에 다시 터지므로 안전 상한을 쓴다.

현행 6은 안전 상한 9 이내다 — **실행당 예산은 지금 안전하다.**

---

## 4. 순회 한계 — 3-2 단계 확대의 실제 제약

상한 6 · 주기 90초 기준:

| 유니버스 | 실행 횟수 | 순회 시간 | 한 봉(240분) 이내 |
| ---: | ---: | ---: | --- |
| 3 | 1 | 1.5분 | ✓ |
| 15 | 3 | 4.5분 | ✓ |
| 40 | 7 | 10.5분 | ✓ |
| **289** | **49** | **73.5분** | **✓** |
| 960 | 160 | 240.0분 | ✓ (천장) |
| 1,200 | 200 | 300.0분 | ✗ |

```
유니버스 천장 = 상한 × (한 봉 ÷ 주기) = 6 × (14,400 ÷ 90) = 960종
```

### 3-1 이 3-2 에 넘기는 숫자

> **289종은 감당 가능하다.** 순회 73.5분으로 4시간봉 한 봉 안에 세 번 돌고도 남는다.
> 이 상한에서의 천장은 **960종**이다.

**예산은 3-2 의 병목이 아니다.** 그 결론은 `ASSET_CLASS.md` §2 를 바꾸지 않는다 —
3-2 를 막는 것은 예산이 아니라 **실적 데이터 공급원 부재**다.

---

## 5. 무엇이 아직 안 닫혔나 (D1 · `WorkerHangEvidence §12`)

이 문서는 **수식과 한계**까지다. §12 를 닫으려면 24시간 실측이 필요하고, 그것은 운영
호스트에서만 나온다:

| 수용 기준 | 상태 |
| --- | --- |
| 24시간 `sync_positions` 타임아웃 0건 | **미확인** — 호스트 관측 필요 |
| `paper_engine` 유효 실행 정상 | **미확인** — 호스트 관측 필요 |
| 심볼 수 대비 예산 한계 실측 | **완료** — §3·§4 |
| 잡 실행률 76~77% 유지 | **미확인** — 호스트 관측 필요 |

### 확인 절차

```bash
curl -s localhost:8875/api/system/worker | python3 -c "
import json,sys
d=json.load(sys.stdin)
for name in ('sync_positions','paper_engine'):
    print(name, (d.get('jobs') or {}).get(name))
print('굶은 잡:', (d.get('job_starvation') or {}).get('starved'))
print('loop_lag:', d.get('loop_tag') or d.get('loop_lag'))
print('총 misfired:', sum(int(j.get('misfired') or 0) for j in d['jobs'].values()))
"
```

통과 기준은 `RESTART_RUNBOOK.md` §"사전 점검 추가 — 잡별 실행률"과 같다.
**`runs` 숫자만 보지 말 것** — 재시작 직후엔 전부 0이고 정상처럼 보인다.

---

## 6. 상한을 올리고 싶다면

순서가 있다. 뒤집으면 사고가 재발한다.

1. **심볼당 소요를 다시 잰다.** 30초는 2026-08 실측이다. 분석 경로가 바뀌면 값이 바뀐다
2. **안전 상한을 다시 계산한다** — `per_run_limit()`
3. **순회 천장을 확인한다** — `max_universe_for_one_bar()`
4. 한 단계만 올리고 **24시간 실행률을 대조**한다 (C6 — 76~77% 기준)

주기(`worker_sync_positions_interval_seconds`)를 줄이면 순회는 빨라지지만 **예산도 함께
줄어든다**(예산 = 주기 × 5). 한쪽만 보고 조이면 다른 쪽이 터진다.

---

## 7. 금지

- 상한 없이(`0`) 운영 — 유니버스 크기가 곧 실행당 비용이 된다. 사고의 형태 그대로다
- 하드 상한(15)에 맞춰 설정 — 지연 한 번에 터진다
- 심볼당 소요 재측정 없이 상한 인상
- 24시간 실행률 대조 없이 단계 확대 (C6)
- 주기와 배수를 함께 바꾸면서 한쪽만 검증
