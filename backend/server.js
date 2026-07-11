// server.js — 고객 세그먼트 리텐션 API (참조 구현)
// 실행: npm install && npm start   (기본 포트 4000)
//
// 지금은 아래 SEED_* 데이터가 하드코딩되어 있습니다.
// 실제 서비스에서는 이 부분을 DB 조회(예: SELECT ... FROM segment_snapshot)로 교체하면 됩니다.
// backend_api_spec.md 의 스키마/엔드포인트 정의를 그대로 따릅니다.

const express = require("express");
const cors = require("cors");

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 4000;

// ---------------------------------------------------------------
// 시드 데이터 (실서비스에서는 DB로 교체)
// ---------------------------------------------------------------
const SEGMENT_META = {
  growth: { name: "성장형 핵심 고객", color: "#2E7D32" },
  stable: { name: "안정형 주력 고객", color: "#1F3864" },
  dormant: { name: "이탈위험 휴면 고객", color: "#B23B3B" },
};

const SEED_SUMMARY = {
  snapshot_date: "2026-07-03",
  segments: [
    {
      id: "growth", n: 286, pct: "14.3%", growth: "+20.0%", dir: "up",
      oneLiner: "브랜드에 충성하며 정가로 자주 구매하고, YouTube·Instagram·TikTok에서 인플루언서 콘텐츠에 반응하는 고관여·고성장 고객군",
      profile: [["연령대", "30대", "40.2%"], ["성별", "여성", "79.4%"], ["거주지역", "수도권", "67.5%"], ["직업", "사무직", "31.8%"], ["관심사", "여행", "26.6%"], ["선호 스타일", "오피스룩", "33.2%"], ["소비성향", "브랜드충성형", "42.0%"]],
      metrics: [["최근 구매까지", "14.1일"], ["월평균 구매빈도", "2.24회"], ["12개월 누적 매출", "522만원"], ["평균 객단가", "127,681원"], ["정상가 비중", "78.9%"], ["재구매 주기", "23.3일"], ["30일 세션 수", "5.38회"], ["장바구니→구매 전환율", "0.291"]],
    },
    {
      id: "stable", n: 1106, pct: "55.3%", growth: "+3.4%", dir: "up",
      oneLiner: "완만한 성장세를 유지하는 전체 볼륨의 절반 이상을 차지하는 주력 고객군",
      profile: [["연령대", "30대", "34.1%"], ["성별", "여성", "78.9%"], ["거주지역", "수도권", "57.0%"], ["직업", "사무직", "32.8%"], ["관심사", "뷰티", "28.6%"], ["선호 스타일", "캐주얼", "28.8%"], ["소비성향", "트렌드추구형", "41.4%"]],
      metrics: [["최근 구매까지", "47.8일"], ["월평균 구매빈도", "1.02회"], ["12개월 누적 매출", "76.3만원"], ["평균 객단가", "55,173원"], ["정상가 비중", "49.3%"], ["재구매 주기", "51.5일"], ["30일 세션 수", "2.59회"], ["장바구니→구매 전환율", "0.179"]],
    },
    {
      id: "dormant", n: 608, pct: "30.4%", growth: "-12.9%", dir: "down",
      oneLiner: "절반 가까이가 최근 30일간 세션 자체가 없고, 활동이 있어도 상세조회 단계에서 대부분 이탈하는 고위험군",
      profile: [["연령대", "20대", "41.6%"], ["성별", "여성", "77.5%"], ["거주지역", "수도권", "47.9%"], ["직업", "학생", "32.4%"], ["관심사", "피트니스", "21.1%"], ["선호 스타일", "캐주얼", "41.4%"], ["소비성향", "실속형(가성비)", "61.0%"]],
      metrics: [["최근 구매까지", "155.7일"], ["월평균 구매빈도", "0.44회"], ["12개월 누적 매출", "12.7만원"], ["평균 객단가", "26,674원"], ["정상가 비중", "23.4%"], ["재구매 주기", "117.7일"], ["30일 세션 수", "0.82회"], ["장바구니→구매 전환율", "0.005"]],
    },
  ],
};

const SEED_TRENDS = {
  months: ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"],
  growth: [231, 244, 253, 261, 274, 286],
  stable: [1148, 1141, 1130, 1121, 1113, 1106],
  dormant: [621, 615, 617, 618, 613, 608],
  new_entrants: [58, 63, 51, 47, 55, 49],
};

const SEED_MIGRATION = {
  growth: { growth: 261, stable: 24, dormant: 1 },
  stable: { growth: 22, stable: 1040, dormant: 44 },
  dormant: { growth: 3, stable: 42, dormant: 563 },
};

// 고객 목업 60명 (실서비스에서는 customer_features 테이블 조회로 교체)
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
    rows.push({
      id: "CUST" + String(i).padStart(5, "0"),
      segment: seg,
      recency: Math.round(rand(...base.recency)),
      freq: rand(...base.freq).toFixed(2),
      amount: Math.round(rand(...base.amt)),
      topCategory: cats[Math.floor(Math.random() * cats.length)],
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

// 실제 발송 게이트웨이(카카오/이메일/SMS) 연동 지점.
// 지금은 요청을 로그에만 남기고 성공으로 응답합니다.
app.post("/api/v1/campaigns/send", (req, res) => {
  const { target, channel, message_template_id } = req.body || {};
  if (!target || !channel) {
    return res.status(400).json({ error: "invalid_request", message: "target, channel은 필수입니다." });
  }
  const seg = target.type === "segment" ? SEED_SUMMARY.segments.find(s => s.id === target.id) : null;
  const recipients = seg ? seg.n : 1;

  // TODO: 실제 발송 게이트웨이 호출
  // - 카카오 알림톡: 카카오 비즈메시지 API
  // - 이메일: SendGrid / AWS SES
  // - SMS: 알리고 / NAVER Cloud SENS
  // - 앱 푸시: FCM

  const log = {
    log_id: "CMP-" + Date.now(),
    target, channel, message_template_id,
    recipients, status: "sent",
    sent_at: new Date().toISOString(),
  };
  campaignLogs.unshift(log);
  res.json({ status: "queued", log_id: log.log_id, estimated_recipients: recipients });
});

app.get("/api/v1/campaigns/logs", (req, res) => {
  const limit = parseInt(req.query.limit || "20", 10);
  res.json(campaignLogs.slice(0, limit));
});

app.get("/", (req, res) => {
  res.json({ status: "ok", service: "retention-api", endpoints: [
    "GET /api/v1/segments/summary", "GET /api/v1/segments/trends", "GET /api/v1/segments/migration",
    "GET /api/v1/customers?query=", "GET /api/v1/customers/:id",
    "POST /api/v1/campaigns/send", "GET /api/v1/campaigns/logs",
  ]});
});

app.listen(PORT, () => {
  console.log(`Retention API listening on port ${PORT}`);
});
