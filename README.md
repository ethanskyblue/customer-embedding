# Customer Embedding App

```
customer-embedding/
├── .github/workflows/
│   └── recompute-segments.yml  ← 매달 1일 자동으로 클러스터링을 재실행하는 GitHub Actions
├── scripts/
│   ├── run_clustering.py       ← 노트북의 임베딩·클러스터링 로직을 무인 실행용으로 옮긴 배치 스크립트
│   ├── backfill_trends.py      ← 실제 스냅샷이 쌓이기 전, 과거 매출 데이터로 추이를 근사 채우는 스크립트
│   ├── export_graph_csvs.py    ← xlsx(세션·이벤트·동시조회 시트 포함)에서 Neo4j 적재용 CSV를 뽑는 스크립트
│   ├── import_to_neo4j.cypher  ← 위 CSV를 Neo4j Aura에 적재하는 Cypher 스크립트
│   └── requirements.txt
├── data/
│   ├── customer_embedding_training_data_2000_segmented.xlsx   ← 원본 학습용 데이터 (합성 데이터)
│   ├── segment_summary.json     ← 배치 스크립트의 산출물 (백엔드가 이걸 읽음)
│   ├── segment_trends.json
│   ├── segment_migration.json
│   └── segment_snapshots/       ← 매달 실행 시점의 고객→세그먼트 매핑 스냅샷 (trends/migration 계산용 히스토리)
├── frontend/
│   └── index.html      ← 정적 프론트엔드 (Render Static Site로 배포)
└── backend/
    ├── server.js        ← API 서버 (Render Web Service로 배포) — data/*.json을 읽어서 응답
    ├── gateways/         ← 카카오 알림톡/SMS/이메일/푸시 발송 어댑터
    └── package.json
```

**데이터 흐름 (자동화됨)**: `server.js`는 숫자를 코드에 하드코딩하지 않고 `data/segment_*.json`을 읽어서 응답합니다.
이 JSON들은 **매달 1일 GitHub Actions(`recompute-segments.yml`)가 `scripts/run_clustering.py`를 자동 실행**해서
새로 계산하고, 결과를 저장소에 자동 커밋·푸시합니다. 그 push가 Render의 자동 배포를 트리거해 앱에 반영됩니다.

```
GitHub Actions(매달 1일) → scripts/run_clustering.py 실행
  → data/segment_*.json 갱신 + data/segment_snapshots/{YYYY-MM}.json 저장
  → 자동 git commit & push → Render 자동 재배포
```

- **수동 실행**: GitHub 저장소 → Actions 탭 → "Recompute customer segments" → "Run workflow" 버튼으로 즉시 실행 가능
- **세그먼트 이름 자동 매핑**: 클러스터 3개 중 매출_증감률_pct 평균이 가장 높은 클러스터를 `growth`, 가장 낮은 클러스터를 `dormant`로 자동 매핑합니다 (k가 3이 아니게 나오면 `segment_1`, `segment_2`... 형식으로 대체)
- **trends/migration 히스토리**: `data/segment_snapshots/`에 매달 실행 시점의 고객ID→세그먼트 매핑이 쌓이고, 이걸 비교해서 추이·이동 매트릭스를 계산합니다. 스냅샷이 1개뿐인 첫 실행에서는 이동 매트릭스가 항등행렬(전원 유지)로 채워집니다.
- `data/customer_embedding_training_data_2000_segmented.xlsx`는 이 배치의 원본 입력 데이터입니다. 이 파일이 실제 운영 데이터로 교체되면(§ERP 연동 참고) 배치는 코드 변경 없이 새 데이터로 그대로 재계산합니다.

## 세그먼트 추이의 "근사치" 구간에 대하여

앱 실행 초기에는 실제 월별 스냅샷이 1개뿐이라 추이 그래프가 비어 보입니다. 이를 보완하기 위해
`scripts/backfill_trends.py`가 '구매이력' 시트의 **과거 12개월 매출 데이터**를 이용해
"이 시점에 어느 세그먼트에 가까웠을지"를 근사로 채웁니다 (`data/segment_trends_backfill.json`).

**한계**: 세그먼트는 세션·이벤트·SNS·인구통계 4개 데이터를 합쳐 클러스터링한 결과인데,
과거 시점의 세션·이벤트·SNS 로그는 데이터 자체가 없어서 과거 세그먼트를 정확히 재현할 수 없습니다.
이 근사치는 매출 증감률 순위만으로 "오늘의 세그먼트 비율을 과거에도 똑같이 적용했다면"을 추정한 것이며,
프론트엔드 추이 화면에 "근사 추정치" 안내 배너로 항상 명시됩니다. **`run_clustering.py`가 매달 실제
스냅샷을 쌓을 때마다, 겹치는 달은 자동으로 근사치 대신 실제 값으로 교체됩니다** — 12개월이 지나면
근사 구간은 전부 실제 값으로 자연스럽게 대체됩니다.

재실행 방법 (xlsx의 매출 데이터가 바뀌었을 때):
```bash
python scripts/backfill_trends.py   # data/segment_trends_backfill.json 갱신
python scripts/run_clustering.py    # segment_trends.json에 병합 반영
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

`frontend/index.html`의 `window.CUSTOMER_EMBEDDING_API_BASE`가 기본으로 `http://localhost:4000`을 가리키고 있어서
로컬에서 백엔드를 켜둔 상태라면 바로 실제 API 데이터가 표시됩니다.
백엔드를 안 켜도 화면은 자동으로 목업 데이터로 대체되어 정상 동작합니다.

## Render 배포 (2단계)

### 1) 백엔드 배포 (Web Service)
1. 이 저장소를 GitHub `customer-embedding` 저장소에 푸시
2. Render 대시보드 → **New +** → **Web Service**
3. 저장소 연결 후 설정:
   - **Root Directory**: `backend`
   - **Build Command**: `npm install`
   - **Start Command**: `npm start`
4. 배포 완료 후 나오는 URL을 기록 (예: `https://customer-embedding-api-xxxx.onrender.com`)

### 2) 프론트엔드 배포 (Static Site)
1. `frontend/index.html` 안의 `window.CUSTOMER_EMBEDDING_API_BASE` 값을 위에서 받은 백엔드 URL로 수정 후 다시 커밋/푸시
   ```js
   window.CUSTOMER_EMBEDDING_API_BASE = "https://customer-embedding-api-xxxx.onrender.com";
   ```
2. Render 대시보드 → **New +** → **Static Site**
3. 같은 저장소 연결 후 설정:
   - **Root Directory**: `frontend`
   - **Build Command**: (비워두기)
   - **Publish Directory**: `.`
4. 배포 완료 → `https://프로젝트명.onrender.com`으로 접속하면 실제 백엔드와 연동된 앱이 뜹니다.

## 그래프 DB 연동 (고객 검색 탭의 "비슷한 고객 보기")

`backend/graph.js`가 Neo4j 그래프 DB(별도로 만드신 Customer-Session-Product-Category 그래프)에 접속해,
특정 고객과 같은 상품을 조회한 다른 고객을 찾아줍니다.

**그래프 데이터를 처음 만들거나 xlsx가 갱신됐을 때 다시 적재하는 방법**
```bash
python scripts/export_graph_csvs.py   # xlsx -> data/graph/*.csv 생성
git add data/graph/ && git commit -m "Update graph CSVs" && git push
```
그다음 GitHub에 올라간 CSV 경로를 참조하는 `scripts/import_to_neo4j.cypher`를 Neo4j Aura Query 화면에
구간별로 붙여넣어 실행합니다 (저장소 계정/이름이 다르면 파일 안의 GitHub raw URL만 바꿔주면 됩니다).

이 CSV들에는 '구매이력' 시트의 월매출 12개월치·매출 증감률이 Customer 노드 속성으로,
'카테고리_동시조회_매트릭스'·'상품_동시조회_TOP쌍' 시트가 `CO_VIEWED_WITH` 관계로 그대로 반영됩니다 —
`backend/graph.js`의 코디 추천 API(`/api/v1/products/:id/coordination`)가 바로 이 관계를 씁니다
(예전에는 이벤트 로그에서 매번 재계산했는데, 이제는 xlsx 시트 값을 그대로 신뢰하는 방식으로 바뀌어
더 빠르고 xlsx와 항상 일치합니다).

**설정 방법**
```bash
cd backend
# .env에 아래 값 추가 (Aura 콘솔의 인스턴스 상세 화면에서 확인)
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=발급받은비밀번호
```
설정하지 않으면 "고객 검색" 탭의 **비슷한 고객** 버튼을 눌렀을 때 "그래프 DB가 연결되지 않았다"는 안내만 뜨고,
나머지 기능(세그먼트 조회, 캠페인 발송 등)은 그대로 정상 동작합니다 — 다른 게이트웨이들과 동일한 폴백 원칙입니다.

**엔드포인트**
- `GET /api/v1/customers/:id/similar` — 지정한 고객과 겹치는 상품을 많이 조회한 고객 목록
- `GET /api/v1/graph/status` — Neo4j 연결 설정 여부 확인



`backend/gateways/`에 채널별 발송 코드가 들어있습니다.

| 파일 | 채널 | 대행사/제공자 |
|---|---|---|
| `kakaoAlimtalk.js` | 카카오 알림톡 | 알리고 (다른 대행사 쓰면 이 파일만 교체) |
| `sms.js` | SMS/LMS | 알리고 |
| `email.js` | 이메일 | SendGrid |
| `push.js` | 앱 푸시 | FCM (세그먼트 단위 토픽 발송) |

**설정 방법**
```bash
cd backend
cp .env.example .env
# .env를 열어 실제 발급받은 키 값을 채워넣으세요.
```
`.env`를 채우지 않으면 앱은 정상 동작하되, 캠페인 발송이 실제로 나가지 않고 `"simulated"` 상태로 처리됩니다.
Render Web Service에 배포할 때는 `.env` 파일 대신 Render 대시보드의 **Environment** 탭에 동일한 키를 등록하세요.

**Instagram/YouTube/TikTok 같은 광고 채널은 이 게이트웨이 대상이 아닙니다.** 개별 API로 1:1 발송하는 방식이
아니라 광고 관리자(Meta/TikTok Ads Manager)에서 캠페인으로 집행해야 하는 채널이라, 프론트엔드에서
"발송" 대신 "광고 관리자 안내" 버튼으로 표시되고, 백엔드는 `status: "skipped"`로 응답합니다.

**주의**: 지금 구현은 세그먼트 전체(수백~수천 명)에게 보낼 때도 HTTP 요청 안에서 동기적으로 반복 호출합니다
(데모 목적으로 최대 20명까지만 실제 호출). 실서비스에서는 요청을 큐(SQS/RabbitMQ/BullMQ 등)에 넣고
워커가 비동기로 순차 발송하도록 바꿔야 합니다 — 그렇지 않으면 세그먼트가 크면 요청이 타임아웃납니다.

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

## 주의사항 / TODO

- `backend/server.js`의 데이터는 하드코딩된 시드 값입니다. 실제 서비스에서는 `SEED_*` 부분을
  DB 조회 쿼리로 교체하세요 (스키마는 `backend_api_spec.md` 참고).
- `POST /api/v1/campaigns/send`는 이제 `backend/gateways/`를 통해 실제 카카오/SMS/이메일/푸시 발송을 시도합니다
  (자격증명 미설정 시 자동 시뮬레이션). 세그먼트 규모가 커지면 동기 반복 대신 큐 기반 비동기 처리로 바꾸세요.
- Render 무료 플랜의 Web Service는 트래픽이 없으면 슬립 모드로 전환되어
  첫 요청 시 응답이 몇 초 느릴 수 있습니다 (콜드 스타트).
- 캠페인 발송 이력은 브라우저의 `localStorage`에 저장되므로 기기/브라우저마다 따로 쌓입니다.
  여러 사용자가 함께 보는 화면이 필요하면 발송 이력도 백엔드 DB에 저장하도록 바꿔야 합니다
  (`GET /api/v1/campaigns/logs`는 이미 구현되어 있으니 프론트엔드에서 이 엔드포인트를 쓰도록 바꾸면 됩니다).
