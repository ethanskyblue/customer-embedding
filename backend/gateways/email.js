// gateways/email.js — 이메일 발송 (SendGrid v3 API 기준)
//
// 필요 환경변수: SENDGRID_API_KEY, SENDGRID_FROM_EMAIL

const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";

function isConfigured() {
  return !!(process.env.SENDGRID_API_KEY && process.env.SENDGRID_FROM_EMAIL);
}

async function send(payload) {
  if (!isConfigured()) {
    return { status: "simulated", provider: "sendgrid", detail: "SENDGRID_* 환경변수가 설정되지 않아 실제 발송 대신 시뮬레이션했습니다." };
  }

  const body = {
    personalizations: [{ to: [{ email: payload.to }] }],
    from: { email: process.env.SENDGRID_FROM_EMAIL, name: process.env.SENDGRID_FROM_NAME || "고객 리텐션팀" },
    subject: payload.title || "안내 메시지",
    content: [{ type: "text/plain", value: payload.message }],
  };

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(SENDGRID_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.SENDGRID_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (res.status === 202) {
      return { status: "sent", provider: "sendgrid", providerMessageId: res.headers.get("x-message-id") || "", detail: "accepted" };
    }
    const errText = await res.text();
    return { status: "failed", provider: "sendgrid", detail: `[${res.status}] ${errText}` };
  } catch (e) {
    return { status: "failed", provider: "sendgrid", detail: e.message };
  }
}

module.exports = { send, isConfigured, channel: "email" };
