// gateways/kakaoAlimtalk.js — 카카오 알림톡 발송 (알리고 AlimTalk API 기준)
//
// 카카오는 알림톡을 직접 열어주지 않고, 알리고/NHN Toast/인포뱅크 같은
// 인증된 대행사를 통해서만 보낼 수 있습니다. 여기서는 국내에서 가장 흔히 쓰는
// 알리고(https://smartsms.aligo.in) AlimTalk API를 기준으로 구현했습니다.
// 다른 대행사를 쓰신다면 이 파일만 해당 대행사의 API 스펙에 맞춰 바꾸면 됩니다.
//
// 사전 준비 (알리고 콘솔에서):
//   1) 카카오 채널 연동 및 발신 프로필키(senderkey) 발급
//   2) 알림톡 템플릿 등록·승인 (tpl_code)
//   3) API 키 발급
//
// 필요 환경변수:
//   ALIGO_API_KEY, ALIGO_USER_ID, ALIGO_SENDER_KEY, ALIGO_SENDER_PHONE

const ALIGO_ALIMTALK_URL = "https://kakaoapi.aligo.in/akv10/alimtalk/send/";

function isConfigured() {
  return !!(process.env.ALIGO_API_KEY && process.env.ALIGO_USER_ID &&
            process.env.ALIGO_SENDER_KEY && process.env.ALIGO_SENDER_PHONE);
}

/**
 * @param {object} payload
 * @param {string} payload.to           수신자 전화번호 (- 없이 01012345678 형식)
 * @param {string} payload.templateCode 알리고에 등록된 알림톡 템플릿 코드
 * @param {string} payload.message      템플릿 변수가 치환된 실제 발송 문구
 * @param {string} [payload.title]      알림톡 강조 표기형 타이틀 (템플릿에 따라 선택)
 */
async function send(payload) {
  if (!isConfigured()) {
    return {
      status: "simulated",
      provider: "aligo_alimtalk",
      detail: "ALIGO_* 환경변수가 설정되지 않아 실제 발송 대신 시뮬레이션했습니다.",
    };
  }

  const body = new URLSearchParams({
    apikey: process.env.ALIGO_API_KEY,
    userid: process.env.ALIGO_USER_ID,
    senderkey: process.env.ALIGO_SENDER_KEY,
    tpl_code: payload.templateCode,
    sender: process.env.ALIGO_SENDER_PHONE,
    receiver_1: payload.to,
    subject_1: payload.title || "",
    message_1: payload.message,
    failover: "N", // 알림톡 실패 시 SMS 대체발송 여부 (Y로 바꾸면 실패 시 문자로 자동 대체)
  });

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(ALIGO_ALIMTALK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const data = await res.json();
    // 알리고 응답 형식: { code: 0(성공)/음수(실패), message, info: { mid, ... } }
    if (data.code === 0) {
      return { status: "sent", provider: "aligo_alimtalk", providerMessageId: String(data.info?.mid || ""), detail: data.message };
    }
    return { status: "failed", provider: "aligo_alimtalk", detail: `[${data.code}] ${data.message}` };
  } catch (e) {
    return { status: "failed", provider: "aligo_alimtalk", detail: e.message };
  }
}

module.exports = { send, isConfigured, channel: "kakao" };
