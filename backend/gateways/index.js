// gateways/index.js — 발송 게이트웨이 디스패처
//
// 프론트엔드/캠페인 데이터의 "채널"(예: "Instagram / YouTube", "네이버 / 카카오")은
// 마케팅 관점의 콘텐츠 채널 설명이고, 실제 발송에 쓰는 "게이트웨이"는 별도입니다.
// 광고 플랫폼(Instagram/YouTube/TikTok)은 개별 API로 1:1 발송하는 게 아니라
// 광고 관리자에서 캠페인을 집행하는 방식이라 이 디스패처의 대상이 아닙니다.

const kakao = require("./kakaoAlimtalk");
const sms = require("./sms");
const email = require("./email");
const push = require("./push");

const GATEWAYS = { kakao, sms, email, push };

// 한 번의 API 요청으로 동기 처리할 최대 수신자 수.
// 실서비스에서는 세그먼트 규모(수백~수천 명)를 이 방식으로 처리하면 안 되고,
// SQS/RabbitMQ 같은 큐에 넣고 워커가 비동기로 처리해야 합니다.
// 여기서는 참고 구현이라 데모 목적으로 상한을 둡니다.
const MAX_SYNC_RECIPIENTS = 20;

/**
 * @param {string} gatewayId  'kakao' | 'sms' | 'email' | 'push' | 그 외(광고 채널 등)
 * @param {object} campaign   { segmentId, title, message, templateCode }
 * @param {object[]} customers 대상 고객 목록 (mock: id, phone, email 포함)
 */
async function dispatchCampaign(gatewayId, campaign, customers) {
  const gw = GATEWAYS[gatewayId];
  if (!gw) {
    return {
      status: "skipped",
      provider: "none",
      recipients: 0,
      detail: `'${gatewayId}'는 등록된 발송 게이트웨이가 아닙니다. Instagram/TikTok 등 광고 채널은 광고 관리자에서 별도 집행이 필요합니다.`,
      results: [],
    };
  }

  // 푸시는 세그먼트 토픽 하나로 한 번에 발송
  if (gatewayId === "push") {
    const result = await gw.send({ segmentId: campaign.segmentId, title: campaign.title, message: campaign.message });
    return { ...result, recipients: customers.length, results: [result] };
  }

  // 카카오/SMS/이메일은 고객 단위로 개별 발송 (여기서는 데모용으로 상한을 둔 동기 반복)
  const targets = customers.slice(0, MAX_SYNC_RECIPIENTS);
  const results = [];
  for (const c of targets) {
    const to = gatewayId === "email" ? c.email : c.phone;
    if (!to) {
      results.push({ status: "failed", provider: gw.channel, detail: `고객 ${c.id}에 ${gatewayId === "email" ? "이메일" : "전화번호"} 정보 없음` });
      continue;
    }
    const r = await gw.send({ to, title: campaign.title, message: campaign.message, templateCode: campaign.templateCode });
    results.push({ ...r, customerId: c.id });
  }

  const sentCount = results.filter(r => r.status === "sent" || r.status === "simulated").length;
  const failedCount = results.length - sentCount;
  const skippedCount = Math.max(0, customers.length - targets.length);

  return {
    status: failedCount === 0 ? (results[0]?.status || "sent") : "partial",
    provider: gw.channel,
    recipients: customers.length,
    sent: sentCount,
    failed: failedCount,
    detail: skippedCount > 0
      ? `${targets.length}명에게 실제 API 호출 시도(데모 상한), 나머지 ${skippedCount}명은 큐잉 대상으로 남김`
      : `${targets.length}명 처리 완료`,
    results,
  };
}

module.exports = { dispatchCampaign, GATEWAYS };
