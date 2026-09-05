# TRD: Customer Embedding App

| 항목 | 내용 |
|---|---|
| 문서 버전 | v1.0 |
| 작성일 | 2026-07-03 |
| 상태 | Draft (프로토타입 단계) |
| 관련 문서 | `PRD.md`(제품 요구사항), `backend_api_spec.md`(API 상세 명세) |

이 문서는 `PRD.md`에서 정의한 요구사항을 **어떻게 구현했는지/구현할지**를 다룬다. 기능 요구사항은 PRD를, API 요청/응답 필드는 `backend_api_spec.md`를 참조하고, 여기서는 기술 스택·아키텍처·데이터 스키마·비기능 구현·배포·테스트를 다룬다.

---

## 1. 기술 스택

| 레이어 | 선택 | 비고 |
|---|---|---|
| 프론트엔드 | Vanilla JS + HTML + CSS (프레임워크 없음) | 빌드 단계 없이 정적 파일 하나로 배포 가능해야 해서 React 등 번들러 기반 프레임워크를 배제함 |
| 백엔드 | Node.js 18+ / Express 4 | 팀 내 JS 통일성, 가벼운 REST API에 충분 |
| 클러스터링 | Python (PCA, scikit-learn K-means) | 백엔드와 분리된 배치 잡. Node에 이식하지 않음 (§3.3 참조) |
| 데이터 저장(현재) | 정적 JSON 파일 (`data/*.json`) | 배치 잡의 산출물을 파일로 받는 방식. DB 아님 |
| 데이터 저장(계획) | PostgreSQL 등 RDB | §7 데이터 스키마의 테이블 정의 참고 |
| 발송 게이트웨이 | 알리고(카카오 알림톡/SMS), SendGrid(이메일), FCM Legacy(푸시) | 국내 채널 우선, 각 어댑터는 `backend/gateways/*.js`로 격리 |
| 배포 | Render (Static Site + Web Service) | 프론트/백엔드 독립 배포, 무료 티어 기준 콜드 스타트 존재 |
| 버전 관리 | Git / GitHub | 단일 모노레포 (`frontend/`, `backend/`, `data/`) |

---

## 2. 시스템 구성도

```
┌─────────────────────┐
│ Python 클러스터링    │  PCA 임베딩 + Dense Fusion + K-means(k=3)
│ 배치 잡 (Colab/노트북)│  실루엣 0.119 / ARI 0.80 (합성 데이터 기준)
└──────────┬───────────┘
           │ 결과 산출 (수동 또는 스케줄러)
           ▼
data/segment_summary.json, segment_trends.json, segment_migration.json
data/customer_embedding_training_data_2000_segmented.xlsx (원본 입력)
           │
           ▼
┌─────────────────────┐        ┌──────────────────────────┐
│ backend/server.js    │──────▶│ backend/gateways/*.js     │──▶ 알리고 / SendGrid / FCM
│ (Express REST API)   │        │ kakao·sms·email·push 어댑터│
└──────────┬───────────┘        └──────────────────────────┘
           │ JSON REST (CORS 허용)
           ▼
┌─────────────────────┐
│ frontend/index.html  │  상태 객체(state) + render() 패턴의 순수 JS SPA
│ (Render Static Site) │  fetch 실패 시 목업 데이터 자동 폴백
└──────────────────────┘
```

**설계 원칙**
- 클러스터링(분석)과 서빙(API)을 분리한다 — Python 재학습이 Node 서버 재배포를 요구하지 않는다.
- 프론트엔드는 백엔드 부재 시에도 목업 데이터로 항상 렌더링된다 (데모/장애 내성).
- 발송 게이트웨이는 어댑터 패턴으로 격리해, 대행사 교체 시 해당 파일만 수정하면 된다.

---

## 3. 백엔드 기술 명세

### 3.1 라우팅 구조 (`backend/server.js`)
| 메서드/경로 | 설명 |
|---|---|
| `GET /` | 헬스체크 + 엔드포인트 목록 |
| `GET /api/v1/segments/summary` | `data/segment_summary.json` 그대로 반환 |
| `GET /api/v1/segments/trends` | `data/segment_trends.json` 그대로 반환 |
| `GET /api/v1/segments/migration` | `data/segment_migration.json` 그대로 반환 |
| `GET /api/v1/customers?query=&segment=` | 목업 60명 중 조건 필터 (부분일치 검색) |
| `GET /api/v1/customers/:id` | 단건 조회, 404 처리 포함 |
| `POST /api/v1/campaigns/send` | 게이트웨이 디스패치 (§3.3) |
| `GET /api/v1/campaigns/logs?limit=` | 인메모리 발송 로그 조회 |
| `GET /api/v1/gateways/status` | 게이트웨이별 자격증명 설정 여부 |

### 3.2 데이터 로딩
- `data/*.json`은 `require()`로 서버 기동 시 1회 로드 (인메모리 상수). 파일이 바뀌어도 **서버 재시작 전까지 반영 안 됨** — 배치 갱신 주기와 서버 재배포 주기를 맞추거나, 이후 DB 폴링/캐시 무효화 방식으로 전환 필요.
- 고객 목업 60명은 서버 기동 시 `Math.random()` 기반으로 매번 다르게 생성됨 → **재시작할 때마다 고객 검색 결과가 바뀐다.** 실 데이터 전환 시 DB 조회로 대체되면 해결됨.

### 3.3 발송 게이트웨이 (`backend/gateways/`)
공통 인터페이스:
```js
// 모든 게이트웨이 모듈은 아래 형태를 따른다
module.exports = {
  send(payload) => Promise<{status, provider, providerMessageId?, detail}>,
  isConfigured() => boolean,
  channel: string,
};
```
| 모듈 | 프로토콜 | 필요 env | 타임아웃 |
|---|---|---|---|
| `kakaoAlimtalk.js` | 알리고 AlimTalk (`x-www-form-urlencoded` POST) | `ALIGO_API_KEY/USER_ID/SENDER_KEY/SENDER_PHONE` | 10s (AbortController) |
| `sms.js` | 알리고 SMS | `ALIGO_API_KEY/USER_ID/SENDER_PHONE` | 10s |
| `email.js` | SendGrid v3 (`application/json` POST) | `SENDGRID_API_KEY/FROM_EMAIL` | 10s |
| `push.js` | FCM Legacy HTTP (`/topics/segment_{id}` 토픽 발송) | `FCM_SERVER_KEY` | 10s |

`gateways/index.js`가 `channel` → 모듈 매핑을 담당. 등록되지 않은 채널(Instagram 등 광고 플랫폼)은 `status: "skipped"`로 명시적으로 구분해 응답한다.

**동시성 제약**: 세그먼트 전체 발송 시 `MAX_SYNC_RECIPIENTS = 20`으로 제한된 동기 반복문으로 처리한다. 세그먼트 규모(수백~수천 명)를 감당하려면 **큐 기반 비동기 처리(SQS/BullMQ 등)로 전환이 필수**이며, 현재 구현은 참고용 동기 버전이다.

**타임아웃 정책**: 모든 게이트웨이 fetch 호출에 `AbortController` 기반 10초 타임아웃을 건다. (개발 중 타임아웃 미설정으로 인한 요청 행(hang) 이슈를 실제로 발견해 수정한 이력 있음 — §11 참고.)

### 3.4 에러 처리
- 게이트웨이 자격증명 미설정 → 예외 아님, `status: "simulated"` 정상 응답
- 게이트웨이 API 실패/타임아웃 → `status: "failed"`, `detail`에 원인 텍스트 포함
- 등록되지 않은 채널 → `status: "skipped"`
- 필수 파라미터 누락(`target`, `channel`, `message`) → `400 invalid_request`
- 존재하지 않는 고객 ID 조회 → `404 not_found`

### 3.5 환경변수
`.env.example` 참고. 미설정 시 해당 게이트웨이만 시뮬레이션 모드로 동작하고 서버 기동에는 영향 없음(런타임 검사, 부팅 시 검증 없음 — 이후 부팅 시 헬스체크에 게이트웨이 상태 포함 검토 가능).

---

## 4. 프론트엔드 기술 명세

### 4.1 아키텍처 패턴
프레임워크 없이 **단일 `state` 객체 + `render()` 함수** 패턴을 사용한다 (React의 단방향 데이터 흐름을 手동 구현). 모든 상태 변경 함수(`setTab`, `doSearch`, `confirmSend` 등)는 `state`를 갱신한 뒤 `render()`를 호출해 `#root`의 `innerHTML`을 전체 재생성한다.

- 장점: 의존성 0, 빌드 단계 불필요, 파일 하나로 배포
- 트레이드오프: DOM 전체 재생성 방식이라 입력 포커스 유지 등 세밀한 UX는 제한적 (검색창은 `oninput`으로 즉시 재렌더링하되 값 보존 로직 포함)

### 4.2 API 연동 전략
```js
const API_BASE = window.CUSTOMER_EMBEDDING_API_BASE || "http://localhost:4000";
async function fetchJson(path, options) { ... }  // 실패 시 throw

// 각 apiXxx 함수는 fetch 우선 시도 → 실패 시 동일 형태의 mock 함수로 폴백
async function apiGetSegmentSummary() {
  try { return await fetchJson(...); }
  catch (e) { return mockSegmentSummary(); }
}
```
배포 시 `window.CUSTOMER_EMBEDDING_API_BASE`를 실제 백엔드 URL로 바꾸는 것이 유일한 설정 지점이다. 하단 상태 표시줄(`#api-status`)이 연결 여부를 실시간으로 보여준다(`pingApi()`가 초기 로드 후 별도 호출).

### 4.3 로컬 저장
캠페인 발송 이력은 `localStorage`(`customer_embedding_app_campaign_log` 키)에 JSON 배열로 저장한다. **브라우저/기기별로 분리**되며 서버와 동기화되지 않는다 — 다중 사용자 협업이 필요해지면 `GET /api/v1/campaigns/logs`를 프론트에서 소비하도록 변경해야 한다 (백엔드는 이미 구현됨).

### 4.4 접근성/반응형
- 버튼/입력에 `:focus-visible` 스타일 적용
- 780px 이하에서 그리드 레이아웃을 1~2열로 전환하는 미디어쿼리 포함
- 색상 대비: 브랜드 팔레트(잉크/브라스/세그먼트 컬러)가 텍스트-배경 조합에서 WCAG AA 대비를 목표로 하나, 정식 대비 검사(axe 등)는 미실시 — TODO

---

## 5. 데이터 스키마

### 5.1 `data/segment_summary.json`
```ts
{
  snapshot_date: string;       // "YYYY-MM-DD"
  source_file: string;
  method: string;
  segments: Array<{
    id: "growth" | "stable" | "dormant";
    n: number; pct: string; growth: string; dir: "up" | "down";
    oneLiner: string;
    profile: [string, string, string][];   // [항목, 값, 비율]
    metrics: [string, string][];            // [지표명, 값]
  }>;
}
```

### 5.2 `data/segment_trends.json`
```ts
{ months: string[]; growth: number[]; stable: number[]; dormant: number[]; new_entrants: number[]; }
```
4개 배열의 길이는 `months.length`와 동일해야 한다 (검증 로직 없음 — TODO).

### 5.3 `data/segment_migration.json`
```ts
{ [from: string]: { [to: string]: number } }  // 3x3 행렬, key는 segment id
```

### 5.4 고객 객체 (백엔드 목업 / 향후 `customer_features` 테이블)
```ts
{
  id: string;          // "CUST00001" 형식
  segment: "growth" | "stable" | "dormant";
  recency: number;     // 일
  freq: string;        // 월평균 구매빈도, 소수점 2자리 문자열
  amount: number;      // 12개월 누적 매출(원)
  topCategory: string;
  phone: string;       // 데모용 가짜 값 — 실 데이터 전환 시 마스킹/동의 확인 필수
  email: string;       // 데모용 가짜 값
}
```

### 5.5 캠페인 발송 로그
```ts
{
  log_id: string; target: object; channel: string; gateway: string | null;
  message_template_id: string; recipients: number;
  status: "sent" | "simulated" | "partial" | "failed" | "skipped";
  provider: string; detail: string; sent_at: string;
}
```

> DB 전환 시 테이블 정의는 `backend_api_spec.md` §2를 참고 (`segment_snapshot`, `customer_features`, `campaign_log`).

---

## 6. 배포 아키텍처

| 대상 | Render 서비스 유형 | Root Directory | Build | Start |
|---|---|---|---|---|
| `frontend/` | Static Site | `frontend` | (없음) | Publish Directory `.` |
| `backend/` | Web Service | `backend` | `npm install` | `npm start` |

- 프론트엔드와 백엔드는 **독립적으로 배포**되며, 프론트엔드는 빌드된 API URL(`window.CUSTOMER_EMBEDDING_API_BASE`)을 하드코딩한다. 환경별(스테이징/프로덕션) 분리가 필요해지면 이 값을 빌드 타임 주입 방식으로 전환 검토.
- 백엔드 환경변수는 Render **Environment** 탭에 등록 (`.env`는 배포되지 않음, `.gitignore` 처리됨).
- 무료 티어 Web Service는 트래픽 없을 시 슬립 → 콜드 스타트 지연(수 초) 발생 가능. SLA가 필요해지면 유료 플랜 또는 헬스체크 핑 스케줄러 고려.

---

## 7. 보안 요구사항

| 항목 | 현재 | TODO |
|---|---|---|
| 발송 자격증명 저장 위치 | 서버 환경변수만 (프론트 미노출) | 유지, Secrets Manager 전환 고려(스케일 시) |
| API 인증 | **없음** — 누구나 `POST /campaigns/send` 호출 가능 | JWT 기반 인증 + RBAC (발송 권한 분리) 도입 필요 |
| CORS | 전체 허용(`cors()` 기본값) | 프로덕션에서는 프론트엔드 도메인으로 제한 필요 |
| 개인정보(연락처) | 목업 가짜 데이터만 존재 | 실 데이터 연동 시 접근 로그·마스킹·보관기한 정책 수립 필수 |
| Rate limiting | 없음 | 캠페인 발송 엔드포인트에 우선 적용 검토 (오발송/남용 방지) |

---

## 8. 성능 및 확장성

| 이슈 | 현재 한계 | 개선 방향 |
|---|---|---|
| 캠페인 발송 | 요청-응답 동기 처리, 최대 20명 | SQS/BullMQ 등 큐 + 워커로 비동기 전환 |
| 세그먼트 데이터 갱신 | 서버 재시작 필요 (`require()` 캐시) | 파일 변경 감지 또는 DB 폴링/캐시 무효화 |
| 고객 검색 | 인메모리 60건 선형 탐색 | DB 인덱스 기반 검색으로 전환 시 자동 해결 |
| 프론트엔드 렌더링 | 전체 `innerHTML` 재생성 | 현재 데이터 규모(세그먼트 3개, 고객 60건)에서는 문제 없음. 데이터가 커지면 가상 스크롤 등 고려 |

---

## 9. 테스트 현황

**수행함**
- 백엔드 전체 라우트 `node --check` 구문 검사 및 실제 기동 후 curl로 응답 검증 (`/`, `/segments/summary`, `/campaigns/send` 정상/스킵 케이스)
- 게이트웨이 타임아웃 동작 검증 (더미 자격증명 + 네트워크 차단 환경에서 정상적으로 `failed` 반환 확인, 이 과정에서 타임아웃 누락 버그 발견 후 수정)
- 프론트엔드 JS 구문 검사 (`node --check`)

**미수행 (TODO)**
- 실제 알리고/SendGrid/FCM 자격증명을 이용한 end-to-end 발송 테스트
- 프론트엔드 자동화 테스트(E2E: Playwright/Cypress 등) — 현재는 수동 확인만
- 부하 테스트 (동시 요청, 세그먼트 대량 발송 시나리오)
- 접근성 자동 검사(axe-core 등)

---

## 10. 로깅 / 모니터링

- 현재는 `console.log`/`console.warn` 수준의 로깅만 존재하며, 구조화된 로그(JSON), 중앙 수집(Datadog/CloudWatch 등)은 없음.
- 캠페인 발송 실패율, 게이트웨이별 응답 시간 등 운영 지표를 위한 대시보드 필요 (M2~M3 로드맵 단계에서 검토, PRD §10 참고).

---

## 11. 알려진 기술 부채 / 이슈 이력

- 게이트웨이 fetch에 타임아웃이 없어 요청이 무한 대기할 수 있었던 이슈를 개발 중 실제로 재현·수정함 (모든 게이트웨이에 10초 `AbortController` 적용).
- 서버 기동 시 고객 목업 데이터가 매번 랜덤 생성되어 재시작마다 검색 결과가 달라짐 — 실 데이터 전환 전까지는 데모 특성으로 감안.
- 프론트엔드 `.env` 격인 설정 값(`window.CUSTOMER_EMBEDDING_API_BASE`)이 소스에 하드코딩되어 있어 배포 환경마다 코드 수정 후 재배포가 필요함 — 빌드 타임 환경변수 주입 또는 별도 `config.json` 분리 검토.
- 인증/RBAC 미구현 상태로, 실제 운영 전에는 반드시 도입 필요 (PRD Out of Scope 항목과 동일).
