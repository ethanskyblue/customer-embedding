# 고객 세그먼트 리텐션 대시보드 — 백엔드 연동 아키텍처 및 API 명세

## 1. 전체 데이터 흐름

```
[원천 로그]                [배치/스트리밍 파이프라인]           [클러스터링 서비스]        [API 서버]        [대시보드]
세션·이벤트 로그   ──┐                                                                                   
구매 이력          ──┼──▶  ETL (일 배치 or 스트리밍)  ──▶  피처 집계 → PCA 임베딩   ──▶  세그먼트 테이블 ──▶  REST API ──▶  프론트엔드
SNS 반응           ──┤        (Airflow / dbt 등)          → K-means 재학습/재배정      + 캠페인 로그
고객 프로필         ──┘                                     (일 1회 또는 6시간 주기)
```

**배치 vs 실시간 권장**
- 세그먼트 재계산(클러스터링)은 **일 배치**로 충분합니다. 고객의 세그먼트는 하루 이틀 사이에 급변하지 않고, K-means 전체 재학습은 계산 비용이 있어 매 요청마다 돌릴 이유가 없습니다.
- 개별 고객 조회, 캠페인 발송 이력 조회는 **실시간(API 직접 조회)**로 처리합니다.
- 세그먼트 이동/신규 편입 같은 시계열 지표는 **배치 결과를 매일 스냅샷으로 적재**해 트렌드 테이블에 쌓는 방식을 권장합니다.

## 2. 데이터 모델 (최소 스키마)

```sql
-- 세그먼트 배정 (배치마다 스냅샷 적재 → 시계열 조회 가능)
segment_snapshot (
  snapshot_date   DATE,
  customer_id     VARCHAR,
  segment_id      VARCHAR,     -- 'growth' | 'stable' | 'dormant'
  cluster_score   FLOAT,       -- 임베딩 공간에서의 실루엣/거리 등 신뢰도
  is_new_entrant  BOOLEAN      -- 전일 대비 신규 편입 여부
)

-- 고객 단위 최신 피처 (검색/상세 조회용)
customer_features (
  customer_id, segment_id, recency_days, purchase_freq_monthly,
  total_amount_12m, aov, top_category, exit_point, sales_growth_pct, updated_at
)

-- 캠페인 발송 이력
campaign_log (
  log_id, customer_id / segment_id, channel, message_template_id,
  status,          -- 'queued' | 'sent' | 'failed'
  sent_at, provider_message_id
)
```

## 3. REST API 명세

### 3.1 세그먼트 개요
```
GET /api/v1/segments/summary?date=2026-07-03
```
```json
{
  "snapshot_date": "2026-07-03",
  "total_customers": 2000,
  "segments": [
    { "id": "growth", "name": "성장형 핵심 고객", "count": 286, "pct": 14.3, "revenue_growth_pct": 20.0 },
    { "id": "stable", "name": "안정형 주력 고객", "count": 1106, "pct": 55.3, "revenue_growth_pct": 3.4 },
    { "id": "dormant", "name": "이탈위험 휴면 고객", "count": 608, "pct": 30.4, "revenue_growth_pct": -12.9 }
  ]
}
```

### 3.2 세그먼트 시계열 (이동 추이 / 신규 편입)
```
GET /api/v1/segments/trends?from=2026-02-01&to=2026-07-01&granularity=month
```
```json
{
  "series": [
    { "month": "2026-02", "growth": 240, "stable": 1150, "dormant": 610, "new_entrants": 58 },
    { "month": "2026-03", "growth": 251, "stable": 1140, "dormant": 609, "new_entrants": 63 }
  ]
}
```

### 3.3 세그먼트 간 이동 매트릭스 (이번 달 vs 지난달)
```
GET /api/v1/segments/migration?from=2026-06-01&to=2026-07-01
```
```json
{
  "matrix": {
    "growth":  { "growth": 260, "stable": 24, "dormant": 2 },
    "stable":  { "growth": 31,  "stable": 1040, "dormant": 35 },
    "dormant": { "growth": 1,   "stable": 18,  "dormant": 589 }
  }
}
```

### 3.4 개별 고객 검색/조회
```
GET /api/v1/customers/{customer_id}
GET /api/v1/customers?query=CUST00123&segment=dormant&page=1
```
```json
{
  "customer_id": "CUST00123",
  "segment_id": "dormant",
  "recency_days": 162,
  "purchase_freq_monthly": 0.3,
  "total_amount_12m": 98000,
  "top_category": "청바지",
  "exit_point": "상품_상세조회",
  "sales_growth_pct": -18.2
}
```

### 3.5 캠페인 발송
```
POST /api/v1/campaigns/send
Content-Type: application/json

{
  "target": { "type": "segment", "id": "dormant" },   // 또는 { "type": "customer", "id": "CUST00123" }
  "channel": "kakao",
  "message_template_id": "dormant_winback_30off",
  "scheduled_at": null   // null이면 즉시 발송
}
```
```json
{ "status": "queued", "log_id": "CMP-20260703-00042", "estimated_recipients": 608 }
```

```
GET /api/v1/campaigns/logs?segment_id=dormant&limit=20
```

## 4. 인증/권한
- 대시보드 → API: 사내 SSO 기반 JWT (읽기 전용 role과 캠페인 발송 role 분리 — 발송은 마케팅 매니저 이상만 가능하도록 RBAC 권장)
- API → 캠페인 발송사(카카오/문자 게이트웨이/ESP): 별도 서버사이드 키로 프록시, 프론트엔드에 발송 자격증명 노출 금지

## 5. 실제 캠페인 발송 연동 지점
`POST /api/v1/campaigns/send`가 내부적으로 아래 중 하나를 호출:
- 이메일: SendGrid / AWS SES API
- 카카오 알림톡: 카카오 비즈메시지 API (사전 승인된 템플릿 필요)
- SMS: 알리고/네이버클라우드 SENS 등 국내 SMS 게이트웨이
- 앱 푸시: FCM(Firebase Cloud Messaging)

## 6. 프론트엔드 연동 방법
지금 드리는 `customer_retention_app.html`은 위 5개 엔드포인트를 호출하는 지점에
`// TODO: fetch('/api/v1/...')`  주석과 함께 목업 함수가 들어가 있습니다.
실제 백엔드가 준비되면 목업 함수 내부를 fetch 호출로 교체하기만 하면 됩니다 (UI 로직은 변경 불필요).
