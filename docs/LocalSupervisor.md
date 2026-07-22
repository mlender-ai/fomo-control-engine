# 로컬 서버 감시자 (Local Supervisor)

로컬 백엔드(8875)·프론트(8876)가 죽어도 자동으로 다시 살아나게 하는 keepalive 루프.

## 왜 launchd 가 아닌가
이 저장소는 `~/Documents` 아래에 있고, macOS TCC(개인정보 보호)가 **launchd 에이전트의
`~/Documents` 접근을 차단**한다(`Operation not permitted`, exit 126). 그래서 launchd 대신
현재 로그인 세션에서 도는 백그라운드 감시 루프를 쓴다 — 이 세션은 파일 접근이 허용돼 있고,
`nohup`+`disown` 으로 터미널을 닫아도 유지된다.

## 사용법
```bash
cd "~/Documents/Fomo club engine"
scripts/local/start-supervisor.sh   # 감시 루프 시작(이미 돌면 무시). 죽은 서버 15초 내 재기동
scripts/local/stop-supervisor.sh    # 감시 루프+서버 완전 종료(포트 8875/8876 기준)
tail -f logs/supervisor.log         # 재시작 이력
```

- `run-backend.sh` / `run-frontend.sh`: 각 서버 실행 래퍼(launchd 최소 PATH 대비 런타임 경로 명시).
- `supervisor.sh`: 15초마다 8875·8876 리스닝 확인, 죽었으면 재기동.
- 프론트 래퍼는 **절대 build 하지 않는다**(AGENTS.md 불변 규칙 4) — 이미 만든 `.next` 를 serve 만.
  빌드는 사람이 서버 중지 후 `cd dashboard && npm run build` 로 별도 수행 후 다시 start.

## 재부팅 후
감시 루프는 로그인 세션 프로세스라 **재부팅 후엔 자동 복구되지 않는다.** 로그인 후 한 번:
```bash
scripts/local/start-supervisor.sh
```
(진짜 부팅 영속이 필요하면 `/bin/bash` 에 Full Disk Access 를 부여하고 launchd 로 전환해야
하는데, 광범위 권한이라 기본값으로 두지 않는다.)

## 서버를 죽이는 실제 원인 (별도 조치 필요)
- `backend/fomo_control_engine.db` 가 **12.8GB** 로 비대하다. 재시작 자동화는 증상 완화일 뿐,
  근본 원인일 가능성이 크다(장기 구동 시 메모리·쿼리 부하 → 크래시). 리텐션/정리(`app/db/maintenance.py`,
  WO-72 백업/리텐션)가 실제로 도는지, 어떤 테이블이 비대한지 진단하는 후속 WO 필요.

## 금지
- 서버 정리에 `pkill -f next-server` 등 **광범위 패턴 kill 금지** — 다른 프로젝트 dev 서버까지
  죽인다(2026-07-23 taro 3200·simulo 3000 오살상 사건). 반드시 포트 기준: `lsof -ti :8876 | xargs kill`.
