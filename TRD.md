# TRD: Customer Embedding App (v3 — 최종본 기준)

| 항목 | 내용 |
|---|---|
| 문서 버전 | v3.0 |
| 작성일 | 2026-09-14 |
| 관련 문서 | `PRD.md`(제품 요구사항), `backend_api_spec.md`(API 상세), `SECURITY.md`, `STOREFRONT_INTEGRATION.md` |

> **v3에서 고친 것**: (1) 그래프 DB 실제 규모 수치가 예전 추정치(노드 ~6,750/관계 ~46,000)로
> 남아있던 것을 **실측치(노드 7,109/관계 30,756)**로 정정. (2) 클러스터링 배치와 그래프 DB
> 적재가 "하나로 이어진 파이프라인"처럼 보이던 §2 구성도를 **서로 독립된 두 파이프라인**으로
> 명확히 분리해서 다시 그림 (PRD.md §6과 동일한 구조).

---

## 1. 기술 스택

| 레이어 | 선택 | 비고 |
|---|---|---|
| 프론트엔드 | Vanilla JS + HTML + CSS | 빌드 단계 없이 단일 파일 배포 |
| 백엔드 | Node.js 18+ / Express 4 | REST API |
| 배치(클러스터링) | Python 3.11 (pandas, scikit-learn, openpyxl) | GitHub Actions에서 무인 실행 |
| 배치 스케줄러 | **GitHub Actions** (cron: 매달 1일) | 결과를 자동 커밋·푸시 |
| 그래프 DB | **Neo4j Aura Free** | 비슷한 고객·추천·코디 쿼리 |
| 그래프 드라이버 | `neo4j-driver` (Node) | 백엔드에서 Cypher 실행 |
| 발송 게이트웨이 | 알리고(카카오 알림톡/SMS), SendGrid(이메일), FCM Legacy(푸시) | 자격증명 없으면 자동 시뮬레이션 |
| 데이터 저장 | 정적 JSON 파일(`data/*.json`) + 스냅샷 히스토리(`data/segment_snapshots/`) | DB 없이 파일 기반 |
| 배포 | Render (Static Site + Web Service) | 각각 독립 배포, GitHub push로 자동 재배포 |

---

## 2. 시스템 구성도

**클러스터링 배치(파이프라인 A)와 그래프 DB 적재(파이프라인 B)는 서로 독립적이다.** 둘 다
같은 xlsx를 입력으로 읽지만, 산출물도 다르고 실행 방식(자동 vs 수동)도 다르다.

```
                    data/customer_embedding_training_data_2000_segmented.xlsx
                                    (공통 입력, 두 파이프라인 모두 읽기 전용)
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
┌────────────────────────────────────────┐   ┌────────────────────────────────────────┐
│ 파이프라인 A — 세그먼트 분류              │   │ 파이프라인 B — 그래프 DB 적재              │
│ 실행 주체: GitHub Actions (매달 1일 자동) │   │ 실행 주체: 사람 (xlsx 변경 시 수동)         │
│                                         │   │                                          │
│ scripts/run_clustering.py               │   │ scripts/export_graph_csvs.py            │
│  1) 세션·이벤트·구매·SNS·프로필 5개 시트 로드│   │  1) 세션·이벤트·상품·동시조회 2개 시트 로드   │
│  2) 이벤트 로그 집계 → PCA 임베딩(6/8/8/8) │   │  2) 노드 CSV 6종 + 관계 CSV 7종 생성        │
│     → Fusion(12) → K-means               │   │     (data/graph/*.csv)                  │
│  3) 클러스터→세그먼트 이름 자동 매핑         │   │  3) GitHub에 커밋·푸시                    │
│     (매출증감률 기준)                      │   │                                          │
│  4) 프로필·지표·oneLiner·캠페인 메시지 생성  │   │ scripts/import_to_neo4j.cypher          │
│  5) data/segment_summary.json 저장        │   │  4) Neo4j Aura Query 화면에서 수동 실행     │
│  6) data/segment_snapshots/{월}.json 저장 │   │     → MERGE로 노드·관계 적재               │
│  7) segment_trends/migration.json 갱신    │   │                                          │
│  8) git commit & push (자동)              │   │                                          │
└─────────────────┬────────────────────────┘   └─────────────────┬────────────────────────┘
                   │                                              │
                   ▼                                              ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ backend/server.js (Express, Render Web Service)                                       │
│  - data/*.json require() 후 REST API로 서빙 (파이프라인 A 결과)                            │
│  - backend/graph.js : Neo4j Aura 조회 (파이프라인 B 결과, 비슷한 고객·추천·코디)              │
│  - backend/gateways/* : 카카오·SMS·이메일·푸시 실제 발송                                    │
└─────────────────────────────────┬──────────────────────────────────────────────────────┘
                                   │ fetch (CORS 허용)
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ frontend/index.html (정적 SPA, Render Static Site)                                     │
│  - state + render() 패턴, 백엔드/그래프 DB 실패 시 최소 placeholder로 폴백                   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**설계 원칙**
- 클러스터링(분석)과 서빙(API)을 완전히 분리한다 — 배치 재실행이 서버 재배포를 요구하지 않는다
  (배치 결과 커밋이 push를 트리거해 자동 재배포되긴 하지만, 서버 코드 자체는 안 바뀐다).
- 파이프라인 A는 "정기적으로 무인 실행"이 목적이라 스케줄러(GitHub Actions)에 올렸고,
  파이프라인 B는 "원본 데이터가 바뀔 때만" 필요해서 의도적으로 수동 실행으로 남겨뒀다
  (Neo4j Aura Free는 비밀번호 분실 시 인스턴스 재생성이 필요해, 완전 자동화보다 사람이
  통제하는 편이 안전하다고 판단).
- 프론트엔드는 백엔드/그래프 DB 부재 시에도 최소 상태로 정상 렌더링된다.

---

## 3. 백엔드 기술 명세

### 3.1 라우팅 구조 (`backend/server.js`)

| 메서드/경로 | 설명 | 데이터 출처 |
|---|---|---|
| `GET /` | 헬스체크 + 엔드포인트 목록 | - |
| `GET /api/v1/segments/summary` | 세그먼트 요약(프로필·지표·메시지 포함) | 파이프라인 A |
| `GET /api/v1/segments/trends` | 월별 추이 (근사+실제 병합됨) | 파이프라인 A |
| `GET /api/v1/segments/migration` | 세그먼트 이동 매트릭스 | 파이프라인 A |
| `GET /api/v1/customers?query=&segment=` | 고객 검색 (대소문자 무시) | 목업(60명, 별도) |
| `GET /api/v1/customers/:id` | 고객 단건 조회 | 목업(60명, 별도) |
| `GET /api/v1/customers/:id/similar` | 비슷한 상품을 조회한 고객 목록 | 파이프라인 B (Neo4j) |
| `GET /api/v1/customers/:id/recommendations` | 협업 필터링 기반 상품 추천 | 파이프라인 B (Neo4j) |
| `GET /api/v1/products/:id/coordination` | 코디 추천 (CO_VIEWED_WITH 기반) | 파이프라인 B (Neo4j) |
| `POST /api/v1/campaigns/send` | 게이트웨이 디스패치 | - |
| `GET /api/v1/campaigns/logs` | 발송 로그 조회 | - |
| `GET /api/v1/gateways/status` | 발송 게이트웨이별 자격증명 설정 여부 | - |
| `GET /api/v1/graph/status` | Neo4j 연결 설정 여부 | - |

> **참고**: 고객 검색(`/customers`, `/customers/:id`)은 아직 실제 2,000명 데이터가 아니라
> 데모용 목업 60명을 쓴다. 그래프 DB(파이프라인 B)는 실제 xlsx 기반 2,000명 전체가 적재되어
> 있어 두 엔드포인트 그룹의 고객 모수가 서로 다르다 — 향후 개선 과제(§11).

### 3.2 데이터 로딩 (방어적 처리)
```js
function loadJsonSafely(filename, fallback) {
  try { return require(path.join(__dirname, "..", "data", filename)); }
  catch (e) { console.error(...); return fallback; }
}
```
`data/*.json`이 없거나 손상되어도 **서버 전체가 기동 실패하지 않도록** 폴백 값을 반환한다.
(과거 `segment_migration.json`이 없어서 서버가 기동조차 못 했던 실제 이슈를 계기로 도입됨 — §11 참고.)
파일은 서버 기동 시 1회 로드되며, 파이프라인 A가 새 파일을 만들어도 **서버 재시작 전까지
반영되지 않는다** (Render는 git push 시 자동 재시작하므로 실무에서는 문제없음).

### 3.3 그래프 연동 (`backend/graph.js`)
```js
module.exports = { findSimilarCustomers, findRecommendedProducts, findCoordinationRecommendations, isConfigured, closeDriver };
```
- `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` 미설정 시 `configured:false`로 즉시 반환 (드라이버 생성 자체를 시도하지 않음)
- 코디 추천은 `VIEWED` 관계를 2홉 순회하는 대신, xlsx의 동시조회 시트를 그대로 옮긴
  `CO_VIEWED_WITH` 관계를 1홉만 조회 — 더 빠르고 원본 데이터와 항상 일치
- 상품 단위 직접 동시조회 기록이 부족하면 같은 카테고리 상품으로 보강(2단계 폴백)

### 3.4 발송 게이트웨이 (`backend/gateways/`)
공통 인터페이스: `send(payload)`, `isConfigured()`, `channel`.
모든 게이트웨이 fetch 호출에 **10초 `AbortController` 타임아웃**이 걸려 있다 (개발 중 타임아웃
누락으로 요청이 무한 대기하던 실제 버그를 수정하며 도입, §11 참고).
세그먼트 전체 발송은 `MAX_SYNC_RECIPIENTS = 20`으로 제한된 동기 반복이며, 대상 고객이
0명이면 `"failed"`를 명시적으로 반환한다(과거엔 빈 배열일 때 `"sent"`로 잘못 표시되던 버그 수정).

### 3.5 에러 처리
- 게이트웨이/그래프 자격증명 미설정 → 예외 아님, `configured:false` 정상 응답
- 그래프 쿼리 실행 자체가 실패 → 500 응답, **클라이언트에는 일반화된 메시지만** 반환하고 상세
  원인은 서버 로그에만 기록 (`SECURITY.md`의 정보 노출 지적을 반영해 수정됨)
- 잘못된 대소문자의 고객ID → 서버가 자동으로 대문자 정규화 후 조회

---

## 4. 프론트엔드 기술 명세

### 4.1 아키텍처 패턴
프레임워크 없이 **단일 `state` 객체 + `render()` 함수** 패턴을 사용한다. 탭은 5개(세그먼트
개요/추이/고객 검색/캠페인 관리/코디 위젯 미리보기)이며, `state.tab` 값에 따라 `render()`가
해당 뷰 함수를 호출한다.

### 4.2 API 연동 및 폴백 원칙
```js
async function apiGetSegmentSummary() {
  try { return await fetchJson("/api/v1/segments/summary"); }
  catch (e) { return mockSegmentSummary(); }
}
```
mock 함수들은 **최소 placeholder**만 반환한다 — 실제 콘텐츠(세그먼트 프로필, 지표, 캠페인
메시지)는 오직 `data/segment_summary.json`(파이프라인 A 산출물)에서만 나오도록 단일 진실
공급원(source of truth)을 강제한다. (초기 버전은 mock에 실제 데이터를 통째로 복사해뒀다가
배치 결과와 어긋나는 문제가 반복됐던 것을 계기로 단순화함, §11 참고.)

### 4.3 세그먼트 추이 차트 (`renderLineChart`)
- x축을 배열 인덱스가 아니라 **"YYYY-MM"을 개월수로 환산한 실제 달력 간격**에 비례하게 그린다.
  데이터가 비어있는 구간(예: 근사 산출 범위와 실제 배치 실행 시점 사이)은 음영 밴드로 표시된다.
- `months.length < 2`일 때는 차트 대신 안내 문구를 표시한다.

### 4.4 반응형/모바일
- `h1`은 `clamp(22px, 6.5vw, 34px)` + `overflow-wrap: break-word`로 좁은 화면에서 긴
  영문 문자열이 뷰포트를 넘기지 않도록 처리 (실제 모바일 가로 스크롤 버그를 수정하며 도입)
- `#root`에 `overflow-x: hidden`을 방어적으로 적용

---

## 5. 데이터 스키마

### 5.1 `data/segment_summary.json` (파이프라인 A 산출물)
```ts
{
  snapshot_date: string;
  source_file: string;
  method: string;
  segments: Array<{
    id: "growth" | "stable" | "dormant";
    n: number; pct: string; growth: string; dir: "up" | "down";
    oneLiner: string;
    profile: [string, string, string][];
    metrics: [string, string][];
    messages: [string, string, string, string | null][];
  }>;
}
```

### 5.2 `data/segment_trends.json` (근사 + 실제 병합본)
```ts
{
  months: string[]; growth: number[]; stable: number[]; dormant: number[];
  new_entrants: number[];
  approximate_until?: string;
  approximate_note?: string;
}
```

### 5.3 `data/segment_snapshots/{YYYY-MM}.json`
```ts
{ [customerId: string]: "growth" | "stable" | "dormant" }
```

### 5.4 `data/graph/*.csv` (파이프라인 B 산출물, Neo4j 적재용)
노드: `nodes_customers.csv`(월매출 12개월 속성 포함, 2,000행), `nodes_sessions.csv`(4,906행),
`nodes_products.csv`(180행), `nodes_categories.csv`(12행), `nodes_devices.csv`(3행),
`nodes_exitevents.csv`(8행)
관계: `rel_has_session.csv`(4,906행), `rel_used_device.csv`(4,906행), `rel_ended_at.csv`
(4,906행), `rel_viewed.csv`(12,228행), `rel_belongs_to.csv`(180행),
`rel_category_cooccurrence.csv`(62행), `rel_product_cooccurrence.csv`(3,568행)

### 5.5 캠페인 발송 로그
```ts
{
  log_id: string; target: object; channel: string; gateway: string | null;
  recipients: number; status: "sent"|"simulated"|"partial"|"failed"|"skipped";
  provider: string; detail: string; sent_at: string;
}
```

> 원본 xlsx 스키마(입력 데이터 계약)는 `PRD.md` §1.1 참고.

---

## 6. 배치 자동화 (파이프라인 A)

### 6.1 `scripts/run_clustering.py`
- 실행 시점(wall-clock)이 아니라 **`DATA_REFERENCE_DATE`(고정값, 2026-07-03)**를 기준으로
  최근성(recency)을 계산한다. 입력 xlsx의 `최근_구매시점`이 이 기준일에 맞춰 생성된 정적
  데이터이므로, 매번 실행 시점을 기준으로 계산하면 데이터의 실제 의미와 어긋나기 때문이다.
- `RUN_MONTH`(스냅샷 파일명)는 반대로 **실제 실행 시점**을 그대로 쓴다.
- `top_n_pct()` 헬퍼로 관심사·스타일·소비성향·카테고리·이탈지점·채널·캠페인유형·해시태그의
  1~3위를 함께 산출한다.
- `build_one_liner()` / `build_campaign_messages()`가 세그먼트별 실제 SNS 채널·캠페인·
  해시태그 데이터를 템플릿에 대입해 한 줄 요약과 캠페인 메시지를 **매달 다시 생성**한다.

### 6.2 `scripts/backfill_trends.py`
과거 실제 스냅샷이 부족한 초기 구간을, xlsx의 12개월 매출 데이터만으로 근사 추정한다.
**오늘 실제 세그먼트 비율을 과거에도 순위 기준으로 동일하게 적용**하는 방식을 쓴다. `run_clustering.py`가 이 결과를 실제 스냅샷과 자동 병합한다.

### 6.3 `scripts/fix_purchase_consistency.py`
xlsx의 '구매이력' 시트에서 발견된 데이터 오류(최근 구매월 이후에도 매출이 남아있는 논리적
모순, 2,000명 중 1,384명·69.2%에서 발생)를 수정하는 1회성 패치 스크립트. **이 수정으로
세그먼트별 매출 증감률이 크게 바뀌었다** (예: 성장형 +20.0%→+17.6%, 안정형 +3.4%→-30.2%,
휴면 -12.9%→-57.5%, 인원수는 286/1,106/608→294/1,106/600) — 데이터 오류를 고친 자연스러운
결과다.

### 6.4 `.github/workflows/recompute-segments.yml`
```yaml
on:
  schedule: [{ cron: "0 18 1 * *" }]
  workflow_dispatch: {}
permissions:
  contents: write
```
저장소의 "Workflow permissions"가 Read and write로 설정되어 있어야 자동 커밋이 성공한다.

---

## 7. 그래프 DB 연동 (파이프라인 B)

### 7.1 스키마
```
(:Customer {customerId, totalAmount12m, salesGrowthPct, revenue_YYYY_MM ×12})
(:Session {sessionId, startTime, endTime, totalEvents, converted})
(:Product {productId, name, brand, price})
(:Category {category})
(:Device {deviceType})  (:ExitEvent {exitEventType})

(:Customer)-[:HAS_SESSION]->(:Session)
(:Session)-[:USED_DEVICE]->(:Device)
(:Session)-[:ENDED_AT]->(:ExitEvent)
(:Session)-[:VIEWED {eventType, eventTime}]->(:Product)
(:Product)-[:BELONGS_TO]->(:Category)
(:Category)-[:CO_VIEWED_WITH {count}]->(:Category)
(:Product)-[:CO_VIEWED_WITH {count}]->(:Product)
```

### 7.2 적재 파이프라인
```
scripts/export_graph_csvs.py  → data/graph/*.csv (GitHub에 커밋, 사람이 실행)
scripts/import_to_neo4j.cypher → Neo4j Aura Query 화면에서 구간별 수동 실행
```
`MERGE` 기반이라 여러 번 실행해도 안전하다. `VIEWED` 관계는 최초 설계 시 `eventType`을
매칭 조건에서 빠뜨려 같은 세션 내 반복 조회가 하나로 합쳐지는 버그가 있었고, 관계 속성까지
매칭 조건에 포함시켜 수정했다 (§11 참고).

### 7.3 실측 규모 (2026-09 기준)
| 구분 | 개수 |
|---|---|
| 노드 합계 | **7,109개** (Customer 2,000 · Session 4,906 · Product 180 · Category 12 · ExitEvent 8 · Device 3) |
| 관계 합계 | **30,756개** (VIEWED 12,228 · HAS_SESSION/USED_DEVICE/ENDED_AT 각 4,906 · CO_VIEWED_WITH(상품) 3,568 · BELONGS_TO 180 · CO_VIEWED_WITH(카테고리) 62) |

Neo4j Aura Free 한도(노드 20만/관계 40만)의 **노드 3.6%, 관계 7.7%**만 사용 중이라 여유가 크다.
(v2 문서의 "노드 ~6,750/관계 ~46,000"은 실제 적재 전 설계 단계의 추정치였다 — 이번에 실측치로 정정했다.)

### 7.4 다른 그래프 프로젝트와의 공존
Neo4j Aura Free는 **계정당 인스턴스 1개**만 허용한다. 같은 계정에 이미 다른 그래프 프로젝트가
있다면, 새 인스턴스를 만드는 대신 **같은 인스턴스에 노드 라벨로 구분해서 함께 적재**하는 것을
권장한다 (라벨/관계 타입이 겹치지 않으면 두 프로젝트가 하나의 그래프 안에서 안전하게 공존 가능).

---

## 8. 배포 아키텍처

| 대상 | Render 서비스 유형 | Root Directory | Build | Start |
|---|---|---|---|---|
| `frontend/` | Static Site | `frontend` | (없음) | Publish Directory `.` |
| `backend/` | Web Service | `backend` | `npm install` | `npm start` |

백엔드 환경변수(Render Environment 탭): `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/
`NEO4J_DATABASE`, `ALIGO_*`, `SENDGRID_*`, `FCM_SERVER_KEY`. 프론트엔드는
`window.CUSTOMER_EMBEDDING_API_BASE`에 백엔드 URL을 하드코딩(빌드 타임 주입 없음 — §11 기술 부채).

---

## 9. 보안 요구사항

`SECURITY.md`에 별도 문서로 정리되어 있으며 요약하면:

| 항목 | 상태 |
|---|---|
| API 인증 | 없음 (Critical) |
| CORS | 전체 허용 (High) — 단, 코디 추천 API는 쇼핑몰 등 외부 도메인 호출이 목적이라 의도적으로 열어둠 |
| Rate limiting | 없음 (High) |
| 에러 상세 노출 | 수정 완료 — 클라이언트엔 일반화된 메시지만 |
| PII 마스킹 | 미적용 (Medium) |
| 입력값 검증 | 부분적 (customerId 대소문자 정규화만 적용) |

---

## 10. 성능 및 확장성

| 이슈 | 현재 한계 | 개선 방향 |
|---|---|---|
| 캠페인 발송 | 동기 처리, 최대 20명 | 큐(SQS/BullMQ) 전환 |
| 세그먼트 데이터 갱신 | 서버 재시작 필요(`require()` 캐시) | Render 자동 재배포로 사실상 해결됨 |
| 그래프 쿼리 | 코디 추천은 1홉 조회로 최적화됨 | 비슷한 고객/추천은 여전히 다홉 순회, 대량 트래픽 시 캐싱 검토 |
| 그래프 DB 적재 | 수동 실행 (파이프라인 B) | 필요성이 확인되면 GitHub Actions로 자동화 검토 |
| 프론트엔드 렌더링 | 전체 `innerHTML` 재생성 | 현재 데이터 규모에서 문제 없음 |

---

## 11. 알려진 기술 부채 / 실제로 겪은 이슈 이력

1. **게이트웨이 fetch 타임아웃 누락** — 초기 구현엔 타임아웃이 없어 네트워크가 막힌 환경에서
   요청이 무한 대기할 뻔했다. 모든 게이트웨이에 10초 `AbortController`를 추가해 해결.
2. **빈 발송 대상이 "성공"으로 표시됨** — 대상 고객이 0명일 때도 상태값이 기본적으로 `"sent"`로
   떨어지던 로직 결함을 발견해 `"failed"`로 명시하도록 수정.
3. **`segment_migration.json` 부재 시 서버 기동 실패** — 스냅샷이 1개뿐인 최초 배포 시점엔
   이 파일이 생성되지 않는데, `server.js`가 무조건 `require()`하다 보니 서버 전체가 죽을
   뻔했다. 배치가 항상 파일을 생성하도록(스냅샷 1개면 항등행렬로) 고치고, 서버도 파일 로드
   실패 시 폴백하도록 이중으로 방어.
4. **`VIEWED` 관계 중복 병합** — Cypher `MERGE`가 `eventType`을 매칭 조건에 포함하지 않아,
   같은 세션에서 같은 상품을 여러 이벤트로 봤을 때 관계가 하나로 뭉개짐(12,228건 중 925건
   손실). 매칭 조건에 이벤트 속성을 포함시켜 해결.
5. **CSV 인코딩(한글 깨짐)** — 최초 CSV 내보내기가 BOM 없는 UTF-8이라 엑셀에서 한글이
   물음표로 깨짐. `utf-8-sig`로 전환.
6. **구매이력 데이터 논리적 모순** — 최근 구매월 이후에도 월매출이 존재하는 모순이 2,000명 중
   1,384명에서 발견됨. 별도 패치 스크립트로 수정 (§6.3). 데이터 정합성 버그가 분석 결과
   자체를 왜곡할 수 있다는 걸 보여주는 사례.
7. **추이 근사치의 부자연스러운 불연속** — 초기엔 "오늘 세그먼트 평균값과 가장 가까운 값"으로
   과거를 근사했더니 오늘의 실제 비율과 크게 달라져 그래프가 뚝 끊기는 모양이 됨. "오늘의
   실제 비율을 순위 기준으로 과거에도 동일 적용"하는 방식으로 교체.
8. **월별 추이 그래프의 날짜 공백 미표시** — 데이터가 없는 달이 인덱스 기준 균등 간격으로
   그려져 공백이 안 보이던 문제. x축을 실제 달력 간격 비례로 변경하고 공백 구간에 음영 표시.
9. **모바일 가로 스크롤** — `h1`이 고정 폰트 크기(34px)라 좁은 화면에서 긴 영문 문자열이
   줄바꿈 없이 넘쳐 페이지 전체가 가로로 밀림. `clamp()` + `overflow-wrap`으로 해결.
10. **프론트엔드 API 주소가 배포 때마다 초기화될 뻔함** — 코드 수정본을 여러 차례 전달하는
    과정에서 로컬 개발용 기본값(`http://localhost:4000`)이 실수로 프로덕션 설정을 덮어쓴 적이
    있었다. 배포 전 `window.CUSTOMER_EMBEDDING_API_BASE` 값 확인을 운영 체크리스트에 추가할
    필요가 있음(§8 기술 부채로 남김).
11. **그래프 DB 규모 문서화 오류** — 실제 적재 전 설계 단계에서 추정한 "노드 ~6,750/관계
    ~46,000"이 문서(v2)에 그대로 남아있었다. 실제 적재 후 재계산한 실측치(노드 7,109/관계
    30,756)로 정정 (§7.3). 추정치와 실측치를 문서에 구분 표기하지 않으면 이런 오류이 나중에
    발견하기 어렵다는 교훈.

## 12. 테스트 현황

**수행함**: 전체 API 라우트 실행 검증, 게이트웨이 타임아웃/에러 처리 검증,
`run_clustering.py`/`backfill_trends.py`/`export_graph_csvs.py`/`fix_purchase_consistency.py`
로컬 실행 및 결과 재검증, Neo4j 실제 인스턴스에 적재 후 카운트 대조 검증(§7.3 실측치의 근거),
프론트엔드 JS 구문 검사, `customer_persona_clustering_colab.ipynb` 전체 셀 재실행 후
`segment_summary.json`과 결과 일치 확인.

**미수행**: 실제 자격증명을 이용한 게이트웨이 end-to-end 발송, 프론트엔드 자동화 테스트,
부하 테스트, 접근성 자동 검사.
