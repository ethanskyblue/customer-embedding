// server.js — 고객 세그먼트 리텐션 API (참조 구현)
// 실행: npm install && npm start   (기본 포트 4000)
//
// 지금은 아래 SEED_* 데이터가 하드코딩되어 있습니다.
// 실제 서비스에서는 이 부분을 DB 조회(예: SELECT ... FROM segment_snapshot)로 교체하면 됩니다.
// backend_api_spec.md 의 스키마/엔드포인트 정의를 그대로 따릅니다.

require("dotenv").config();
const express = require("express");
const cors = require("cors");
const path = require("path");
const { dispatchCampaign, GATEWAYS } = require("./gateways");
const graph = require("./graph");

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 4000;

// ---------------------------------------------------------------
// 시드 데이터: data/*.json 에서 로드
//
// 이 JSON 파일들은 Python 클러스터링 배치 잡(노트북 참고)의 산출물입니다.
// 즉 "클러스터링은 이 서버가 하는 게 아니라" 별도의 배치 작업이 미리 계산해서
// data/ 에 결과를 떨어뜨려주고, 이 서버는 그 결과를 읽어서 API로 노출만 합니다.
// 실서비스에서는 이 require() 대신 DB(segment_snapshot 테이블) 조회로 교체하세요.
// 원본 학습 데이터: data/customer_embedding_training_data_2000_segmented.xlsx
// ---------------------------------------------------------------
const SEED_SUMMARY = require(path.join(__dirname, "..", "data", "segment_summary.json"));
const SEED_TRENDS = require(path.join(__dirname, "..", "data", "segment_trends.json"));
const SEED_MIGRATION = require(path.join(__dirname, "..", "data", "segment_migration.json"));

const SEGMENT_META = {
  growth: { name: "성장형 핵심 고객", color: "#2E7D32" },
  stable: { name: "안정형 주력 고객", color: "#1F3864" },
  dormant: { name: "이탈위험 휴면 고객", color: "#B23B3B" },
};

// 고객 목업 60명 (실서비스에서는 customer_features 테이블 조회로 교체)
// phone/email은 카카오·SMS·이메일 게이트웨이 데모용으로 추가한 필드입니다.
function buildCustomers() {
  const cats = ["원피스", "니트", "청바지", "코트", "가디건", "액세서리", "신발", "가방"];
  const segs = ["growth", "stable", "dormant"];
  const weights = [0.143, 0.553, 0.304];
  const rows = [];
  for (let i = 1; i <= 60; i++) {
    const r = Math.random();
    let seg = "dormant", acc = 0;
    for (let j = 0; j < segs.length; j++) { acc += weights[j]; if (r <= acc) { seg = segs[j]; break; } }
    const base = seg === "growth" ? { recency: [8, 25], freq: [1.6, 3.0], amt: [3000000, 8500000] }
      : seg === "stable" ? { recency: [25, 75], freq: [0.5, 1.6], amt: [300000, 1400000] }
      : { recency: [90, 220], freq: [0.1, 0.7], amt: [20000, 300000] };
    const rand = (a, b) => a + Math.random() * (b - a);
    const id = "CUST" + String(i).padStart(5, "0");
    rows.push({
      id, segment: seg,
      recency: Math.round(rand(...base.recency)),
      freq: rand(...base.freq).toFixed(2),
      amount: Math.round(rand(...base.amt)),
      topCategory: cats[Math.floor(Math.random() * cats.length)],
      // 데모용 가짜 연락처 (실서비스에서는 CRM/ERP의 실제 연락처로 교체, 마스킹·동의 여부 확인 필수)
      phone: "0100000" + String(i).padStart(4, "0"),
      email: id.toLowerCase() + "@example.com",
    });
  }
  return rows;
}
const CUSTOMERS = buildCustomers();

// 캠페인 발송 이력 (실서비스에서는 campaign_log 테이블)
const campaignLogs = [];

// ---------------------------------------------------------------
// 라우트
// ---------------------------------------------------------------
app.get("/api/v1/segments/summary", (req, res) => {
  res.json(SEED_SUMMARY);
});

app.get("/api/v1/segments/trends", (req, res) => {
  res.json(SEED_TRENDS);
});

app.get("/api/v1/segments/migration", (req, res) => {
  res.json(SEED_MIGRATION);
});

app.get("/api/v1/customers", (req, res) => {
  const q = (req.query.query || "").toUpperCase();
  const segment = req.query.segment;
  let rows = CUSTOMERS;
  if (q) rows = rows.filter(r => r.id.includes(q));
  if (segment) rows = rows.filter(r => r.segment === segment);
  res.json(rows.slice(0, 20));
});

app.get("/api/v1/customers/:id", (req, res) => {
  const row = CUSTOMERS.find(r => r.id === req.params.id);
  if (!row) return res.status(404).json({ error: "not_found" });
  res.json(row);
});

// 그래프 DB(Neo4j) 탐색: 같은 상품을 조회한 다른 고객 찾기.
// Neo4j 환경변수가 없으면 configured:false로 응답 (프론트엔드가 그래프 미연결 안내를 표시함).
app.get("/api/v1/customers/:id/similar", async (req, res) => {
  const customerId = req.params.id.toUpperCase(); // 검색 엔드포인트와 동일하게 대소문자 구분 없이 처리
  try {
    const { configured, results } = await graph.findSimilarCustomers(customerId, 10);
    res.json({ configured, customerId, similar: results });
  } catch (e) {
    console.error("graph query failed:", e.message); // 상세 원인은 서버 로그에만 남김
    res.status(500).json({ error: "graph_query_failed", message: "그래프 조회에 실패했습니다." });
  }
});

// 실제 발송 게이트웨이(카카오 알림톡/SMS/이메일/푸시) 연동.
// gateway 파라미터로 카카오/SMS/이메일/푸시 중 하나를 지정하면 gateways/index.js가
// 해당 대행사 API를 실제로 호출합니다 (자격증명이 없으면 자동으로 시뮬레이션 처리).
app.post("/api/v1/campaigns/send", async (req, res) => {
  const { target, channel, gateway, message_template_id, title, message } = req.body || {};
  if (!target || !channel) {
    return res.status(400).json({ error: "invalid_request", message: "target, channel은 필수입니다." });
  }
  if (!message) {
    return res.status(400).json({ error: "invalid_request", message: "message(발송 문구)는 필수입니다." });
  }

  const targetCustomers = target.type === "segment"
    ? CUSTOMERS.filter(c => c.segment === target.id)
    : CUSTOMERS.filter(c => c.id === target.id);

  const gatewayId = gateway || null; // 'kakao' | 'sms' | 'email' | 'push' | null(=미지정, 광고 채널 등)
  const dispatchResult = await dispatchCampaign(
    gatewayId,
    { segmentId: target.type === "segment" ? target.id : targetCustomers[0]?.segment, title, message, templateCode: message_template_id },
    targetCustomers,
  );

  const log = {
    log_id: "CMP-" + Date.now(),
    target, channel, gateway: gatewayId, message_template_id,
    recipients: dispatchResult.recipients,
    status: dispatchResult.status,
    provider: dispatchResult.provider,
    detail: dispatchResult.detail,
    sent_at: new Date().toISOString(),
  };
  campaignLogs.unshift(log);

  res.json({
    status: dispatchResult.status,
    log_id: log.log_id,
    estimated_recipients: dispatchResult.recipients,
    provider: dispatchResult.provider,
    detail: dispatchResult.detail,
  });
});

// 등록된 발송 게이트웨이와 자격증명 설정 여부 (운영 점검용)
app.get("/api/v1/gateways/status", (req, res) => {
  const status = Object.fromEntries(
    Object.entries(GATEWAYS).map(([id, gw]) => [id, { configured: gw.isConfigured() }])
  );
  res.json(status);
});

app.get("/api/v1/graph/status", (req, res) => {
  res.json({ neo4j: { configured: graph.isConfigured() } });
});

app.get("/api/v1/campaigns/logs", (req, res) => {
  const limit = parseInt(req.query.limit || "20", 10);
  res.json(campaignLogs.slice(0, limit));
});

app.get("/", (req, res) => {
  res.json({ status: "ok", service: "customer-embedding-api", endpoints: [
    "GET /api/v1/segments/summary", "GET /api/v1/segments/trends", "GET /api/v1/segments/migration",
    "GET /api/v1/customers?query=", "GET /api/v1/customers/:id",
    "POST /api/v1/campaigns/send", "GET /api/v1/campaigns/logs",
  ]});
});

app.listen(PORT, () => {
  console.log(`Customer Embedding API listening on port ${PORT}`);
});

process.on("SIGTERM", async () => { await graph.closeDriver(); process.exit(0); });
process.on("SIGINT", async () => { await graph.closeDriver(); process.exit(0); });

