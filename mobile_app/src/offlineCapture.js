/**
 * Offline / durable field capture store.
 *
 * Layout under FileSystem.documentDirectory:
 *   captures/<sessionId>/meta.json
 *   captures/<sessionId>/chunk_0000.mp4 …
 *   captures/<sessionId>/gps.csv
 *   captures/<sessionId>/frames_log.json   (written at finalize time)
 *
 * Recording is local-first. Upload (when online) does:
 *   init → POST each chunk in order → finalize.
 */
import * as FileSystem from 'expo-file-system/legacy';
import AsyncStorage from '@react-native-async-storage/async-storage';

const ROOT = `${FileSystem.documentDirectory || ''}captures/`;
const INDEX_KEY = 'offline_capture_sessions_v1';

function pad4(n) {
  return String(n).padStart(4, '0');
}

export function sessionDir(sessionId) {
  return `${ROOT}${sessionId}/`;
}

export function chunkPath(sessionId, index) {
  return `${sessionDir(sessionId)}chunk_${pad4(index)}.mp4`;
}

async function readJson(path, fallback = null) {
  try {
    const raw = await FileSystem.readAsStringAsync(path);
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

async function writeJson(path, obj) {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(obj));
}

async function registerSession(sessionId) {
  const raw = await AsyncStorage.getItem(INDEX_KEY);
  let list = [];
  try {
    list = raw ? JSON.parse(raw) : [];
  } catch {
    list = [];
  }
  if (!list.includes(sessionId)) {
    list.unshift(sessionId);
    await AsyncStorage.setItem(INDEX_KEY, JSON.stringify(list.slice(0, 40)));
  }
}

export async function unregisterSession(sessionId) {
  const raw = await AsyncStorage.getItem(INDEX_KEY);
  let list = [];
  try {
    list = raw ? JSON.parse(raw) : [];
  } catch {
    list = [];
  }
  list = list.filter((s) => s !== sessionId);
  await AsyncStorage.setItem(INDEX_KEY, JSON.stringify(list));
}

/**
 * Create (or reopen) a durable capture session on disk.
 * Always call at Start — works offline.
 */
export async function initLocalSession(sessionId, { offline = false } = {}) {
  const dir = sessionDir(sessionId);
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => {});
  const metaPath = `${dir}meta.json`;
  let meta = await readJson(metaPath, null);
  if (!meta) {
    meta = {
      sessionId,
      createdAt: Date.now(),
      offlineStart: Boolean(offline),
      chunks: [],
      uploaded: [],
      status: 'recording',
    };
  } else {
    meta.status = meta.status === 'uploaded' ? meta.status : 'recording';
    meta.offlineStart = meta.offlineStart || Boolean(offline);
  }
  await writeJson(metaPath, meta);
  await registerSession(sessionId);
  return meta;
}

export async function readMeta(sessionId) {
  return readJson(`${sessionDir(sessionId)}meta.json`, null);
}

async function writeMeta(sessionId, meta) {
  await writeJson(`${sessionDir(sessionId)}meta.json`, meta);
}

/** Copy a finished ~60s clip into the session folder and record it in meta. */
export async function saveLocalChunk(sessionId, index, sourceUri) {
  const dir = sessionDir(sessionId);
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => {});
  const dest = chunkPath(sessionId, index);
  const info = await FileSystem.getInfoAsync(dest);
  if (!info.exists) {
    await FileSystem.copyAsync({ from: sourceUri, to: dest });
  }
  const meta = (await readMeta(sessionId)) || {
    sessionId,
    createdAt: Date.now(),
    chunks: [],
    uploaded: [],
    status: 'recording',
  };
  if (!meta.chunks.includes(index)) {
    meta.chunks = [...meta.chunks, index].sort((a, b) => a - b);
  }
  meta.updatedAt = Date.now();
  meta.status = 'pending_upload';
  await writeMeta(sessionId, meta);
  return dest;
}

export async function markChunkUploaded(sessionId, index) {
  const meta = await readMeta(sessionId);
  if (!meta) return;
  if (!meta.uploaded.includes(index)) {
    meta.uploaded = [...meta.uploaded, index].sort((a, b) => a - b);
  }
  meta.updatedAt = Date.now();
  await writeMeta(sessionId, meta);
}

export async function saveGpsCsv(sessionId, csvBodyWithHeader) {
  const path = `${sessionDir(sessionId)}gps.csv`;
  await FileSystem.writeAsStringAsync(path, csvBodyWithHeader);
  return path;
}

export async function saveFrameMeta(sessionId, jsonString) {
  const path = `${sessionDir(sessionId)}frames_log.json`;
  await FileSystem.writeAsStringAsync(path, jsonString);
  return path;
}

export async function listLocalChunks(sessionId) {
  const meta = await readMeta(sessionId);
  const indices = (meta?.chunks || []).slice().sort((a, b) => a - b);
  const out = [];
  for (const i of indices) {
    const path = chunkPath(sessionId, i);
    const info = await FileSystem.getInfoAsync(path);
    if (info.exists) out.push({ index: i, uri: path, uploaded: (meta.uploaded || []).includes(i) });
  }
  return out;
}

export async function listPendingSessions() {
  const raw = await AsyncStorage.getItem(INDEX_KEY);
  let ids = [];
  try {
    ids = raw ? JSON.parse(raw) : [];
  } catch {
    ids = [];
  }
  // Also scan directory for any orphaned sessions
  const names = await FileSystem.readDirectoryAsync(ROOT).catch(() => []);
  for (const n of names) {
    if (n && !ids.includes(n)) ids.push(n);
  }
  const pending = [];
  for (const id of ids) {
    const meta = await readMeta(id);
    if (!meta) continue;
    if (meta.status === 'uploaded') continue;
    const chunks = await listLocalChunks(id);
    if (!chunks.length) continue;
    pending.push({ ...meta, chunkCount: chunks.length });
  }
    pending.sort((a, b) => (a.updatedAt || 0) - (b.updatedAt || 0));
  return pending;
}

export async function discardLocalSession(sessionId) {
  const dir = sessionDir(sessionId);
  try {
    await FileSystem.deleteAsync(dir, { idempotent: true });
  } catch {
    /* ignore */
  }
  await unregisterSession(sessionId);
}

export async function markSessionPending(sessionId) {
  const meta = await readMeta(sessionId);
  if (!meta) return;
  meta.status = 'pending_upload';
  meta.updatedAt = Date.now();
  await writeMeta(sessionId, meta);
}

export async function clearUploadedMarks(sessionId) {
  const meta = await readMeta(sessionId);
  if (!meta) return;
  meta.uploaded = [];
  meta.updatedAt = Date.now();
  await writeMeta(sessionId, meta);
}

export async function markSessionUploaded(sessionId) {
  const meta = await readMeta(sessionId);
  if (meta) {
    meta.status = 'uploaded';
    meta.uploadedAt = Date.now();
    await writeMeta(sessionId, meta);
  }
  await discardLocalSession(sessionId);
}
