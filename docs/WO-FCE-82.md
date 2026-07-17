# WO-FCE-82 — iPhone 홈 화면 PWA와 사설망 접속

우선순위: P1 (모바일 관제 접근성 개선, 거래 로직 변경 없음)
선행: 없음

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-82" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- PWA manifest, 홈 화면 아이콘, Apple standalone metadata가 없다.
- 브라우저 API 기본 주소가 `127.0.0.1:8875`라 휴대폰에서는 휴대폰 자신의 loopback을 조회한다.
- 프론트 서버가 `127.0.0.1`에만 바인딩되어 같은 Wi-Fi의 휴대폰에서 접근할 수 없다.

## 작업
### 1. PWA 설치 표면
- Next metadata manifest와 192/512px maskable 아이콘, 180px Apple 아이콘을 제공한다.
- iPhone safe-area와 standalone 상태 표시줄 메타데이터를 적용한다.

### 2. 모바일 데이터 연결
- 브라우저는 기본적으로 상대 경로 `/api`를 사용한다.
- Next 서버가 `/api/*`를 localhost FastAPI로 프록시해 휴대폰에는 프론트 포트만 노출한다.
- `start:mobile`은 `0.0.0.0:8876`에 바인딩한다.

### 3. 보안 경계
- 포트 포워딩이나 무인증 공개 터널은 금지한다.
- 실시간 금융 데이터를 오프라인 캐시하지 않는다.
- 설치와 접속 절차는 `docs/PWA.md`를 정본으로 한다.

## 수용 기준
- [x] manifest가 standalone, 192/512px 아이콘을 제공
- [x] Apple 홈 화면 아이콘과 safe-area 적용
- [x] 프론트 동일 출처 `/api/system/status` 응답 성공
- [x] 390px 모바일 뷰포트 가로 넘침 없음
- [x] HARNESS 프론트·E2E 게이트 통과

## 금지
- 인증 없는 공용 인터넷 노출 금지
- API 응답·포지션·판정 데이터 오프라인 캐시 금지
- 실주문 기능 추가 금지

## 문서
- `docs/PWA.md`
- `README.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
