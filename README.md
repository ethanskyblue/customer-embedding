# 고객 세그먼트 리텐션 앱

```
retention-app/
├── data/
│   ├── customer_embedding_training_data_2000_segmented.xlsx   ← 원본 학습용 데이터 (합성 데이터)
│   ├── segment_summary.json     ← Python 클러스터링 배치 잡의 산출물 (백엔드가 이걸 읽음)
│   ├── segment_trends.json
│   └── segment_migration.json
├── frontend/
│   └── index.html      ← 정적 프론트엔드 (Render Static Site로 배포)
└── backend/
    ├── server.js        ← API 서버 (Render Web Service로 배포) — data/*.json을 읽어서 응답
    └── package.json
```

**데이터 흐름**: `server.js`는 더 이상 숫자를 코드에 하드코딩하지 않고, `data/segment_*.json`을 읽어서 응답합니다.
이 JSON들은 지금은 손으로 넣어둔 값이지만, 실제로는 **Python 클러스터링 배치 잡(별도 노트북/스크립트)이 주기적으로
계산해서 이 파일들을 갱신하거나, DB 테이블에 적재하는 방식**으로 바뀌는 것을 전제로 설계했습니다.
`data/customer_embedding_training_data_2000_segmented.xlsx`는 그 배치 잡의 원본 입력 데이터입니다.

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

## ERP 연동 (실제 데이터로 전환하기)

나중에 ERP에 구매 이력·고객 마스터 데이터가 있다면 연결할 수 있습니다. 다만 ERP 하나로 전체 파이프라인이
채워지지는 않는다는 점을 먼저 이해하시면 계획을 세우기 쉽습니다.

| 지금 쓰는 4개 데이터 소스 | 실제로는 어디서 옴 |
|---|---|
| 구매 이력, 고객 프로필(인구통계) | **ERP / 자체 커머스 DB** (가능성 높음) |
| 웹/앱 세션·이벤트 로그 | 웹/앱 분석 툴 (GA4, Amplitude, 자체 로그 수집기 등) — ERP에는 보통 없음 |
| SNS 광고 반응 | 광고 플랫폼 API (Meta/TikTok/Google Ads 등) — ERP에는 보통 없음 |

즉 "ERP 연결"만으로는 구매 이력 쪽만 실 데이터가 되고, 나머지 3개는 각자 다른 시스템과 별도로 연동해야
지금 페르소나 수준의 입체적인 분석이 유지됩니다. 아키텍처는 다음과 같이 확장하면 됩니다.

```
ERP(구매/고객)  ─┐
GA4/Amplitude   ─┼──▶  ETL(일 배치) ──▶ 분석 DB(웨어하우스) ──▶ Python 클러스터링 잡 ──▶ data/segment_*.json (or DB)
Ads API         ─┘                                                                        │
                                                                                            ▼
                                                                                   backend/server.js ──▶ 프론트엔드
```

**권장 사항**
- ERP의 실거래(운영) DB에 분석 쿼리를 직접 붙이지 마세요. 운영 DB 부하/보안 문제로, 보통 **야간 배치로 별도 분석 DB에 복제**한 뒤 그 복제본에 붙입니다.
- ERP마다(SAP, 더존, Oracle NetSuite 등) 연동 방식이 다릅니다 — DB 직접 연결이 가능한 경우도 있고, REST/SOAP API로만 여는 경우도 있습니다. 사용 중인 ERP의 연동 옵션부터 확인이 필요합니다.
- 실제 고객 데이터가 들어오는 순간 개인정보 취급 이슈가 생기므로, 접근 권한·마스킹·보관기한 등 사내 데이터 거버넌스 정책 검토가 선행되어야 합니다.
- 클러스터링 로직 자체(PCA + K-means)는 바꿀 필요 없이 재사용 가능합니다 — 입력 데이터 소스만 늘어나는 구조입니다.

- `backend/server.js`의 데이터는 하드코딩된 시드 값입니다. 실제 서비스에서는 `SEED_*` 부분을
  DB 조회 쿼리로 교체하세요 (스키마는 `backend_api_spec.md` 참고).
- `POST /api/v1/campaigns/send`는 실제 카카오/이메일/SMS 발송을 하지 않습니다.
  `server.js`의 `TODO` 주석 부분에 실제 발송 게이트웨이 연동 코드를 추가해야 합니다.
- Render 무료 플랜의 Web Service는 트래픽이 없으면 슬립 모드로 전환되어
  첫 요청 시 응답이 몇 초 느릴 수 있습니다 (콜드 스타트).
- 캠페인 발송 이력은 브라우저의 `localStorage`에 저장되므로 기기/브라우저마다 따로 쌓입니다.
  여러 사용자가 함께 보는 화면이 필요하면 발송 이력도 백엔드 DB에 저장하도록 바꿔야 합니다
  (`GET /api/v1/campaigns/logs`는 이미 구현되어 있으니 프론트엔드에서 이 엔드포인트를 쓰도록 바꾸면 됩니다).
