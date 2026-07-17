# iPhone 홈 화면 웹앱

FOMO Control은 iPhone Safari의 **홈 화면에 추가**로 standalone 웹앱처럼 실행할 수 있다. 앱스토어 설치 파일이 아니라 현재 Mac에서 실행되는 read-only 관제 화면에 접속하는 PWA다.

## 안전 경계

- 같은 Wi-Fi 또는 사용자만 접근 가능한 사설망에서만 사용한다.
- 공유기 포트 포워딩과 인증 없는 공용 터널은 금지한다. 포지션과 계좌 정보가 노출될 수 있다.
- API 응답과 판정 데이터는 오프라인 캐시하지 않는다. 연결이 끊기면 오래된 값을 앱 데이터처럼 보여주지 않는다.
- Mac의 백엔드와 프론트가 실행 중이어야 최신 데이터가 보인다.

## Mac에서 실행

백엔드는 기존처럼 localhost에만 둔다.

```bash
cd backend
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875
```

프론트는 빌드 후 모바일 바인딩으로 시작한다. 브라우저의 `/api/*` 요청은 Next 서버가 localhost 백엔드로 전달한다.

```bash
cd dashboard
npm run build
npm run start:mobile
```

Mac의 Wi-Fi 주소가 `192.168.219.101`이면 iPhone Safari에서 `http://192.168.219.101:8876`을 연다. 주소는 네트워크가 바뀌면 달라질 수 있다.

## iPhone에 추가

1. Mac과 iPhone을 같은 Wi-Fi에 연결한다.
2. iPhone Safari에서 Mac의 모바일 주소를 연다.
3. 공유 버튼을 누르고 **홈 화면에 추가**를 선택한다.
4. **웹 앱으로 열기**를 켜고 추가한다.

아이콘 이름은 `FOMO Control`이며 standalone 화면으로 열린다. HTTP 사설망에서는 브라우저별 설치 판정과 일부 PWA 기능이 제한될 수 있다. 완전한 HTTPS 설치가 필요하면 공개 터널 대신 접근 제어된 사설망 TLS를 사용한다.

## 연결 점검

Mac에서 다음 두 주소가 모두 응답해야 한다.

```bash
curl http://127.0.0.1:8876/manifest.webmanifest
curl http://127.0.0.1:8876/api/system/status
npm --prefix dashboard run check:pwa
```

휴대폰에서 화면은 열리지만 데이터가 비어 있으면 프론트가 이전 빌드인지 확인하고 `docs/FrontendBuildSafety.md` 절차로 재빌드한다.
