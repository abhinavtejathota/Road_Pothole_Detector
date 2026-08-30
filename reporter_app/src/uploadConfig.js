/**
 * Citizen / reporter upload layout (must stay in sync with
 * routes/reporter_service.py).
 *
 * S3 (reporter bucket, usually same as input):
 *   user/{mobileno}/{YYYYMMDD}_{HHMM}/{sha256}.jpg|.mp4
 *   user/{mobileno}/{YYYYMMDD}_{HHMM}/{sha256}_log.json   ← required GPS meta
 *
 * Server hashes media and always writes the JSON sibling (client JSON preferred;
 * otherwise synthesized from form GPS).
 */

export const S3_USER_ROOT = 'user';

export const MEDIA_PHOTO_NAME = 'capture.jpg';
export const MEDIA_VIDEO_NAME = 'capture.mp4';
export const MEDIA_META_NAME = 'capture_log.json';

export function isVideoAsset(asset) {
  const mime = String(asset?.mimeType || asset?.type || '').toLowerCase();
  const name = String(asset?.fileName || asset?.name || '').toLowerCase();
  if (mime.startsWith('video/')) return true;
  return /\.(mp4|mov|mkv|avi)$/i.test(name);
}

/** Local multipart filename — server renames to {sha256}.jpg|.mp4. */
export function mediaNameForAsset(asset) {
  if (isVideoAsset(asset)) return MEDIA_VIDEO_NAME;
  return MEDIA_PHOTO_NAME;
}

export function mediaMimeForAsset(asset) {
  if (asset?.mimeType) return asset.mimeType;
  return isVideoAsset(asset) ? 'video/mp4' : 'image/jpeg';
}

/** FormData part for /api/reporter/filecomplaint media field. */
export function mediaFormPart(asset) {
  return {
    uri: asset.uri,
    name: mediaNameForAsset(asset),
    type: mediaMimeForAsset(asset),
  };
}

/**
 * Mobile-style GPS frame meta (same shape as videographer ``*_log.json``).
 * One frame for a photo / capture point; videos use the same until denser GPS exists.
 */
export function buildCitizenFrameMeta({
  latitude,
  longitude,
  accuracy,
  defectType,
  description,
  capturedAt,
  isVideo = false,
} = {}) {
  const lat = Number(latitude);
  const lon = Number(longitude);
  const frame = {
    frame: 0,
    video_second: 0,
    t_ms: 0,
    lat,
    lon,
  };
  if (accuracy != null && Number.isFinite(Number(accuracy))) {
    frame.accuracy_m = Number(accuracy);
  }
  const payload = {
    assumed_fps: 30,
    country: 'India',
    country_code: 'IN',
    source: 'citizen',
    media_kind: isVideo ? 'video' : 'photo',
    captured_at: capturedAt || new Date().toISOString(),
    frames: [frame],
  };
  if (defectType) payload.defect_type = String(defectType).trim();
  if (description) payload.description = String(description).trim();
  return payload;
}
