# WO-FCE-ENGINE-LIVENESS-01 — 런타임 실측 스냅샷

- 측정 시각: **2026-07-27 22:54 KST** (프로덕션 머신, 실제 구동 중인 프로세스·DB 대상)
- 측정 기준 코드: main @ `f464d4b` (WO-FCE-PAPER-OBSERVABILITY-01 머지분)
- 선행 WO 스냅샷의 미수집분(2·3·4·5번) **전량 청산 완료**

---

## 1. `GET /api/system/paper/diagnosis`

```
HTTP 404 {"detail":"Not Found"}
```

**→ 결정적 발견.** 선행 WO가 만든 진단 표면이 실행 중인 프로세스에 **존재하지 않는다.**

## 2. `GET /api/system/worker` — 잡 하트비트 전량

`flag_warnings` 키 **부재**(선행 WO가 추가한 필드) → 구버전 코드 구동 확정.

| 항목 | 값 |
| --- | --- |
| status / scheduler_running | running / True |
| telegram_sender_enabled | True |
| **is_muted** | **False** (뮤트 아님 — 침묵의 원인이 아니다) |

잡 28종 전량: **실패 0 · 백오프 0 · stale 0**. `current_interval_seconds == base_interval_seconds` 전부 일치.
주요 잡 실행 횟수: `toss_stock_scout` 35,014 · `paper_engine` 3,798 · `polymarket_paper` 5,830 · `periodic_pulse` 3,798.
(`telegram_bot`은 interval 0/0 상주 태스크라 runs=0이 정상.)

## 3. 플래그 실효값

| 플래그 | 값 |
| --- | --- |
| `FCE_TOSS_STOCK_SCOUT_ENABLED` | **True** |
| `FCE_STOCK_PAPER_ENGINE_ENABLED` | True |
| `FCE_POLYMARKET_PAPER_ENABLED` | True |
| `FCE_TELEGRAM_ALERTS_ENABLED` | True |
| `FCE_PAPER_TELEGRAM_ALERTS_ENABLED` | True |

**→ 작업 7 답: 주식 구동 잡은 켜져 있다.** D6의 "주식이 한 번도 평가된 적 없음" 가설은 **기각**(실제로 7/23까지 평가·주문 산출물 존재).

## 4. 뮤트 상태

`muted_until: None`, `is_muted: False`. **뮤트는 이번 침묵과 무관.**

## 5. 프로세스 상태

| 항목 | 값 |
| --- | --- |
| 8875 백엔드 | PID 27923, 기동 **2026-07-23 02:53** → **4일 20시간 무재시작** |
| 8876 프론트 | PID 27946, 동일 시각 |
| keepalive 감시자 | PID 27913 실행 중, 재시작 이력 5회(전부 7/23 설치 시점) |

**→ 서버는 죽지 않았다.** keepalive는 정상. 그러나 **7/24 20:09 머지된 관측성 PR 코드를 프로세스가 로드한 적이 없다**(기동이 머지보다 이틀 빠름).

## 6. DB / 디스크

DB 6.8GB (직전 WO의 VACUUM 후 5.4GB → 4일간 +1.4GB 재증가) · 디스크 여유 521GB.

## 7. 로그

- `logs/app.log` 최종 기록 **2026-07-19** (8일 전) — 현재 프로세스는 여기에 쓰지 않는다(supervisor가 `logs/backend.log`로 리다이렉트). 로그 경로 이원화로 진단이 어려웠다.
- `logs/backend.log` 정상 기록 중(7.8MB), **error/exception/traceback 0건**.

---

## 트랙별 실제 산출물 (DB 실측)

| 트랙 | 최종 산출물 | 판정 |
| --- | --- | --- |
| **주식(토스)** | `toss_quotes`·`toss_rankings_snapshot` **2026-07-23 13:33** · `stock_paper_orders` 7/23 13:30 | **4일간 완전 정지** — 그런데 `toss_stock_scout` 잡은 35,014회 "성공" |
| **크립토** | `paper_gate_funnel` **7/27 08:00**(당일) · `paper_trades` 13건 전부 closed | **살아있음, 신규 진입 0** |
| **폴리** | `poly_estimates` **7/27 13:51**(당일) | **살아있음** |

알림은 정상 발화 중: 24시간 41건 발화 · 35건 전달 (`whale_entry` 7/27 13:54, `periodic_pulse` 7/27 10:18).

---

## D1~D6 발화 판정 (확정)

| 결함 | 판정 | 근거 |
| --- | --- | --- |
| **D1** 단일 실패점 | **미발화** | 실패 0건. 단 구조 결함은 실재 → 예방 수리 대상 |
| **D2** 사망 감지 부재 | **발화** | 주식 트랙 4일 정지를 아무도 알리지 않음. 잡은 "성공"으로 기록(effective run 아님) |
| **D3** 3트랙 stale 감지 밖 | **발화** | 감지 대상이 `sync_positions` 단독. 주식 4일 정지 무경보 |
| **D4** 백오프 고착 | **미발화** | 전 잡 `current == base` |
| **D5** 선행 WO 5·6 미완 | **발화** | 트랙 생존 라인·재시작 가시화·DB 감시 부재가 그대로 이번 침묵 |
| **D6** 실측 부재 | **발화(청산됨)** | 이 문서로 해소. 단 결론은 반대 — 플래그는 True였다 |

## 이번 침묵의 확정 원인 (2가지, 둘 다 "감시 부재")

1. **주식 트랙 데이터 수집이 7/23 13:33에 멈췄고 4일간 아무도 몰랐다.** 잡은 계속 "성공"으로 기록 — 조기 반환/무수확을 성공과 구분하지 못하는 상태(effective run 미구현 코드 구동 중).
2. **크립토·폴리는 살아있으나 진입이 0이라 텔레그램이 조용했고, "살아있는데 조용한 것"과 "죽어서 조용한 것"을 구분할 신호가 없었다.**

+ **배포 갭**: 이 둘을 고쳤어야 할 관측성 PR이 머지 후 **프로세스에 반영되지 않았다**(재시작 없음). 코드 머지와 실제 구동 사이의 간극을 감시하는 장치도 없었다.
