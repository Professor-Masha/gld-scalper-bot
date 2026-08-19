export class ApiClient {
  constructor(token) { this.token = token; }
  async request(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (options.method && options.method !== "GET") config.headers["X-Dashboard-Token"] = this.token;
    if (options.body) { config.headers["Content-Type"] = "application/json"; config.body = JSON.stringify(options.body); }
    const response = await fetch(path, config);
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    if (!response.ok) throw new Error(payload.detail || "Request failed");
    return payload;
  }
  start(action, options = {}) { return this.request(`/api/processes/${action}/start`, { method: "POST", body: { options } }); }
  stop(action) { return this.request(`/api/processes/${action}/stop`, { method: "POST" }); }
}
