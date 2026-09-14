// gateways/push.js — 앱 푸시 발송 (Firebase Cloud Messaging, Legacy HTTP API 기준)
//
// 세그먼트 전체에게 한 번에 보내야 하므로, 고객마다 디바이스 토큰을 관리하는 대신
// "고객이 가입 시 자신의 세그먼트 토픽을 구독한다"는 전제로 토픽 발송 방식을 사용합니다.
// (예: growth 세그먼트 고객 앱이 /topics/segment_growth 를 구독)
//
// 참고: Google은 Legacy HTTP API를 더 이상 신규 프로젝트에 권장하지 않습니다.
// 신규로 시작하신다면 FCM HTTP v1 API + 서비스 계정 인증으로 구현하시는 걸 권장합니다.
// (v1은 OAuth2 토큰 발급이 필요해 google-auth-library 등 별도 의존성이 필요합니다.)
//
// 필요 환경변수: FCM_SERVER_KEY

const FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send";

function isConfigured() {
  return !!process.env.FCM_SERVER_KEY;
}

/**
 * @param {object} payload
 * @param {string} payload.segmentId  'growth' | 'stable' | 'dormant'
 * @param {string} payload.title
 * @param {string} payload.message
 */
async function send(payload) {
  if (!isConfigured()) {
    return { status: "simulated", provider: "fcm", detail: "FCM_SERVER_KEY가 설정되지 않아 실제 발송 대신 시뮬레이션했습니다." };
  }

  const body = {
    to: `/topics/segment_${payload.segmentId}`,
    notification: { title: payload.title || "알림", body: payload.message },
  };

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(FCM_LEGACY_URL, {
      method: "POST",
      headers: {
        "Authorization": `key=${process.env.FCM_SERVER_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const data = await res.json();
    if (data.success >= 1) {
      return { status: "sent", provider: "fcm", providerMessageId: String(data.multicast_id || ""), detail: `success=${data.success}` };
    }
    return { status: "failed", provider: "fcm", detail: JSON.stringify(data.results || data) };
  } catch (e) {
    return { status: "failed", provider: "fcm", detail: e.message };
  }
}

module.exports = { send, isConfigured, channel: "push" };
