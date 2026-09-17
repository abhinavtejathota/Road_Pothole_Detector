/**
 * Citizen reporter API — Bearer JWT to /api/reporter/*
 *
 * Additions over the original:
 *  - getComplaintById   — fetch a single complaint by ID
 *  - updateProfile      — allow the user to set a display name
 *  - getStats           — reporter-level aggregated statistics
 *  - deleteComplaint    — request deletion of a pending complaint
 *  - Offline draft queue — complaints queued while offline are retried
 *    automatically the next time any API call succeeds.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const TOKEN_KEY = 'sr_reporter_token';
const BASE_KEY = 'sr_reporter_api_base';
const DRAFT_QUEUE_KEY = 'sr_reporter_draft_queue';

const PROD_FALLBACK = 'http://45.194.2.247:5005';
const DEV_FALLBACK = 'http://10.0.2.2:5005';

function defaultBase() {
  const env = (process.env.EXPO_PUBLIC_API_BASE || '').trim();
  if (env) return env.replace(/\/+$/, '');
  return __DEV__ ? DEV_FALLBACK : PROD_FALLBACK;
}

// ---------------------------------------------------------------------------
// Base URL helpers
// ---------------------------------------------------------------------------

export async function getBaseUrl() {
  return (await AsyncStorage.getItem(BASE_KEY)) || defaultBase();
}

export async function setBaseUrl(url) {
  await AsyncStorage.setItem(BASE_KEY, String(url || '').replace(/\/+$/, ''));
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

export async function getToken() {
  return (await AsyncStorage.getItem(TOKEN_KEY)) || '';
}

export async function setToken(token) {
  if (token) await AsyncStorage.setItem(TOKEN_KEY, token);
  else await AsyncStorage.removeItem(TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// Core request
// ---------------------------------------------------------------------------

async function request(path, { method = 'GET', body, formData, timeoutMs = 60000 } = {}) {
  const base = await getBaseUrl();
  const token = await getToken();
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (!formData) headers['Content-Type'] = 'application/json';

  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  let res;
  try {
    res = await fetch(`${base}${path}`, {
      method,
      headers,
      body: formData || (body != null ? JSON.stringify(body) : undefined),
      signal: ctrl?.signal,
    });
  } finally {
    if (timer) clearTimeout(timer);
  }
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : null;
  if (!res.ok) {
    const err = new Error(data?.message || data?.error || res.statusText);
    err.status = res.status;
    err.data = data;
    if (res.status === 401 && !path.includes('/auth/otp/') && !path.includes('/auth/lookup')) {
      await setToken('');
    }
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------------------
// Offline draft queue
// ---------------------------------------------------------------------------

/**
 * Read the draft queue (array of serialised complaint payloads).
 * Each item: { id, defect_type, description, latitude, longitude,
 *               gps_accuracy_m, captured_at, mediaUri, mediaName,
 *               mediaType, queuedAt }
 */
export async function getDraftQueue() {
  try {
    const raw = await AsyncStorage.getItem(DRAFT_QUEUE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export async function saveDraftQueue(queue) {
  await AsyncStorage.setItem(DRAFT_QUEUE_KEY, JSON.stringify(queue));
}

/**
 * Append a draft entry to the queue.
 * Returns the newly assigned draft id.
 */
export async function enqueueDraft(draft) {
  const queue = await getDraftQueue();
  const id = `draft_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  queue.push({ ...draft, id, queuedAt: new Date().toISOString() });
  await saveDraftQueue(queue);
  return id;
}

/** Remove a draft by id (after successful submission). */
export async function removeDraft(id) {
  const queue = await getDraftQueue();
  await saveDraftQueue(queue.filter((d) => d.id !== id));
}

/**
 * Attempt to flush all queued drafts.
 * Returns { submitted: string[], failed: string[] } arrays of draft IDs.
 */
export async function flushDraftQueue() {
  const queue = await getDraftQueue();
  if (!queue.length) return { submitted: [], failed: [] };

  const submitted = [];
  const failed = [];

  for (const draft of queue) {
    try {
      const form = new FormData();
      form.append('defect_type', draft.defect_type);
      if (draft.description) form.append('description', draft.description);
      form.append('latitude', String(draft.latitude));
      form.append('longitude', String(draft.longitude));
      if (draft.gps_accuracy_m != null) form.append('gps_accuracy_m', String(draft.gps_accuracy_m));
      if (draft.captured_at) form.append('captured_at', draft.captured_at);
      if (draft.mediaUri) {
        form.append('media', {
          uri: draft.mediaUri,
          name: draft.mediaName || 'capture.jpg',
          type: draft.mediaType || 'image/jpeg',
        });
      }
      await request('/api/reporter/filecomplaint', {
        method: 'POST',
        formData: form,
        timeoutMs: 300000,
      });
      submitted.push(draft.id);
      await removeDraft(draft.id);
    } catch {
      failed.push(draft.id);
    }
  }
  return { submitted, failed };
}

// ---------------------------------------------------------------------------
// Public API surface
// ---------------------------------------------------------------------------

export const api = {
  // ── Auth ──────────────────────────────────────────────────────────────────
  lookup: (mobile) =>
    request('/api/reporter/auth/lookup', { method: 'POST', body: { mobile } }),

  requestOtp: (mobile, { resend = false } = {}) =>
    request('/api/reporter/auth/otp/request', {
      method: 'POST',
      body: { mobile, resend: !!resend },
    }),

  verifyOtp: (mobile, otp) =>
    request('/api/reporter/auth/otp/verify', { method: 'POST', body: { mobile, otp } }),

  me: () => request('/api/reporter/auth/me'),

  logout: async () => {
    try {
      await request('/api/reporter/auth/logout', { method: 'POST' });
    } catch {
      /* ignore */
    }
    await setToken('');
  },

  // ── Profile ───────────────────────────────────────────────────────────────

  /**
   * Update the reporter's public display name.
   * PATCH /api/reporter/profile
   * Body: { name: string }
   */
  updateProfile: (patch) =>
    request('/api/reporter/profile', { method: 'PATCH', body: patch }),

  /**
   * Fetch reporter-level aggregate statistics.
   * GET /api/reporter/stats
   * Returns: { total, open, in_progress, resolved, rejected }
   */
  getStats: () => request('/api/reporter/stats'),

  // ── Categories ────────────────────────────────────────────────────────────
  categories: () => request('/api/reporter/categories'),

  // ── Complaints ────────────────────────────────────────────────────────────
  trackComplaints: () => request('/api/reporter/trackcomplaint'),

  /**
   * Fetch a single complaint by its integer ID.
   * GET /api/reporter/complaint/{id}
   */
  getComplaintById: (id) => request(`/api/reporter/complaint/${id}`),

  /**
   * Submit a new complaint.
   * Server stores as user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}.jpg|.mp4
   * (see uploadConfig.js + routes/reporter_service.py).
   */
  submitComplaint: (formData) =>
    request('/api/reporter/filecomplaint', { method: 'POST', formData, timeoutMs: 300000 }),

  /**
   * Request deletion of a pending/open complaint.
   * DELETE /api/reporter/complaint/{id}
   * Only complaints in "open" status can be deleted by the reporter.
   */
  deleteComplaint: (id) => request(`/api/reporter/complaint/${id}`, { method: 'DELETE' }),
};
