/**
 * Citizen reporter API — Bearer JWT to /api/reporter/*
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const TOKEN_KEY = 'sr_reporter_token';
const BASE_KEY = 'sr_reporter_api_base';

const PROD_FALLBACK = 'http://45.194.2.247:5005';
const DEV_FALLBACK = 'http://10.0.2.2:5005';

function defaultBase() {
  const env = (process.env.EXPO_PUBLIC_API_BASE || '').trim();
  if (env) return env.replace(/\/+$/, '');
  return __DEV__ ? DEV_FALLBACK : PROD_FALLBACK;
}

export async function getBaseUrl() {
  return (await AsyncStorage.getItem(BASE_KEY)) || defaultBase();
}

export async function setBaseUrl(url) {
  await AsyncStorage.setItem(BASE_KEY, String(url || '').replace(/\/+$/, ''));
}

export async function getToken() {
  return (await AsyncStorage.getItem(TOKEN_KEY)) || '';
}

export async function setToken(token) {
  if (token) await AsyncStorage.setItem(TOKEN_KEY, token);
  else await AsyncStorage.removeItem(TOKEN_KEY);
}

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

export const api = {
  lookup: (mobile) => request('/api/reporter/auth/lookup', { method: 'POST', body: { mobile } }),
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
  categories: () => request('/api/reporter/categories'),
  trackComplaints: () => request('/api/reporter/trackcomplaint'),
  submitComplaint: (formData) =>
    // Server stores as user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}.jpg|.mp4
    // (see uploadConfig.js + routes/reporter_service.py).
    request('/api/reporter/filecomplaint', { method: 'POST', formData, timeoutMs: 300000 }),
};
