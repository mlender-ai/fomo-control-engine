# 로컬 프론트 긴급 폴백

`127.0.0.1:8876`이 `ERR_CONNECTION_REFUSED`이거나 Next 산출물 세대가 갈려 화면이
깨졌을 때 아래 한 명령으로 복구한다.

```bash
cd dashboard
npm run recover:local
```

명령은 다음 순서를 하나의 실패-폐쇄(fail-closed) 절차로 강제한다.

1. 8876 리스너가 FCE의 `next start`인지 확인한다. 다른 프로세스면 종료하지 않는다.
2. 기존 FCE 프론트를 `SIGTERM`으로 종료하고 포트가 닫힐 때까지 기다린다.
3. `npm run build`로 `.next`를 새로 만든다.
4. `npm run start:local`을 백그라운드로 시작한다.
5. `/` 응답을 기다린 뒤 `npm run check:local-assets`로 제품 라우트와 CSS/JS를 검사한다.
6. 자산 검사가 실패하면 새 서버도 종료하고 실패로 끝낸다.

로그는 `/tmp/fce-dashboard.log`에 기록된다. 이 폴백은 백엔드를 시작하거나 실주문
경로를 건드리지 않는다. API가 죽은 경우에는 별도로 다음을 실행한다.

```bash
cd backend
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875
```

백엔드 lifespan 안에서 스케줄러와 Telegram polling도 함께 시작된다. `/scout` 명령이
무응답이면 프론트 상태와 별개로 `GET /health`와 `GET /api/system/worker`를 먼저 확인한다.
`8875`가 닫혀 있으면 관심목록 데이터는 SQLite에 남아 있어도 새 Telegram 업데이트는
처리되지 않는다.

정상 배포 절차와 빌드 금지는 [`FrontendBuildSafety.md`](FrontendBuildSafety.md)를
정본으로 유지한다.
