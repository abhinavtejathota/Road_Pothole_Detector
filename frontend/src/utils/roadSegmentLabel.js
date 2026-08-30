import { ROAD_CLASS, SEGMENT_STATUS } from "../data/surveyConstants";

function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function clean(s) {
  const t = String(s ?? "").trim();
  return t && t !== "nan" && t !== "None" ? t : "";
}

function titleCase(d) {
  const d2 = clean(d);
  if (!d2) return "";
  return d2
    .toLowerCase()
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Human-readable road title + area context for map popups. */
export function roadSegmentDisplay(props = {}) {
  const cls = props.road_class || "other";
  const ref = clean(props.ref);
  const name = clean(props.name);
  const district = titleCase(props.district_name || props.district);
  const place = clean(props.place_name || props.display_name);
  const state = clean(props.state_name) || (props.state_key === "telangana" ? "Telangana" : props.state_key === "andhra" ? "Andhra Pradesh" : "");

  let title;
  switch (cls) {
    case "nh":
      title = ref || name || "National Highway";
      if (ref && name && name.toUpperCase() !== ref.toUpperCase()) title = `${ref} — ${name}`;
      break;
    case "sh":
      title = ref || name || "State Highway";
      if (ref && name && name.toUpperCase() !== ref.toUpperCase()) title = `${ref} — ${name}`;
      break;
    case "mdr":
      title = name || ref || "Major district road";
      break;
    default:
      title = name || ref || "Local road";
      break;
  }

  const area = place
    || [district ? `${district} district` : "", state].filter(Boolean).join(" · ")
    || state
    || "India";

  return { title, area };
}

export function roadSegmentPopupHtml(props = {}) {
  const { title, area } = roadSegmentDisplay(props);
  const road = ROAD_CLASS[props.road_class]?.label || props.road_class || "Road";
  const status = SEGMENT_STATUS[props.status]?.label || props.status || "";
  const km = props.length_km != null ? `${props.length_km} km` : "";

  const meta = [km, status].filter(Boolean).join(" · ");
  const lines = [
    `<strong>${esc(title)}</strong>`,
    esc(area),
    esc(road),
    meta ? esc(meta) : "",
  ].filter(Boolean);

  return lines.join("<br/>");
}
