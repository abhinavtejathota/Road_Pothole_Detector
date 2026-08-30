/** Shared survey UI constants (district-based Andhra / Telangana). */

export const INDIA_BOUNDS = [[8.0, 68.5], [35.5, 96.5]];
export const INDIA_CENTER = [22.5, 79.0];
/** Country overview — India fills the frame (avoid showing most of Asia). */
export const INDIA_ZOOM = 5.5;

export const STATE_META = {
  andhra: {
    id: 1,
    key: "andhra",
    name: "Andhra Pradesh",
    // Post-bifurcation AP focus (tighter north so Telangana is not framed).
    bounds: [[12.75, 77.0], [19.05, 84.75]],
    center: [15.95, 79.95],
    zoom: 7.2,
  },
  telangana: {
    id: 2,
    key: "telangana",
    name: "Telangana",
    bounds: [[15.85, 77.25], [19.9, 81.8]],
    center: [17.9, 79.5],
    zoom: 8,
  },
};

export const MAP_TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
export const MAP_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>';

export const ROAD_CLASS = {
  nh: { label: "National highway", color: "#dc2626", order: 1 },
  sh: { label: "State highway", color: "#ea580c", order: 2 },
  mdr: { label: "MDR", color: "#ca8a04", order: 3 },
  other: { label: "Local roads", color: "#475569", order: 4 },
};

export const ASSIGNMENT_COLOR = "#6366f1";
export const COMPLETED_COLOR = "#9ca3af";

export const SEGMENT_STATUS = {
  available: { label: "Not assigned", color: "#d4d4d4" },
  assigned: { label: "Assigned today", color: ASSIGNMENT_COLOR },
  pending_approval: { label: "Pending supervisor", color: "#f97316" },
  approved: { label: "Completed", color: COMPLETED_COLOR },
  completed: { label: "Already covered", color: COMPLETED_COLOR },
  verified: { label: "Verified covered", color: COMPLETED_COLOR },
};

export function todayIST() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}
