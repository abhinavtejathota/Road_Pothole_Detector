import { useEffect } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import L from "leaflet";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import { STATE_META, MAP_TILE_URL, MAP_TILE_ATTRIBUTION } from "../data/surveyConstants";

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({ iconRetinaUrl: markerIcon2x, iconUrl: markerIcon, shadowUrl: markerShadow });

const COLORS = { High: "#b3261e", Medium: "#c98a1a", Low: "#1e7a4c" };

export default function PotholeMap({ points = [], height = 380 }) {
  const center = STATE_META.andhra.center;
  const zoom = STATE_META.andhra.zoom;

  useEffect(() => {}, [points]);

  if (!points.length) {
    return <div className="map-box empty" style={{ height }}>No map data</div>;
  }

  return (
    <div className="map-box" style={{ height }}>
      <MapContainer center={center} zoom={zoom} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
        <TileLayer url={MAP_TILE_URL} attribution={MAP_TILE_ATTRIBUTION} />
        {points.filter((p) => p.lat && p.lon).map((p, i) => (
          <CircleMarker
            key={i}
            center={[p.lat, p.lon]}
            radius={9}
            pathOptions={{
              color: "#16202a",
              weight: 2,
              fillColor: COLORS[p.severity] || "#8a97a3",
              fillOpacity: 0.85,
            }}
          >
            <Popup>
              <strong>{p.severity || "—"}</strong>
              {(p.work_status || p.status) && <><br />{p.work_status || p.status}</>}
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}

