/**
 * formatters.js — display formatting utilities for the reporter app.
 *
 * All functions are pure and return strings. They never throw; on bad input
 * they return a safe fallback string.
 */

// ---------------------------------------------------------------------------
// Date / time
// ---------------------------------------------------------------------------

/**
 * Format an ISO timestamp or Date into a human-readable local string.
 * e.g. "12 Jul 2025, 10:30 AM"
 */
export function formatDateTime(value) {
  if (!value) return '—';
  try {
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return String(value);
  }
}

/**
 * Format only the date portion.
 * e.g. "12 Jul 2025"
 */
export function formatDate(value) {
  if (!value) return '—';
  try {
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  } catch {
    return String(value);
  }
}

/**
 * Returns a relative time string: "just now", "5 min ago", "2 days ago", etc.
 * Falls back to formatDateTime if the date is older than 7 days.
 */
export function formatRelativeTime(value) {
  if (!value) return '—';
  try {
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);

    const diffMs = Date.now() - d.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHr / 24);

    if (diffSec < 30) return 'just now';
    if (diffSec < 90) return '1 min ago';
    if (diffMin < 60) return `${diffMin} min ago`;
    if (diffHr < 2) return '1 hour ago';
    if (diffHr < 24) return `${diffHr} hours ago`;
    if (diffDay === 1) return 'yesterday';
    if (diffDay < 7) return `${diffDay} days ago`;
    return formatDate(d);
  } catch {
    return String(value);
  }
}

// ---------------------------------------------------------------------------
// GPS coordinates
// ---------------------------------------------------------------------------

/**
 * Format lat/lon pair to a human-readable string.
 * e.g. "17.38500°N, 78.48600°E"
 */
export function formatCoords(latitude, longitude, { decimals = 5 } = {}) {
  if (latitude == null || longitude == null) return 'Unknown location';
  const lat = Number(latitude);
  const lon = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return 'Invalid location';
  const latDir = lat >= 0 ? 'N' : 'S';
  const lonDir = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(decimals)}°${latDir}, ${Math.abs(lon).toFixed(decimals)}°${lonDir}`;
}

/**
 * Format GPS accuracy.
 * e.g. "±12 m" or "±0.3 km"
 */
export function formatAccuracy(meters) {
  if (meters == null || !Number.isFinite(Number(meters))) return '';
  const m = Math.round(Number(meters));
  if (m < 1000) return `±${m} m`;
  return `±${(m / 1000).toFixed(1)} km`;
}

/**
 * Returns a short quality label for GPS accuracy.
 */
export function gpsQualityLabel(meters) {
  if (meters == null || !Number.isFinite(Number(meters))) return 'Unknown';
  const m = Number(meters);
  if (m <= 10) return 'Excellent';
  if (m <= 30) return 'Good';
  if (m <= 100) return 'Fair';
  return 'Poor';
}

// ---------------------------------------------------------------------------
// Complaint status
// ---------------------------------------------------------------------------

const STATUS_LABELS = {
  open: 'Open',
  in_progress: 'In Progress',
  resolved: 'Resolved',
  rejected: 'Rejected',
  pending: 'Pending Review',
  closed: 'Closed',
};

/** Human-readable label for a status code. */
export function formatStatus(status) {
  if (!status) return 'Unknown';
  return STATUS_LABELS[String(status).toLowerCase()] || toTitleCase(status);
}

/**
 * Returns a colour pair { bg, fg } for a status badge.
 * Uses a palette consistent with the existing app design.
 */
export function statusColors(status) {
  switch (String(status || '').toLowerCase()) {
    case 'open':
      return { bg: '#dbeafe', fg: '#1d4ed8' };
    case 'in_progress':
      return { bg: '#fef9c3', fg: '#854d0e' };
    case 'resolved':
      return { bg: '#dcfce7', fg: '#15803d' };
    case 'rejected':
      return { bg: '#fee2e2', fg: '#b91c1c' };
    case 'closed':
      return { bg: '#f1f5f9', fg: '#475569' };
    default:
      return { bg: '#e2e8f0', fg: '#334155' };
  }
}

// ---------------------------------------------------------------------------
// File size
// ---------------------------------------------------------------------------

/**
 * Format bytes into a readable file size string.
 * e.g. "2.3 MB"
 */
export function formatFileSize(bytes) {
  if (bytes == null || !Number.isFinite(Number(bytes))) return '';
  const b = Number(bytes);
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// ---------------------------------------------------------------------------
// Tracking number
// ---------------------------------------------------------------------------

/**
 * Truncate a long tracking number for compact display.
 * e.g. "TN-20250712-0001" → shown as-is; longer SHA-like strings → truncated.
 */
export function formatTrackingNumber(tn) {
  if (!tn) return '—';
  const s = String(tn);
  if (s.length <= 20) return s;
  return `${s.slice(0, 8)}…${s.slice(-6)}`;
}

// ---------------------------------------------------------------------------
// Miscellaneous
// ---------------------------------------------------------------------------

function toTitleCase(str) {
  return String(str)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Convert snake_case or camelCase defect type to display text. */
export function formatDefectType(raw) {
  if (!raw) return '—';
  return toTitleCase(String(raw));
}

/** Format phone number for display: "+91 98765 43210" */
export function formatMobile(digits) {
  const d = String(digits || '').replace(/\D/g, '').slice(-10);
  if (d.length !== 10) return digits ? `+91 ${digits}` : '—';
  return `+91 ${d.slice(0, 5)} ${d.slice(5)}`;
}
