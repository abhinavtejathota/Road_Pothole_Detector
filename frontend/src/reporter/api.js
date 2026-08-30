const BASE = "";
const TOKEN_KEY = "sr_reporter_token";

export function getReporterToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setReporterToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

async function reporterRequest(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const token = getReporterToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const timeoutMs = options.timeoutMs ?? 60000;
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: "omit",
      ...options,
      ...(ctrl ? { signal: ctrl.signal } : {}),
      headers: isForm
        ? headers
        : { "Content-Type": "application/json", ...headers },
    });
  } catch (e) {
    if (e?.name === "AbortError") {
      const err = new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
      err.status = 408;
      throw err;
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }

  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const err = new Error(data?.message || data?.error || res.statusText);
    err.data = data;
    err.status = res.status;
    if (res.status === 401 && !path.includes("/auth/otp/") && !path.includes("/auth/lookup")) {
      setReporterToken(null);
    }
    throw err;
  }
  return data;
}

export const reporterApi = {
  categories: () => reporterRequest("/api/reporter/categories"),

  lookup: (mobile) =>
    reporterRequest("/api/reporter/auth/lookup", {
      method: "POST",
      body: JSON.stringify({ mobile }),
    }),

  requestOtp: (mobile, { resend = false } = {}) =>
    reporterRequest("/api/reporter/auth/otp/request", {
      method: "POST",
      body: JSON.stringify({ mobile, resend: !!resend }),
    }),

  verifyOtp: (mobile, otp) =>
    reporterRequest("/api/reporter/auth/otp/verify", {
      method: "POST",
      body: JSON.stringify({ mobile, otp }),
    }),

  me: () => reporterRequest("/api/reporter/auth/me"),

  logout: async () => {
    try {
      await reporterRequest("/api/reporter/auth/logout", { method: "POST" });
    } catch {
      /* ignore */
    }
    setReporterToken(null);
  },

  submitComplaint: (formData) =>
    reporterRequest("/api/reporter/filecomplaint", {
      method: "POST",
      body: formData,
      timeoutMs: 300000,
    }),

  trackComplaints: () => reporterRequest("/api/reporter/trackcomplaint"),
};
