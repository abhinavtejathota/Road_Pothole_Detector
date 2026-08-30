import { useEffect, useRef } from "react";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MAP_TILE_URL, MAP_TILE_ATTRIBUTION, INDIA_BOUNDS, INDIA_CENTER, INDIA_ZOOM } from "../data/surveyConstants";

function FitBounds({ bounds, fitKey, zoom, center }) {
  const map = useMap();
  const fittedRef = useRef(null);

  useEffect(() => {
    const key = String(fitKey ?? "default");
    if (fittedRef.current === key) return;
    fittedRef.current = key;
    try {
      if (bounds?.length === 2) {
        // Prefer center+zoom for state views so AP fills the frame without TG.
        if (center && zoom != null && zoom <= 8) {
          map.setView(center, zoom, { animate: false });
        } else {
          map.fitBounds(bounds, { padding: [20, 20], maxZoom: zoom ?? 10 });
        }
      } else if (center) {
        map.setView(center, zoom ?? INDIA_ZOOM);
      } else {
        map.setView(INDIA_CENTER, INDIA_ZOOM);
      }
    } catch {
      /* ignore bad bounds */
    }
  }, [map, bounds, fitKey, zoom, center]);

  return null;
}

/** Base map only — no roads, potholes, or overlays. */
export default function PlainMap({
  center = INDIA_CENTER,
  zoom = INDIA_ZOOM,
  bounds = null,
  fitKey = null,
  height = 420,
  emptyMessage = "Select a state or district.",
}) {
  if (!bounds && !fitKey) {
    return (
      <div className="map-box empty" style={{ height }}>
        {emptyMessage}
      </div>
    );
  }

  const mapBounds = bounds || INDIA_BOUNDS;

  return (
    <div className="map-box survey-map-wrap" style={{ height }}>
      <MapContainer center={center} zoom={zoom} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
        <TileLayer url={MAP_TILE_URL} attribution={MAP_TILE_ATTRIBUTION} />
        <FitBounds bounds={mapBounds} fitKey={fitKey} zoom={zoom} center={center} />
      </MapContainer>
    </div>
  );
}
