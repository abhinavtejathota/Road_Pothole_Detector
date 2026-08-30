/**
 * Field / videographer upload layout (must stay in sync with
 * routes/field_upload_service.py).
 *
 * S3 input bucket:
 *   videographer/{username}/{start}_{end}-{YYYYMMDD-HHMM}/{sha256}.mp4
 *   + sibling .csv / _log.json next to the media object
 *
 * Server renames the assembled video to a content hash; clients send stable
 * local names and pass start/end labels so the route folder is correct.
 */

export const S3_VG_ROOT = 'videographer';

export const MEDIA_VIDEO_NAME = 'capture.mp4';
export const MEDIA_GPS_NAME = 'capture.csv';
export const MEDIA_FRAMES_NAME = 'capture_log.json';

/** Build multipart fields the Flask finalize/upload handlers expect. */
export function routeUploadFields(summary) {
  const start =
    (summary?.start && summary.start.label)
    || (summary?.legs?.[0]?.start && summary.legs[0].start.label)
    || '';
  const end =
    (summary?.end && summary.end.label)
    || (() => {
      const legs = summary?.legs || [];
      const last = legs[legs.length - 1];
      return last?.end?.label || '';
    })()
    || '';
  const out = {};
  if (String(start).trim()) out.start_label = String(start).trim();
  if (String(end).trim()) out.end_label = String(end).trim();
  return out;
}

/** Sibling names for a capture stem (local / form only — S3 uses hash.mp4). */
export function fieldSiblingNames(stem = 'capture') {
  const base = String(stem || 'capture').replace(/\.[^.]+$/, '') || 'capture';
  return {
    mediaName: `${base}.mp4`,
    gpsName: `${base}.csv`,
    frameMetaName: `${base}_log.json`,
  };
}

/** Append route + capture_session fields onto a FormData body. */
export function appendFieldUploadMeta(form, {
  captureSessionId,
  startLabel,
  endLabel,
  routeLabel,
} = {}) {
  if (captureSessionId) form.append('capture_session_id', String(captureSessionId));
  if (routeLabel) form.append('route_label', String(routeLabel));
  if (startLabel) form.append('start_label', String(startLabel));
  if (endLabel) form.append('end_label', String(endLabel));
  return form;
}
