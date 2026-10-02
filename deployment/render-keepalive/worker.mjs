export function isOfficeTime(timestamp) {
  const parts = new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Kathmandu', hour: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(timestamp));
  const hour = Number(parts.find(part => part.type === 'hour').value);
  return hour >= 9 && hour < 18;
}

export function healthUrl(value, path) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.onrender.com') || url.username || url.password || url.port || url.pathname !== path || url.search || url.hash) {
    throw new Error('Configure an HTTPS onrender.com health endpoint without credentials or query parameters.');
  }
  return url.href;
}

export async function checkServices(timestamp, env, request = fetch) {
  if (env.ENABLED !== 'true' || !isOfficeTime(timestamp)) return [];
  const targets = [
    ['ConsMan', healthUrl(env.CONSMAN_HEALTH_URL, '/api/v1/health/')],
    ['Gateway', healthUrl(env.GATEWAY_HEALTH_URL, '/api/health')],
  ];
  return Promise.all(targets.map(async ([service, url]) => {
    try {
      const response = await request(url, {method: 'GET', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(90000)});
      const result = {service, status: response.status, ok: response.ok};
      await response.body?.cancel();
      console.log(JSON.stringify(result));
      return result;
    } catch {
      const result = {service, ok: false};
      console.error(JSON.stringify(result));
      return result;
    }
  }));
}

export default {
  scheduled(controller, env, context) {
    context.waitUntil(checkServices(controller.scheduledTime, env));
  },
  fetch() {
    return new Response('ConsMan office-hours health checks', {headers: {'Cache-Control': 'no-store'}});
  },
};
