/**
 * validators.js — form and data validation utilities for the reporter app.
 *
 * All validators return { valid: boolean, message: string }.
 * Pure functions — no side effects, easily unit-testable.
 */

// ---------------------------------------------------------------------------
// Mobile number
// ---------------------------------------------------------------------------

/** Strip non-digit chars and normalise to 10-digit Indian national number. */
export function normaliseIndianMobile(raw) {
  let digits = String(raw || '').replace(/\D/g, '');
  // Strip country-code prefixes
  if (digits.startsWith('0091') && digits.length >= 14) digits = digits.slice(4);
  else if (digits.startsWith('91') && digits.length === 12) digits = digits.slice(2);
  else if (digits.startsWith('0') && digits.length === 11) digits = digits.slice(1);
  return digits.slice(0, 10);
}

/** Indian mobile numbers start with 6-9 and are exactly 10 digits. */
export function validateMobile(raw) {
  const digits = normaliseIndianMobile(raw);
  if (!digits) return { valid: false, message: 'Mobile number is required.' };
  if (!/^[6-9]\d{9}$/.test(digits)) {
    return { valid: false, message: 'Enter a valid 10-digit Indian mobile number (starts with 6-9).' };
  }
  return { valid: true, message: '' };
}

// ---------------------------------------------------------------------------
// OTP
// ---------------------------------------------------------------------------

const OTP_MIN_LENGTH = 4;
const OTP_MAX_LENGTH = 8;

export function validateOtp(raw) {
  const clean = String(raw || '').replace(/\D/g, '');
  if (!clean) return { valid: false, message: 'OTP is required.' };
  if (clean.length < OTP_MIN_LENGTH) {
    return { valid: false, message: `OTP must be at least ${OTP_MIN_LENGTH} digits.` };
  }
  if (clean.length > OTP_MAX_LENGTH) {
    return { valid: false, message: `OTP must be at most ${OTP_MAX_LENGTH} digits.` };
  }
  return { valid: true, message: '' };
}

// ---------------------------------------------------------------------------
// Complaint form
// ---------------------------------------------------------------------------

/** Validates the defect/category field. */
export function validateDefectType(value) {
  if (!value || !String(value).trim()) {
    return { valid: false, message: 'Please select a category.' };
  }
  return { valid: true, message: '' };
}

/** Validates the optional description field (max length guard). */
export function validateDescription(value, { maxLength = 1000 } = {}) {
  const str = String(value || '').trim();
  if (str.length > maxLength) {
    return { valid: false, message: `Description must be ${maxLength} characters or fewer (currently ${str.length}).` };
  }
  return { valid: true, message: '' };
}

/** Validates that GPS coords exist and are in the plausible India bounding-box. */
export function validateGps(coords) {
  if (!coords || coords.latitude == null || coords.longitude == null) {
    return { valid: false, message: 'GPS location is required. Please wait for location fix.' };
  }
  const lat = Number(coords.latitude);
  const lon = Number(coords.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return { valid: false, message: 'Invalid GPS coordinates.' };
  }
  // Rough India bounding box — skipped in __DEV__ so emulators (0,0) still work.
  if (!__DEV__ && (lat < 6.5 || lat > 37.5 || lon < 68.0 || lon > 97.5)) {
    return {
      valid: false,
      message: `GPS coordinates (${lat.toFixed(4)}, ${lon.toFixed(4)}) appear to be outside India.`,
    };
  }
  return { valid: true, message: '' };
}

/** GPS accuracy threshold check — warn (not error) if worse than threshold. */
export function gpsAccuracyWarning(coords, { thresholdMeters = 50 } = {}) {
  if (!coords || coords.accuracy == null) return '';
  const acc = Number(coords.accuracy);
  if (Number.isFinite(acc) && acc > thresholdMeters) {
    return `Low GPS accuracy (±${Math.round(acc)} m). Move to open sky for a better fix.`;
  }
  return '';
}

/** Validates that a media asset has been selected. */
export function validateMediaAsset(asset) {
  if (!asset || !asset.uri) {
    return { valid: false, message: 'Please capture a photo or select one from your gallery.' };
  }
  return { valid: true, message: '' };
}

/**
 * Validates an entire complaint form in one call.
 * Returns an array of error strings; empty array means form is valid.
 */
export function validateComplaintForm({ defectType, description, gps, asset }) {
  const errors = [];
  const defectResult = validateDefectType(defectType);
  if (!defectResult.valid) errors.push(defectResult.message);

  const gpsResult = validateGps(gps);
  if (!gpsResult.valid) errors.push(gpsResult.message);

  const mediaResult = validateMediaAsset(asset);
  if (!mediaResult.valid) errors.push(mediaResult.message);

  const descResult = validateDescription(description);
  if (!descResult.valid) errors.push(descResult.message);

  return errors;
}

// ---------------------------------------------------------------------------
// URL / API base
// ---------------------------------------------------------------------------

export function validateApiBaseUrl(raw) {
  const url = String(raw || '').trim();
  if (!url) return { valid: false, message: 'API base URL is required.' };
  if (!/^https?:\/\/.+/.test(url)) {
    return { valid: false, message: 'URL must start with http:// or https://' };
  }
  if (url.endsWith('/')) {
    return { valid: false, message: 'URL must not end with a trailing slash.' };
  }
  return { valid: true, message: '' };
}
