const BASE = "";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function isRetriableError(err) {
  const s = err?.status;
  if (s === 408 || s === 502 || s === 503 || s === 504) return true;
  const msg = String(err?.message || err || "");
  return /timed out|network|Failed to fetch|Database is busy|Could not reach/i.test(msg);
}

async function requestOnce(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const { headers: optHeaders, timeoutMs, ...rest } = options;
  const ms =
    timeoutMs ??
    (path.includes("/auth/me")
      ? 15000
      : path.includes("/auth/login")
        ? 12000
        : path.includes("/dashboard")
          ? 90000
          : path.includes("/survey/")
            ? 45000
            : 45000);
  const ctrl = typeof AbortController !== "undefined" && ms ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), ms) : null;
  // Keep idle-logout from firing while API work is in flight (dashboard after login).
  try {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event("smartroad:activity"));
    }
  } catch {
    /* ignore */
  }
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: "include",
      ...rest,
      ...(ctrl ? { signal: ctrl.signal } : {}),
      headers: isForm
        ? { ...optHeaders }
        : { "Content-Type": "application/json", ...optHeaders },
    });
  } catch (e) {
    if (e?.name === "AbortError") {
      const err = new Error(`Request timed out after ${Math.round(ms / 1000)}s`);
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
    // Drop zombie browser sessions so the SPA returns to /login instead of retrying forever.
    if (
      res.status === 401 &&
      typeof window !== "undefined" &&
      !path.includes("/auth/login")
    ) {
      try {
        window.dispatchEvent(
          new CustomEvent("smartroad:auth-expired", { detail: { path, message: err.message } })
        );
      } catch {
        /* ignore */
      }
    }
    throw err;
  }
  return data;
}

/** Wait/retry on busy DB — never settle for a blank fail-soft payload. */
async function request(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const { retries, retryDelayMs = 800, noRetry = false, ...rest } = options;
  const max = noRetry
    ? 1
    : Math.max(1, Number(retries ?? (method === "GET" || method === "HEAD" ? 10 : 1)));

  let last;
  for (let i = 0; i < max; i++) {
    try {
      return await requestOnce(path, rest);
    } catch (e) {
      last = e;
      if (e?.status === 401 || e?.status === 403 || e?.status === 404) throw e;
      if (i >= max - 1 || !isRetriableError(e)) throw e;
      try {
        if (typeof window !== "undefined") {
          window.dispatchEvent(new Event("smartroad:activity"));
        }
      } catch {
        /* ignore */
      }
      await sleep(retryDelayMs * (1 + 0.35 * i));
    }
  }
  throw last;
}

export const api = {
  me: () => request("/api/auth/me", { timeoutMs: 15000, retries: 6 }),
  login: (username, password) =>
    request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
      timeoutMs: 12000,
      noRetry: true,
    }),
  logout: () =>
    request("/api/auth/logout", { method: "POST", timeoutMs: 5000, noRetry: true }),

  dashboard: () => request("/api/dashboard", { timeoutMs: 90000, retries: 8 }),
  adminDashboard: (params) => request(`/api/admin-dashboard${params || ""}`, { timeoutMs: 30000, retries: 4 }),
  adminDashboardVideos: (params) =>
    request(`/api/admin-dashboard/videos${params || ""}`, { timeoutMs: 60000, retries: 3 }),
  adminDashboardVideoDetail: (sessionId) =>
    request(`/api/admin-dashboard/videos/${sessionId}`, { timeoutMs: 45000, retries: 3 }),
  adminDashboardVideoRequeue: (sessionId) =>
    request(`/api/admin-dashboard/videos/${sessionId}/requeue`, {
      method: "POST",
      timeoutMs: 120000,
      noRetry: true,
    }),
  adminDashboardMembers: (params) =>
    request(`/api/admin-dashboard/members${params || ""}`, { timeoutMs: 45000, retries: 3 }),
  adminDashboardKmCoverage: (params) =>
    request(`/api/admin-dashboard/km-coverage${params || ""}`, { timeoutMs: 30000, retries: 3 }),
  adminDashboardKmCoverageClass: (roadClass, params) =>
    request(`/api/admin-dashboard/km-coverage/${encodeURIComponent(roadClass)}${params || ""}`, {
      timeoutMs: 90000,
      retries: 2,
    }),
  adminDashboardPotholes: (params) =>
    request(`/api/admin-dashboard/potholes${params || ""}`, { timeoutMs: 30000, retries: 3 }),
  adminDashboardPotholeDetails: (params) =>
    request(`/api/admin-dashboard/potholes/details${params || ""}`, { timeoutMs: 45000, retries: 3 }),
  adminDashboardPotholeImagesZipUrl: (params) =>
    `/api/admin-dashboard/potholes/images-zip${params || ""}`,
  downloadAdminPotholeImages: async (params) => {
    const url = `/api/admin-dashboard/potholes/images-zip${params || ""}`;
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) {
      let msg = `Download failed (${res.status})`;
      try {
        const body = await res.json();
        if (body?.error) msg = body.error;
      } catch {
        /* ignore */
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(cd);
    const filename = m ? decodeURIComponent(m[1].replace(/"/g, "")) : "pothole_frames.zip";
    const a = document.createElement("a");
    const href = URL.createObjectURL(blob);
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(href);
  },
  vgDetails: () => request("/api/vg-details", { timeoutMs: 30000, retries: 4 }),
  createVg: (body) =>
    request("/api/vg-details", {
      method: "POST",
      body: JSON.stringify(body || {}),
      noRetry: true,
    }),
  upsertVgDetails: (userId, body) =>
    request(`/api/vg-details/${userId}`, {
      method: "PUT",
      body: JSON.stringify(body || {}),
      noRetry: true,
    }),
  mapData: () => request("/api/dashboard/map-data", { timeoutMs: 90000, retries: 6 }),

  users: () => request("/api/users", { timeoutMs: 60000, retries: 8 }),
  createUser: (body) =>
    request("/api/users", { method: "POST", body: JSON.stringify(body), noRetry: true }),
  updateUserDistricts: (userId, body) =>
    request(`/api/users/${userId}/districts`, {
      method: "PATCH",
      body: JSON.stringify(body),
      noRetry: true,
    }),
  updateUserState: (userId, body) =>
    request(`/api/users/${userId}/state`, {
      method: "PATCH",
      body: JSON.stringify(body),
      noRetry: true,
    }),
  deleteUser: (userId) =>
    request(`/api/users/${userId}`, { method: "DELETE", noRetry: true }),
  updateMyDistricts: (districtIds) =>
    request("/api/survey/my-districts", {
      method: "PATCH",
      body: JSON.stringify({ district_ids: districtIds }),
      timeoutMs: 300000,
      noRetry: true,
    }),
  clearMySurveyAssignment: () =>
    request("/api/survey/assignments/clear", {
      method: "POST",
      body: JSON.stringify({}),
      noRetry: true,
    }),

  vendors: (params = {}) =>
    request(`/api/vendors?${new URLSearchParams(params)}`, { timeoutMs: 60000, retries: 8 }),
  vendor: (id) => request(`/api/vendors/${id}`, { timeoutMs: 60000, retries: 6 }),
  createVendor: (body) =>
    request("/api/vendors", { method: "POST", body: JSON.stringify(body), noRetry: true }),
  updateVendor: (id, body) =>
    request(`/api/vendors/${id}`, { method: "PUT", body: JSON.stringify(body), noRetry: true }),
  deleteVendor: (id) =>
    request(`/api/vendors/${id}`, { method: "DELETE", noRetry: true }),
  suggestVendors: (sessionId) =>
    request(`/api/vendors/suggest?session_id=${sessionId}`, { retries: 4 }),

  tasks: (params = {}) =>
    request(`/api/tasks?${new URLSearchParams(params)}`, { timeoutMs: 90000, retries: 8 }),
  task: (id) => request(`/api/tasks/${id}`, { timeoutMs: 60000, retries: 6 }),
  createTask: (sessionId) =>
    request(`/api/tasks/create/${sessionId}`, { method: "POST", noRetry: true }),
  deleteTask: (id) =>
    request(`/api/tasks/${id}`, { method: "DELETE", noRetry: true }),
  allocateTask: (id, body) =>
    request(`/api/tasks/${id}/allocate`, {
      method: "POST",
      body: JSON.stringify(body),
      noRetry: true,
    }),
  updateTaskStatus: (id, body) =>
    request(`/api/tasks/${id}/status`, {
      method: "POST",
      body: JSON.stringify(body),
      noRetry: true,
    }),
  potholes: (sessionId) => request(`/api/tasks/potholes/${sessionId}`, { retries: 6 }),

  validateContext: (woId) => request(`/api/validate/${woId}/context`, { retries: 6 }),
  validateUpload: (woId, formData) =>
    request(`/api/validate/${woId}`, { method: "POST", body: formData, noRetry: true }),
  reviewQueue: () => request("/api/validate/queue", { retries: 6 }),
  reviewValidation: (valId, body) =>
    request(`/api/validate/review/${valId}`, {
      method: "POST",
      body: JSON.stringify(body),
      noRetry: true,
    }),

  surveyStates: () => request("/api/survey/states"),
  surveyDistricts: (stateKey) => {
    const q = new URLSearchParams();
    if (stateKey) q.set("state_key", stateKey);
    const qs = q.toString();
    return request(`/api/survey/districts${qs ? `?${qs}` : ""}`);
  },
  surveyRoadLengths: (stateKey, districtId) => {
    const q = new URLSearchParams();
    if (stateKey) q.set("state_key", stateKey);
    if (districtId) q.set("district_id", districtId);
    const qs = q.toString();
    return request(`/api/survey/road-lengths${qs ? `?${qs}` : ""}`);
  },
  surveyNhOverview: (stateKey) =>
    request(`/api/survey/nh-overview?${new URLSearchParams({ state_key: stateKey })}`),
  surveySegments: (stateKey, districtId, { classes = "" } = {}) => {
    const q = new URLSearchParams({ state_key: stateKey, district_id: districtId });
    if (classes) q.set("classes", classes);
    return request(`/api/survey/segments?${q}`);
  },
  surveyGeocode: (q, { stateKey, districtId, districtIds } = {}) => {
    const params = new URLSearchParams({ q });
    if (stateKey) params.set("state_key", stateKey);
    if (districtId) params.set("district_id", districtId);
    if (districtIds?.length) params.set("district_ids", districtIds.join(","));
    return request(`/api/survey/geocode?${params}`);
  },
  surveyLocate: (lat, lon, { stateKey, districtId, reverse = false } = {}) => {
    const params = new URLSearchParams({ lat, lon, reverse: reverse ? "1" : "0" });
    if (stateKey) params.set("state_key", stateKey);
    if (districtId) params.set("district_id", districtId);
    return request(`/api/survey/locate?${params}`);
  },
  surveyReverseGeocode: (lat, lon, { preferPlaces = false } = {}) => {
    const params = new URLSearchParams({ lat, lon });
    if (preferPlaces) params.set("prefer_places", "1");
    return request(`/api/survey/reverse-geocode?${params}`);
  },
  surveySettings: () => request("/api/survey/settings"),
  updateSurveySettings: (body) => request("/api/survey/settings", { method: "PUT", body: JSON.stringify(body) }),
  surveyAssignments: (userId, date) => {
    const q = new URLSearchParams();
    if (userId) q.set("user_id", userId);
    if (date) q.set("date", date);
    const qs = q.toString();
    return request(`/api/survey/assignments${qs ? `?${qs}` : ""}`);
  },
  surveyAssignmentGeoJson: (userId, date) => {
    const q = new URLSearchParams();
    if (userId) q.set("user_id", userId);
    if (date) q.set("date", date);
    const qs = q.toString();
    return request(`/api/survey/assignments/geojson${qs ? `?${qs}` : ""}`);
  },
  generateMySurveyAssignment: (body) =>
    request("/api/survey/assign", { method: "POST", body: JSON.stringify(body || {}) }),
  previewSurveyRoutes: (body) =>
    request("/api/survey/routes/preview", { method: "POST", body: JSON.stringify(body || {}) }),
  clearSurveyAssignment: (userId, date) =>
    request("/api/survey/assignments/clear", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, date }),
    }),
  reopenDistrictRoads: (districtId, stateKey) =>
    request(`/api/survey/districts/${encodeURIComponent(districtId)}/reopen-roads`, {
      method: "POST",
      body: JSON.stringify({ state_key: stateKey }),
    }),

  detectionStatus: () => request("/api/detection/status"),
  detectionS3Catalog: (source = "videographer") =>
    request(`/api/detection/s3/catalog?${new URLSearchParams({ source })}`),
  detectionS3Keys: (source = "videographer") =>
    request(`/api/detection/s3/keys?${new URLSearchParams({ source })}`),
  detectionS3Delete: (s3Key, source) =>
    request("/api/detection/s3", {
      method: "DELETE",
      body: JSON.stringify({ s3_key: s3Key, source: source || undefined }),
    }),
  detectionS3Preview: (s3Key) =>
    request(`/api/detection/s3/preview?${new URLSearchParams({ s3_key: s3Key || "" })}`),
  // YOLO can take minutes on long video — no client abort; wait until the server finishes.
  detectionRun: (formData) =>
    request("/api/detection/run", {
      method: "POST",
      body: formData,
      timeoutMs: 0,
      noRetry: true,
    }),
  detectionSessions: (source) => {
    const q = source ? `?${new URLSearchParams({ source })}` : "";
    return request(`/api/detection/sessions${q}`);
  },
  detectionSessionPotholes: (sessionId) => request(`/api/detection/sessions/${sessionId}/potholes`),
  detectionSessionDetail: (sessionId) => request(`/api/detection/sessions/${sessionId}/detail`),

  complaints: (params = {}) =>
    request(`/api/complaints?${new URLSearchParams(params)}`, { timeoutMs: 60000, retries: 6 }),
  verifyComplaint: (id) =>
    request(`/api/complaints/${id}/verify`, { method: "POST", noRetry: true }),
  rejectComplaint: (id, body = {}) =>
    request(`/api/complaints/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
      noRetry: true,
    }),

  reportsList: () => request("/api/reports"),
  reportsOpen: (sessionId) => request(`/api/reports/${sessionId}/open`),
  reportsGenerate: (sessionId) =>
    request(`/api/reports/${sessionId}/generate`, { method: "POST", timeoutMs: 180000 }),

  modelBenchModels: () => request("/api/model-bench/models"),
  modelBenchRunImage: (formData) =>
    request("/api/model-bench/run-image", {
      method: "POST",
      body: formData,
      timeoutMs: 0,
      noRetry: true,
    }),
  modelBenchRunFrame: (formData) =>
    request("/api/model-bench/run-frame", {
      method: "POST",
      body: formData,
      timeoutMs: 0,
      noRetry: true,
    }),
  modelBenchRunVideo: (formData) =>
    request("/api/model-bench/run-video", {
      method: "POST",
      body: formData,
      timeoutMs: 0,
      noRetry: true,
    }),
  modelBenchDiscardVideo: (token) =>
    token
      ? request(`/api/model-bench/video/${encodeURIComponent(token)}`, { method: "DELETE", noRetry: true })
      : request("/api/model-bench/video", { method: "DELETE", noRetry: true }),

  uploadStatus: () => request("/api/upload/status"),
  uploadField: (formData, { onProgress, timeoutMs = 600000 } = {}) =>
    new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/api/upload`);
      xhr.withCredentials = true;
      xhr.timeout = timeoutMs;
      xhr.upload.onprogress = (ev) => {
        if (!onProgress) return;
        if (ev.lengthComputable && ev.total > 0) {
          onProgress({
            fraction: ev.loaded / ev.total,
            percent: Math.round((ev.loaded / ev.total) * 100),
            loaded: ev.loaded,
            total: ev.total,
          });
        }
      };
      xhr.onload = () => {
        let data = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch {
          data = { error: xhr.responseText || "Invalid response" };
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
          return;
        }
        const err = new Error(data?.message || data?.error || data?.status?.message || xhr.statusText);
        err.status = xhr.status;
        err.data = data;
        reject(err);
      };
      xhr.onerror = () => reject(new Error("Network error during upload"));
      xhr.ontimeout = () => reject(new Error(`Upload timed out after ${Math.round(timeoutMs / 1000)}s`));
      xhr.send(formData);
    }),
  uploadKeys: () => request("/api/upload/keys"),

  trackingPing: (body) =>
    request("/api/tracking/ping", { method: "POST", body: JSON.stringify(body) }),
  trackingDiscardSession: (captureSessionId) =>
    request("/api/tracking/discard-session", {
      method: "POST",
      body: JSON.stringify({ capture_session_id: captureSessionId }),
    }),
  trackingVideographers: (date) => {
    const q = date ? `?${new URLSearchParams({ date })}` : "";
    return request(`/api/tracking/videographers${q}`);
  },
  trackingVideographer: (userId, date) => {
    const q = date ? `?${new URLSearchParams({ date })}` : "";
    return request(`/api/tracking/videographers/${userId}${q}`);
  },
  autoTrackGps: (body) =>
    request("/api/auto-track/gps", { method: "POST", body: JSON.stringify(body || {}) }),
  autoTrackUploads: (userId, date) => {
    const q = new URLSearchParams();
    if (userId) q.set("user_id", userId);
    if (date) q.set("date", date);
    const qs = q.toString();
    return request(`/api/auto-track/uploads${qs ? `?${qs}` : ""}`);
  },
};
