# SECURITY: Customer Embedding App 보안 점검 결과

| 항목 | 내용 |
|---|---|
| 문서 버전 | v1.0 |
| 점검일 | 2026-07-03 |
| 점검 범위 | `backend/server.js`, `backend/gateways/*.js`, `frontend/index.html` (코드 직접 검토) |
| 점검 방식 | 정적 코드 리뷰 (자동화된 스캐너 미사용 — `npm audit`, OWASP ZAP 등은 별도 실행 권장) |

지금은 합성/목업 데이터라 사고가 나도 피해가 제한적이지만, 아래 항목들은 **실제 ERP/고객 데이터와 연동하는 순간 그대로 사고로 이어지는 구조적 문제**라 데이터 연동 전에 반드시 해결이 필요합니다.

---

## 요약

| 심각도 | 개수 | 항목 |
|---|---|---|
| 🔴 Critical | 2 | 인증 부재, PII 무방비 노출 |
| 🟠 High | 2 | CORS 전체 허용, 속도 제한 없음 |
| 🟡 Medium | 3 | 에러 상세 노출, 입력값 검증 없음, 프론트엔드 XSS |
| 🟢 Low | 3 | 보안 헤더 부재, 감사 추적 불가, 의존성 스캔 미실시 |

---

## 🔴 Critical

### 1. 모든 API에 인증이 없음
`backend/server.js`를 전체 검토한 결과, 어떤 라우트에도 인증 미들웨어가 없습니다. 특히 `POST /api/v1/campaigns/send`는 **URL만 알면 누구나 실제 카카오 알림톡/SMS/이메일 발송을 트리거할 수 있습니다.**

```js
// backend/server.js
app.post("/api/v1/campaigns/send", async (req, res) => {
  const { target, channel, gateway, message_template_id, title, message } = req.body || {};
  // ... 인증 검사 없이 바로 dispatchCampaign() 호출
```

**위험**: 스팸 발송, 발송 게이트웨이 계정 크레딧 소진(비용 공격), 브랜드 명의로 임의 메시지 발송(피싱 악용 가능).

**개선안**
- 최소한 API Key 헤더 검증부터 시작 (`x-api-key` + 서버 환경변수 비교)
- 중기적으로 JWT 기반 인증 + 발송 권한(RBAC) 분리 — 조회는 일반 사용자, 발송은 마케팅 매니저 이상만

### 2. 고객 개인정보(PII)가 인증 없이 그대로 노출됨
```js
// backend/server.js
app.get("/api/v1/customers", (req, res) => {
  // 인증 없음
  res.json(rows.slice(0, 20));   // phone, email이 그대로 포함된 객체
});
```
지금은 가짜 전화번호/이메일이지만, **API 계약(응답 스키마)이 실제 연동 시에도 그대로 유지될 구조**라, 실 데이터 전환 시 이 엔드포인트 하나로 전체 고객 연락처가 인증 없이 조회 가능해집니다.

**개선안**
- 인증 적용(#1과 동일)
- `phone`/`email` 같은 민감 필드는 목적에 따라 마스킹된 값(`010-****-1234`)만 기본 노출하고, 발송 등 실제 필요 시점에만 서버 내부에서 원본을 사용
- 응답 필드를 요청자 권한에 따라 다르게 구성(field-level access control)

---

## 🟠 High

### 3. CORS가 모든 출처에 열려 있음
```js
app.use(cors());   // 옵션 없음 = Access-Control-Allow-Origin: *
```
어떤 웹사이트에서든 브라우저 JS로 이 API를 호출할 수 있습니다. #1, #2와 결합하면 악성 사이트가 방문자 브라우저를 통해 API를 대신 호출하게 만들 수 있습니다.

**개선안**
```js
app.use(cors({ origin: ["https://고객사도메인.onrender.com"], credentials: true }));
```
프론트엔드 실제 배포 도메인만 명시적으로 허용.

### 4. 속도 제한(rate limiting)이 전혀 없음
발송 엔드포인트를 포함해 모든 라우트가 무제한 호출 가능합니다. 캠페인 발송처럼 **호출당 실제 비용이 발생하는 엔드포인트**에 제한이 없는 건 특히 위험합니다.

**개선안**
- `express-rate-limit` 같은 미들웨어로 IP/키 기준 기본 제한 적용
- `/api/v1/campaigns/send`는 더 엄격한 별도 제한(예: 분당 1회) 권장

---

## 🟡 Medium

### 5. 발송 실패 시 내부 오류 상세가 그대로 응답에 노출됨
```js
// backend/gateways/kakaoAlimtalk.js
return { status: "failed", provider: "aligo_alimtalk", detail: `[${data.code}] ${data.message}` };
```
이 `detail`이 그대로 `POST /campaigns/send` 응답에 담겨 호출자에게 전달됩니다. 대행사 API의 원문 에러 메시지에는 계정 설정·잔여 크레딧 등 내부 정보가 섞여 나올 수 있습니다.

**개선안**: 클라이언트에는 일반화된 메시지(`"발송에 실패했습니다"`)만 보내고, 원본 `detail`은 서버 로그에만 기록.

### 6. 요청 바디에 대한 스키마 검증이 없음
`target.type`, `target.id`, `message` 등이 타입/형식 검증 없이 그대로 내부 로직과 게이트웨이 페이로드에 사용됩니다.
```js
// backend/gateways/push.js
to: `/topics/segment_${payload.segmentId}`,   // segmentId가 growth/stable/dormant인지 검증 안 함
```
`message`도 길이 제한이 없어 비정상적으로 긴 문자열을 그대로 알림톡/SMS API에 전달할 수 있습니다.

**개선안**: `zod`/`joi` 같은 스키마 검증 라이브러리로 라우트 진입점에서 즉시 검증(`target.type`은 enum, `segmentId`는 화이트리스트, `message`는 길이 상한).

### 7. 프론트엔드 이스케이프 함수가 따옴표를 처리하지 않음
```js
// frontend/index.html
function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
...
<input ... value="${esc(state.searchQuery)}" .../>
```
`esc()`가 `"`를 이스케이프하지 않아서, 검색창에 `" onmouseover="alert(1)` 같은 문자열을 입력하면 속성 컨텍스트를 탈출해 임의 HTML/JS가 삽입됩니다. 지금은 검색 결과가 클라이언트 자기 자신에게만 반영되는 셀프 XSS 수준이지만, **이스케이프 함수 자체의 결함**이라 다른 값(예: 실 데이터 연동 후 고객명 등)이 같은 함수로 렌더링되면 저장형/반사형 XSS로 커질 수 있습니다.

**개선안**
```js
function esc(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
```
근본적으로는 문자열 조립 대신 `textContent`/`setAttribute` 사용을 권장하지만, 지금 구조(템플릿 리터럴 기반 렌더링)에서는 최소한 따옴표까지 이스케이프하는 게 시급합니다.

---

## 🟢 Low

### 8. 보안 응답 헤더 미설정
`helmet` 같은 미들웨어가 없어 `X-Content-Type-Options`, `X-Frame-Options` 등 기본 보안 헤더가 빠져 있습니다. → `app.use(helmet())` 한 줄 추가로 대부분 해결.

### 9. 감사 추적(audit trail) 불가
인증이 없다 보니(#1) 캠페인 로그에 "누가" 보냈는지 기록할 방법이 없습니다. 인증 도입 후 `campaign_log`에 `sent_by` 필드 추가 권장.

### 10. 의존성 취약점 점검 미실시
`package.json`의 `express`/`cors`/`dotenv`에 대해 `npm audit` 또는 GitHub Dependabot을 아직 켜지 않았습니다. 저장소 설정에서 Dependabot alerts만 켜도 최소한의 감시가 됩니다.

---

## 우선순위 제안

| 순서 | 항목 | 이유 |
|---|---|---|
| 1 | #1 인증 도입 | 나머지 문제 대부분이 "인증 없음"에서 파생됨 |
| 2 | #3 CORS 제한 | 코드 한 줄 수정으로 즉시 효과 |
| 3 | #7 esc() 따옴표 이스케이프 | 코드 한 줄 수정, 즉시 적용 가능 |
| 4 | #4 Rate limiting | 실 서비스 전환 전 필수 |
| 5 | #2 PII 마스킹 | 실 데이터 연동과 동시에 반드시 적용 |
| 6 | #6 입력값 검증 | 실 데이터 연동 전 적용 |
| 7 | #5, #8, #9, #10 | 여유 있을 때 순차 적용 |

1~3번은 코드 몇 줄로 지금 바로 고칠 수 있는 항목입니다. 원하시면 바로 반영해드릴까요?
