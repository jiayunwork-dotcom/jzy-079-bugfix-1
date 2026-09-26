// 与后端通信的客户端：HTTP 请求 + SSE 实时推送
// 所有接口地址都走同源 /api，由 nginx 反代到后端

export const WINDOWS = [
  { key: '1h', label: '最近一小时' },
  { key: '1d', label: '最近一天' },
];

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* 忽略解析失败 */
    }
    throw new Error(detail);
  }
  return resp.json();
}

export function listTraces({ traceId, service } = {}) {
  const params = new URLSearchParams();
  if (traceId) params.set('trace_id', traceId);
  if (service) params.set('service', service);
  return request(`/api/traces?${params.toString()}`);
}

export function getTrace(traceId) {
  return request(`/api/traces/${encodeURIComponent(traceId)}`);
}

export function getGraph(windowKey) {
  return request(`/api/graph?window=${encodeURIComponent(windowKey)}`);
}

export function submitSpans(spans) {
  return request('/api/spans', {
    method: 'POST',
    body: JSON.stringify({ spans }),
  });
}

/**
 * 订阅后端 SSE 推送。返回带 close() 的句柄。
 * @param {Record<string, (data:any)=>void>} handlers 事件名到回调的映射
 */
export function subscribeEvents(handlers) {
  const source = new EventSource('/api/stream');
  for (const [eventName, handler] of Object.entries(handlers)) {
    source.addEventListener(eventName, (evt) => {
      let data = {};
      try {
        data = JSON.parse(evt.data);
      } catch {
        /* ping 等空事件忽略 */
      }
      handler(data);
    });
  }
  return { close: () => source.close() };
}
