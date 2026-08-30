/** In-memory GeoJSON segment cache — avoids re-downloading the same district/class set. */

const cache = new Map();

export function segmentCacheKey(stateKey, districtId, classes) {
  return `${stateKey}|${districtId}|${classes || "all"}`;
}

export function getCachedSegments(key) {
  return cache.get(key) || null;
}

export function setCachedSegments(key, data) {
  if (data) cache.set(key, data);
  return data;
}

/** Drop all cached GIS payloads (call on logout / login switch). */
export function clearSegmentCache() {
  cache.clear();
}

export async function fetchSegmentsCached(api, stateKey, districtId, classes) {
  const key = segmentCacheKey(stateKey, districtId, classes);
  const hit = getCachedSegments(key);
  if (hit) return hit;
  const data = await api.surveySegments(stateKey, districtId, { classes });
  return setCachedSegments(key, data);
}
