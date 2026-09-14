// gateways/sms.js — 문자(SMS/LMS) 발송 (알리고 SMS API 기준)
// 알림톡 실패 시 대체발송 채널로도 쓰입니다.
//
// 필요 환경변수: ALIGO_API_KEY, ALIGO_USER_ID, ALIGO_SENDER_PHONE

const ALIGO_SMS_URL = "https://apis.aligo.in/send/";

function isConfigured() {
  return !!(process.env.ALIGO_API_KEY && process.env.ALIGO_USER_ID && process.env.ALIGO_SENDER_PHONE);
}

async function send(payload) {
  if (!isConfigured()) {
    return { status: "simulated", provider: "aligo_sms", detail: "ALIGO_* 환경변수가 설정되지 않아 실제 발송 대신 시뮬레이션했습니다." };
  }

  const body = new URLSearchParams({
    apikey: process.env.ALIGO_API_KEY,
    userid: process.env.ALIGO_USER_ID,
    sender: process.env.ALIGO_SENDER_PHONE,
    receiver: payload.to,
    msg: payload.message,
    msg_type: payload.message.length > 45 ? "LMS" : "SMS",
    testmode_yn: process.env.ALIGO_TEST_MODE === "true" ? "Y" : "N",
  });

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(ALIGO_SMS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const data = await res.json();
    if (data.result_code === "1" || data.result_code === 1) {
      return { status: "sent", provider: "aligo_sms", providerMessageId: String(data.msg_id || ""), detail: data.message };
    }
    return { status: "failed", provider: "aligo_sms", detail: `[${data.result_code}] ${data.message}` };
  } catch (e) {
    return { status: "failed", provider: "aligo_sms", detail: e.message };
  }
}

module.exports = { send, isConfigured, channel: "sms" };
