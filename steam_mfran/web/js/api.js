// REST helper + reconnecting event stream.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (form) opts.body = form;
  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch (e) {
    throw new ApiError("Cannot reach the MFRAN server. Is it still running?", 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new ApiError(detail || `Request failed (HTTP ${res.status})`, res.status);
  }
  return data;
}

export const post = (path, body) => api(path, { method: "POST", body: body ?? {} });
export const put = (path, body) => api(path, { method: "PUT", body });

/** Open /api/events and keep it open. */
export function connectEvents(onEvent, onStatus) {
  let ws;
  let delay = 500;
  let stopped = false;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/events`);
    ws.onopen = () => {
      delay = 500;
      onStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch (e) {
        console.error("bad event", e);
      }
    };
    ws.onclose = () => {
      onStatus(false);
      if (!stopped) setTimeout(open, delay);
      delay = Math.min(delay * 2, 8000);
    };
  };
  open();
  return () => {
    stopped = true;
    ws && ws.close();
  };
}
