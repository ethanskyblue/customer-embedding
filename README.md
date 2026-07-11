# 고객 세그먼트 리텐션 앱

```
retention-app/
├── frontend/
│   └── index.html      ← 정적 프론트엔드 (Render Static Site로 배포)
└── backend/
    ├── server.js        ← API 서버 (Render Web Service로 배포)
    └── package.json
```

## 로컬에서 실행해보기

```bash
# 1) 백엔드 실행
cd backend
npm install
npm start                 # http://localhost:4000 에서 API 서버 실행

# 2) 프론트엔드 실행 (새 터미널)
cd frontend
python3 -m http.server 8000   # 또는 VSCode Live Server 등
# 브라우저에서 http://localhost:8000 접속
```

`frontend/index.html`의 `window.RETENTION_API_BASE`가 기본으로 `http://localhost:4000`을 가리키고 있어서
로컬에서 백엔드를 켜둔 상태라면 바로 실제 API 데이터가 표시됩니다.
백엔드를 안 켜도 화면은 자동으로 목업 데이터로 대체되어 정상 동작합니다.

## Render 배포 (2단계)

### 1) 백엔드 배포 (Web Service)
1. 이 저장소를 GitHub에 푸시
2. Render 대시보드 → **New +** → **Web Service**
3. 저장소 연결 후 설정:
   - **Root Directory**: `backend`
   - **Build Command**: `npm install`
   - **Start Command**: `npm start`
4. 배포 완료 후 나오는 URL을 기록 (예: `https://retention-api-xxxx.onrender.com`)

### 2) 프론트엔드 배포 (Static Site)
1. `frontend/index.html` 안의 `window.RETENTION_API_BASE` 값을 위에서 받은 백엔드 URL로 수정 후 다시 커밋/푸시
   ```js
   window.RETENTION_API_BASE = "https://retention-api-xxxx.onrender.com";
   ```
2. Render 대시보드 → **New +** → **Static Site**
3. 같은 저장소 연결 후 설정:
   - **Root Directory**: `frontend`
   - **Build Command**: (비워두기)
   - **Publish Directory**: `.`
4. 배포 완료 → `https://프로젝트명.onrender.com`으로 접속하면 실제 백엔드와 연동된 앱이 뜹니다.

## 주의사항 / TODO

- `backend/server.js`의 데이터는 하드코딩된 시드 값입니다. 실제 서비스에서는 `SEED_*` 부분을
  DB 조회 쿼리로 교체하세요 (스키마는 `backend_api_spec.md` 참고).
- `POST /api/v1/campaigns/send`는 실제 카카오/이메일/SMS 발송을 하지 않습니다.
  `server.js`의 `TODO` 주석 부분에 실제 발송 게이트웨이 연동 코드를 추가해야 합니다.
- Render 무료 플랜의 Web Service는 트래픽이 없으면 슬립 모드로 전환되어
  첫 요청 시 응답이 몇 초 느릴 수 있습니다 (콜드 스타트).
- 캠페인 발송 이력은 브라우저의 `localStorage`에 저장되므로 기기/브라우저마다 따로 쌓입니다.
  여러 사용자가 함께 보는 화면이 필요하면 발송 이력도 백엔드 DB에 저장하도록 바꿔야 합니다
  (`GET /api/v1/campaigns/logs`는 이미 구현되어 있으니 프론트엔드에서 이 엔드포인트를 쓰도록 바꾸면 됩니다).
