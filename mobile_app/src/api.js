/**
 * Mobile API client — Bearer JWT (X-Client: mobile). Web still uses cookies.
 *
 * Defaults:
 *   - Local/dev (`expo start`, Expo Go): EXPO_PUBLIC_API_BASE_DEV (LAN IP)
 *   - Release APK: EXPO_PUBLIC_API_BASE_PROD (server IP)
 *
 * Set in mobile_app/.env â€” see .env.example. Login can still override.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Network from 'expo-network';
import * as FileSystem from 'expo-file-system/legacy';
import {
  appendFieldUploadMeta,
  fieldSiblingNames,
  routeUploadFields,
} from './uploadConfig';

const BASE_KEY = 'api_base_url';
const BASE_PIN_KEY = 'api_base_url_pin';
const TOKEN_KEY = 'access_token';
const USER_CACHE_KEY = 'cached_user_v1';
const COOKIE_KEY = 'session_cookie'; // legacy — always cleared

const PROD_FALLBACK = 'http://45.194.2.247:5005';
/** Android emulator â†’ host machine; physical device needs LAN IP in .env */
const DEV_FALLBACK = 'http://10.0.2.2:5000';

function resolveDefaultBase() {
  const envDev = (process.env.EXPO_PUBLIC_API_BASE_DEV || '').trim();
  const envProd = (process.env.EXPO_PUBLIC_API_BASE_PROD || '').trim();
  const envAny = (process.env.EXPO_PUBLIC_API_BASE || '').trim();

  if (__DEV__) {
    return envDev || envAny || DEV_FALLBACK;
  }
  return envProd || envAny || PROD_FALLBACK;
}

export const DEFAULT_BASE = resolveDefaultBase();
export const IS_DEV_API = Boolean(__DEV__);

/** Bump when defaults change so stale AsyncStorage resets to this build's default. */
const BASE_PIN = `v5-jwt-${__DEV__ ? 'dev' : 'prod'}-${DEFAULT_BASE}`;

let memoryToken = '';

export async function getBaseUrl() {
  const pin = await AsyncStorage.getItem(BASE_PIN_KEY);
  if (pin !== BASE_PIN) {
    await AsyncStorage.setItem(BASE_KEY, DEFAULT_BASE);
    await AsyncStorage.setItem(BASE_PIN_KEY, BASE_PIN);
    return DEFAULT_BASE;
  }
  return (await AsyncStorage.getItem(BASE_KEY)) || DEFAULT_BASE;
}

export async function setBaseUrl(url) {
  const clean = String(url || '').replace(/\/+$/, '');
  await AsyncStorage.setItem(BASE_KEY, clean);
  await AsyncStorage.setItem(BASE_PIN_KEY, BASE_PIN);
}

export async function resetBaseUrlToDefault() {
  await AsyncStorage.setItem(BASE_KEY, DEFAULT_BASE);
  await AsyncStorage.setItem(BASE_PIN_KEY, BASE_PIN);
  return DEFAULT_BASE;
}

/**
 * Fail fast (no waiting on a TCP timeout) when there's clearly no network,
 * instead of every screen discovering it 60â€“120s later via a hung fetch.
 * Fails "open" (assume online) if the OS API itself errors, so a flaky
 * network-state check never blocks a request that might actually work.
 */
export async function isOnline() {
  try {
    const state = await Network.getNetworkStateAsync();
    if (state?.isConnected === false) return false;
    if (state?.isInternetReachable === false) return false;
    return true;
  } catch {
    return true;
  }
}

const OFFLINE_MESSAGE = 'No internet connection â€” connect to Wiâ€‘Fi or mobile data and try again.';

async function loadToken() {
  if (memoryToken) return memoryToken;
  memoryToken = (await AsyncStorage.getItem(TOKEN_KEY)) || '';
  return memoryToken;
}

async function saveToken(value) {
  memoryToken = value || '';
  if (value) await AsyncStorage.setItem(TOKEN_KEY, value);
  else await AsyncStorage.removeItem(TOKEN_KEY);
  await AsyncStorage.removeItem(COOKIE_KEY);
  if (!value) await AsyncStorage.removeItem(USER_CACHE_KEY);
}

async function saveUserCache(user) {
  if (user && typeof user === 'object') {
    await AsyncStorage.setItem(USER_CACHE_KEY, JSON.stringify(user));
  }
}

async function loadUserCache() {
  try {
    const raw = await AsyncStorage.getItem(USER_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function authHeaders(token, extra = {}) {
  const h = { 'X-Client': 'mobile', ...extra };
  if (token) {
    h.Authorization = `Bearer ${token}`;
    h['X-Access-Token'] = token;
  }
  return h;
}

async function ensureAuthed() {
  const token = await loadToken();
  if (!token) {
    throw new Error('Not logged in — open Login and sign in again, then retry Upload.');
  }
  try {
    await request('/api/auth/me', { timeoutMs: 15000 });
  } catch (e) {
    if (e?.status === 401 || /unauthor/i.test(String(e?.message || ''))) {
      await saveToken('');
      throw new Error('Session expired — log in again on the phone, then retry Upload.');
    }
    throw e;
  }
}

async function request(path, { method = 'GET', body, query, timeoutMs, skipAuth = false } = {}) {
  if (!(await isOnline())) {
    throw new Error(OFFLINE_MESSAGE);
  }
  const base = await getBaseUrl();
  const token = skipAuth ? '' : await loadToken();
  let url = `${base}${path}`;
  if (query) {
    const qs = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v != null && v !== '') qs.append(k, String(v));
    });
    const s = qs.toString();
    if (s) url += `?${s}`;
  }
  const headers = {
    Accept: 'application/json',
    Connection: 'close',
    ...authHeaders(token, body ? { 'Content-Type': 'application/json' } : {}),
  };
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const isUploadApi = String(path).includes('/upload');
  const isRouteApi = String(path).includes('/routes/');
  const ms =
    timeoutMs
    ?? (isUploadApi ? 180000 : isRouteApi && method === 'POST' ? 120000 : 45000);
  const timer = ctrl ? setTimeout(() => ctrl.abort(), ms) : null;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
  } catch (e) {
    const aborted = e?.name === 'AbortError';
    if (aborted) {
      throw new Error(
        `Request timed out after ${Math.round(ms / 1000)}s waiting for ${base}${path}. `
        + 'If this is Upload: stay on the same Wiâ€‘Fi as the PC, confirm Flask is running, '
        + 'and that the phone is not on mobile data. Server may also be busy â€” retry once.',
      );
    }
    throw new Error(
      `${e?.message || 'Network request failed'} â€” could not reach ${base}. `
      + 'Check Wiâ€‘Fi (same LAN as the PC) and that the server allows HTTP.',
    );
  } finally {
    if (timer) clearTimeout(timer);
  }

  let data = null;
  const text = await res.text();
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text || 'Invalid response' };
  }
  if (!res.ok) {
    const err = new Error(data?.message || data?.error || data?.status?.message || `HTTP ${res.status}`);
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Cheap reachability probe before long uploads (fails fast if LAN IP is unreachable). */
export async function ensureServerReachable(timeoutMs = 8000) {
  if (!(await isOnline())) {
    throw new Error(OFFLINE_MESSAGE);
  }
  const base = await getBaseUrl();
  const token = await loadToken();
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    await fetch(`${base}/api/auth/me`, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        ...authHeaders(token),
      },
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
    // 401/200 both mean TCP+HTTP reached Flask
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error(
        `Cannot reach server at ${base} (no response in ${Math.round(timeoutMs / 1000)}s). `
        + 'Join the same Wiâ€‘Fi as your PC, open that URL in the phone browser, '
        + 'and confirm python web_app.py is running.',
      );
    }
    throw new Error(
      `Cannot reach server at ${base}. ${e?.message || 'Network error'}. `
      + 'Same Wiâ€‘Fi as the PC is required for this LAN address.',
    );
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Classic multipart/form POST through Flask (PC then pushes to S3). Best for short clips. */
async function uploadFieldClassic({
  mediaUri,
  mediaName,
  gpsUri,
  gpsName,
  frameMetaUri,
  frameMetaName,
  captureSessionId,
  startLabel,
  endLabel,
  routeLabel,
}) {
  const base = await getBaseUrl();
  const token = await loadToken();
  const form = new FormData();
  form.append('media', { uri: mediaUri, name: mediaName || 'capture.mp4', type: 'video/mp4' });
  form.append('gps_log', { uri: gpsUri, name: gpsName || 'capture.csv', type: 'text/csv' });
  if (frameMetaUri) {
    form.append('frame_meta', {
      uri: frameMetaUri,
      name: frameMetaName || 'capture_log.json',
      type: 'application/json',
    });
  }
  appendFieldUploadMeta(form, {
    captureSessionId,
    startLabel,
    endLabel,
    routeLabel,
  });
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), 60 * 60 * 1000) : null;
  let res;
  try {
    res = await fetch(`${base}/api/upload`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        ...authHeaders(token),
        // Do NOT set Connection: close or Content-Type — RN sets multipart boundary;
        // Connection:close has interrupted body uploads on some Android stacks.
      },
      body: form,
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error(
        'Upload timed out after 60 minutes. Confirm Flask is running and the phone is on the same Wi-Fi, then retry.',
      );
    }
    throw new Error(
      `${e?.message || 'Upload failed'} — could not reach ${base}. Stay on the same Wi-Fi as the PC.`,
    );
  } finally {
    if (timer) clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.status?.kind === 'error') {
    throw new Error(data?.status?.message || data?.error || `Upload failed (HTTP ${res.status})`);
  }
  return data;
}

export const api = {
  login: async (username, password) => {
    // Drop prior JWT so login never re-auth's as the old user via Authorization.
    await saveToken('');
    const data = await request('/api/auth/login', {
      method: 'POST',
      body: { username, password },
      skipAuth: true,
    });
    const tok = data?.access_token || '';
    await saveToken(tok);
    if (!tok) {
      throw new Error('Login ok but no access_token — update server for mobile JWT.');
    }
    // Sanity: server must return the account we asked for.
    if (data?.username && String(data.username).toLowerCase() !== String(username).trim().toLowerCase()) {
      await saveToken('');
      throw new Error(`Login returned ${data.username} instead of ${username} — try again.`);
    }
    await saveUserCache(data);
    return data;
  },
  me: async () => {
    const token = await loadToken();
    if (!token) throw new Error('Not logged in');
    if (!(await isOnline())) {
      const cached = await loadUserCache();
      if (cached) return cached;
      throw new Error(OFFLINE_MESSAGE);
    }
    try {
      const data = await request('/api/auth/me');
      await saveUserCache(data);
      return data;
    } catch (e) {
      if (e?.status === 401) throw e;
      const cached = await loadUserCache();
      if (cached) return cached;
      throw e;
    }
  },
  logout: async () => {
    try {
      await request('/api/auth/logout', { method: 'POST' });
    } catch (_) {
      /* ignore */
    }
    await saveToken('');
  },
  assignmentSummary: () =>
    request('/api/survey/assignments', { timeoutMs: 45000 }),
  assignmentGeoJson: () =>
    request('/api/survey/assignments/geojson', { timeoutMs: 45000 }),
  geocode: (q, { stateKey, districtIds, discover = true } = {}) =>
    request('/api/survey/geocode', {
      query: {
        q,
        state_key: stateKey,
        district_ids: (districtIds || []).join(','),
        discover: discover ? '1' : '0',
      },
      timeoutMs: 15000,
    }),
  locate: (lat, lon, { stateKey, reverse = false } = {}) =>
    request('/api/survey/locate', {
      query: {
        lat,
        lon,
        reverse: reverse ? '1' : '0',
        ...(stateKey ? { state_key: stateKey } : {}),
      },
      timeoutMs: 12000,
    }),
  reverseGeocode: (lat, lon) =>
    request('/api/survey/reverse-geocode', {
      query: { lat, lon },
      timeoutMs: 10000,
    }),
  previewRoutes: (body) =>
    request('/api/survey/routes/preview', { method: 'POST', body, timeoutMs: 120000 }),
  assignCorridor: (body) =>
    request('/api/survey/assign', {
      method: 'POST',
      body: { mode: 'corridor', ...body },
      timeoutMs: 120000,
    }),
  assignCustomRoute: (body) =>
    request('/api/survey/assign', {
      method: 'POST',
      body: { mode: 'auto_track', ...body },
      timeoutMs: 120000,
    }),
  surveyDistricts: (stateKey) =>
    request('/api/survey/districts', { query: { state_key: stateKey }, timeoutMs: 60000 }),
  updateMyDistricts: (districtIds) =>
    request('/api/survey/my-districts', {
      method: 'PATCH',
      body: { district_ids: districtIds },
      timeoutMs: 300000,
    }),
  clearMySurveyAssignment: (date) =>
    request('/api/survey/assignments/clear', {
      method: 'POST',
      body: date ? { date } : {},
    }),
  trackingPing: (body) =>
    request('/api/tracking/ping', { method: 'POST', body, timeoutMs: 8000 }).catch(() => null),
  trackingDiscardSession: (captureSessionId) =>
    request('/api/tracking/discard-session', {
      method: 'POST',
      body: { capture_session_id: captureSessionId },
      timeoutMs: 15000,
    }).catch(() => null),

  uploadSessionInit: async (captureSessionId) => {
    if (!(await isOnline())) throw new Error(OFFLINE_MESSAGE);
    await ensureServerReachable(10000);
    await ensureAuthed();
    return request('/api/upload/session/init', {
      method: 'POST',
      body: { capture_session_id: captureSessionId },
      timeoutMs: 30000,
    });
  },

  uploadSessionChunk: async ({
    captureSessionId,
    mediaUri,
    mediaName,
    chunkIndex,
    lat,
    lon,
    accuracy,
    onProgress,
  }) => {
    if (!(await isOnline())) throw new Error(OFFLINE_MESSAGE);
    // Native binary stream → /chunk-bin (no multipart encode on phone).
    // Falls back to FormData /chunk if an older server lacks chunk-bin.
    const base = await getBaseUrl();
    const token = await loadToken();
    if (!token) {
      throw new Error('Session expired — log in again on the phone, then retry Upload.');
    }
    let timeoutMs = 5 * 60 * 1000;
    try {
      const net = await Network.getNetworkStateAsync();
      if (net?.type === Network.NetworkStateType.CELLULAR) {
        timeoutMs = 12 * 60 * 1000;
      }
    } catch {
      /* keep default */
    }

    const headers = {
      Accept: 'application/json',
      'Content-Type': 'video/mp4',
      'X-Capture-Session-Id': String(captureSessionId),
      'X-Chunk-Index': String(chunkIndex),
      ...authHeaders(token),
    };
    if (lat != null && lon != null) {
      headers['X-Lat'] = String(lat);
      headers['X-Lon'] = String(lon);
      if (accuracy != null) headers['X-Accuracy'] = String(accuracy);
    }
    try {
      const info = await FileSystem.getInfoAsync(mediaUri);
      if (info?.exists && info.size != null) {
        headers['Content-Length'] = String(info.size);
      }
    } catch {
      /* server can still stream without CL via safe_fallback=False */
    }

    const reportBytes = (sent, total) => {
      if (!onProgress || !total) return;
      try {
        onProgress({
          fraction: Math.min(1, Math.max(0, sent / total)),
          bytesSent: sent,
          bytesTotal: total,
        });
      } catch {
        /* ignore UI errors */
      }
    };

    let useMultipartFallback = false;
    try {
      let uploaded;
      if (typeof FileSystem.createUploadTask === 'function') {
        const task = FileSystem.createUploadTask(
          `${base}/api/upload/session/chunk-bin`,
          mediaUri,
          {
            httpMethod: 'POST',
            uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
            headers,
            sessionType: FileSystem.FileSystemSessionType.BACKGROUND,
          },
          (prog) => {
            reportBytes(
              prog.totalBytesSent || 0,
              prog.totalBytesExpectedToSend || 0,
            );
          },
        );
        uploaded = await task.uploadAsync();
      } else {
        uploaded = await FileSystem.uploadAsync(
          `${base}/api/upload/session/chunk-bin`,
          mediaUri,
          {
            httpMethod: 'POST',
            uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
            headers,
            sessionType: FileSystem.FileSystemSessionType.BACKGROUND,
          },
        );
        reportBytes(1, 1);
      }
      let data = {};
      try {
        data = uploaded.body ? JSON.parse(uploaded.body) : {};
      } catch {
        data = {};
      }
      const emptyBody =
        uploaded.status === 400
        && /empty chunk/i.test(String(data?.status?.message || data?.error || ''));
      if (uploaded.status === 404 || uploaded.status === 405 || emptyBody) {
        useMultipartFallback = true;
      } else {
        if (uploaded.status === 503 || data?.retry_after) {
          const wait = Number(data?.retry_after || 5) * 1000;
          await new Promise((r) => setTimeout(r, Math.min(20000, Math.max(3000, wait))));
          throw new Error(data?.status?.message || 'Server busy — will retry');
        }
        if (uploaded.status === 401) {
          await saveToken('');
          throw new Error(
            data?.message || data?.error || 'Session expired — log in again on the phone, then retry Upload.',
          );
        }
        if (uploaded.status < 200 || uploaded.status >= 300 || data?.ok === false) {
          throw new Error(
            data?.status?.message || data?.message || data?.error
              || `Chunk upload failed (HTTP ${uploaded.status})`,
          );
        }
        return data;
      }
    } catch (e) {
      if (!useMultipartFallback) throw e;
    }

    if (!useMultipartFallback) {
      throw new Error('Chunk upload failed');
    }

    const form = new FormData();
    form.append('capture_session_id', captureSessionId);
    form.append('chunk_index', String(chunkIndex));
    if (lat != null && lon != null) {
      form.append('lat', String(lat));
      form.append('lon', String(lon));
      if (accuracy != null) form.append('accuracy', String(accuracy));
    }
    form.append('media', {
      uri: mediaUri,
      name: mediaName || `chunk_${chunkIndex}.mp4`,
      type: 'video/mp4',
    });
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const res = await fetch(`${base}/api/upload/session/chunk`, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          ...authHeaders(token),
        },
        body: form,
        ...(ctrl ? { signal: ctrl.signal } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        await saveToken('');
        throw new Error(
          data?.message || data?.error || 'Session expired — log in again on the phone, then retry Upload.',
        );
      }
      if (res.status === 503 || data?.retry_after) {
        const wait = Number(data?.retry_after || res.headers?.get?.('Retry-After') || 5) * 1000;
        await new Promise((r) => setTimeout(r, Math.min(20000, Math.max(3000, wait))));
        throw new Error(data?.status?.message || 'Server busy — will retry');
      }
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.status?.message || data?.error || `Chunk upload failed (HTTP ${res.status})`);
      }
      reportBytes(1, 1);
      return data;
    } finally {
      if (timer) clearTimeout(timer);
    }
  },

  uploadSessionFinalize: async ({
    captureSessionId,
    gpsUri,
    gpsName,
    frameMetaUri,
    frameMetaName,
    startLabel,
    endLabel,
    routeLabel,
  }) => {
    if (!(await isOnline())) throw new Error(OFFLINE_MESSAGE);
    await ensureServerReachable(10000);
    await ensureAuthed();
    const base = await getBaseUrl();
    const token = await loadToken();
    const form = new FormData();
    form.append('capture_session_id', captureSessionId);
    form.append('gps_log', {
      uri: gpsUri,
      name: gpsName || 'capture.csv',
      type: 'text/csv',
    });
    if (frameMetaUri) {
      form.append('frame_meta', {
        uri: frameMetaUri,
        name: frameMetaName || 'capture_log.json',
        type: 'application/json',
      });
    }
    appendFieldUploadMeta(form, {
      startLabel,
      endLabel,
      routeLabel,
    });
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 3 * 60 * 1000) : null;
    try {
      const res = await fetch(`${base}/api/upload/session/finalize`, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          ...authHeaders(token),
        },
        body: form,
        ...(ctrl ? { signal: ctrl.signal } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        await saveToken('');
        throw new Error(
          data?.message || data?.error || 'Session expired — log in again on the phone, then retry Upload.',
        );
      }
      if (!res.ok || data?.status?.kind === 'error') {
        throw new Error(data?.status?.message || data?.error || `Finalize failed (HTTP ${res.status})`);
      }
      return data;
    } finally {
      if (timer) clearTimeout(timer);
    }
  },

  uploadSessionDiscard: (captureSessionId) =>
    request('/api/upload/session/discard', {
      method: 'POST',
      body: { capture_session_id: captureSessionId },
      timeoutMs: 30000,
    }).catch(() => null),

  /**
   * Field upload — always phone → Flask → S3 (server boto3 multipart).
   * Direct phone→S3 removed (unreliable on field networks).
   */
  uploadField: async ({
    mediaUri,
    mediaName,
    gpsUri,
    gpsName,
    frameMetaUri,
    frameMetaName,
    captureSessionId,
    sizeBytes,
    startLabel,
    endLabel,
    routeLabel,
  }) => {
    if (!(await isOnline())) {
      throw new Error(OFFLINE_MESSAGE);
    }
    await ensureAuthed();
    await ensureServerReachable(10000);
    const names = fieldSiblingNames(mediaName || 'capture.mp4');
    let route = { startLabel, endLabel, routeLabel };
    if (!route.startLabel && !route.endLabel && !route.routeLabel) {
      try {
        const summary = await api.assignmentSummary();
        const fields = routeUploadFields(summary);
        route = {
          startLabel: fields.start_label,
          endLabel: fields.end_label,
          routeLabel: fields.route_label,
        };
      } catch {
        /* optional — server falls back to today's assignment */
      }
    }
    return uploadFieldClassic({
      mediaUri,
      mediaName: mediaName || names.mediaName,
      gpsUri,
      gpsName: gpsName || names.gpsName,
      frameMetaUri,
      frameMetaName: frameMetaName || names.frameMetaName,
      captureSessionId,
      startLabel: route.startLabel,
      endLabel: route.endLabel,
      routeLabel: route.routeLabel,
    });
  },
};

