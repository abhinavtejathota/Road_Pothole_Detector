/**
 * Citizen complaint GPS meta — matches mobile field ``*_log.json`` /
 * routes/reporter_service.build_citizen_frame_meta.
 */

export const MEDIA_META_NAME = "capture_log.json";

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
    country: "India",
    country_code: "IN",
    source: "citizen",
    media_kind: isVideo ? "video" : "photo",
    captured_at: capturedAt || new Date().toISOString(),
    frames: [frame],
  };
  if (defectType) payload.defect_type = String(defectType).trim();
  if (description) payload.description = String(description).trim();
  return payload;
}

/** Browser File for multipart ``frame_meta``. */
export function citizenFrameMetaFile(meta) {
  const body = JSON.stringify(meta, null, 2);
  return new File([body], MEDIA_META_NAME, { type: "application/json" });
}
