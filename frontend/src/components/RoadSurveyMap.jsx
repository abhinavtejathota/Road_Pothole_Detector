import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, CircleMarker, Popup, Polyline, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../api";
import { ROAD_CLASS, SEGMENT_STATUS, STATE_META, MAP_TILE_URL, MAP_TILE_ATTRIBUTION, ASSIGNMENT_COLOR, COMPLETED_COLOR } from "../data/surveyConstants";
import { roadSegmentPopupHtml } from "../utils/roadSegmentLabel";

const POTHOLE_COLORS = { High: "#b3261e", Medium: "#c98a1a", Low: "#1e7a4c" };
const HIDDEN = { opacity: 0, weight: 0, interactive: false };
const TRAIL_COLOR = "#0ea5e9";
/** Break GPS trail only on real teleports — match backend TRAIL_JUMP_KM (1.25).
 *  0.25 was too aggressive for ~20–60s pings and made cyan look disjointed. */
const TRAIL_JUMP_KM = 1.25;
/** Match backend END_PIN_REACH_KM — grey ends / cyan continues after this. */
const END_PIN_REACH_KM = 0.25;
/** Tracking only: below this zoom, continents dominate — snap back to route. */
const TRACKING_MIN_ZOOM = 4;
const reverseCache = new Map();
/** Wider click tolerance around thin road strokes (Canvas renderer). */
const roadRenderer = L.canvas({ tolerance: 16, padding: 0.5 });

function validLatLon(lat, lon) {
  const la = Number(lat);
  const lo = Number(lon);
  return Number.isFinite(la) && Number.isFinite(lo) && la >= -90 && la <= 90 && lo >= -180 && lo <= 180;
}

function safeLatLng(lat, lon) {
  return validLatLon(lat, lon) ? [Number(lat), Number(lon)] : null;
}

function haversineKm(a, b) {
  const r = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const lat1 = toRad(a[0]);
  const lat2 = toRad(b[0]);
  const x = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(x));
}

/** Cyan from the closest approach to the end pin onward (grey covers up to that point). */
function trailSegmentsAfterEnd(trail, endLatLon, radiusKm = END_PIN_REACH_KM) {
  if (!endLatLon) return trailSegmentsFromPoints(trail);
  const pts = (trail || [])
    .map((p) => {
      if (p == null) return null;
      const ll = safeLatLng(p.lat, p.lon ?? p.lng);
      if (!ll) return null;
      return { ll, ts: p.ts || "", raw: p };
    })
    .filter(Boolean);
  // Keep capture order — do not re-sort across the whole trail (breaks segment gaps).
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < pts.length; i += 1) {
    const d = haversineKm(pts[i].ll, endLatLon);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  if (best < 0 || bestD > radiusKm) return trailSegmentsFromPoints(trail);
  return trailSegmentsFromPoints(pts.slice(best).map((p) => p.raw));
}

/** Split trail into road-following segments; drop straight teleports across the city.
 *  ``null`` / missing lat-lon entries are treated as hard segment breaks (from API). */
function trailSegmentsFromPoints(trail, jumpKm = TRAIL_JUMP_KM) {
  const segments = [];
  let cur = [];

  const flush = () => {
    if (cur.length >= 2) segments.push(cur);
    cur = [];
  };

  const pts = trail || [];
  for (const p of pts) {
    if (p == null || p === false) {
      flush();
      continue;
    }
    const ll = safeLatLng(p.lat, p.lon ?? p.lng);
    if (!ll) {
      flush();
      continue;
    }
    if (cur.length && haversineKm(cur[cur.length - 1], ll) > jumpKm) {
      flush();
      cur = [ll];
    } else {
      cur.push(ll);
    }
  }
  flush();
  return segments;
}

function driverIcon(live) {
  const color = live ? "#E8B40A" : "#6b7280";
  return L.divIcon({
    className: "tracking-driver-icon",
    html: `<div class="tracking-driver-pin${live ? " is-live" : ""}" style="--pin:${color}">
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path fill="currentColor" d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1a2.5 2.5 0 0 1-5 0H10a2.5 2.5 0 0 1-5 0H4a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1zm2.5 5.5a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm9 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM8.4 7l-1 3h9.2l-1-3H8.4z"/>
      </svg>
    </div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  });
}

function TrailPane() {
  const map = useMap();
  useEffect(() => {
    if (!map.getPane("gpsTrail")) {
      map.createPane("gpsTrail");
      const pane = map.getPane("gpsTrail");
      pane.style.zIndex = 650;
      pane.style.pointerEvents = "none";
    }
    // Grey covered path sits above cyan so it is visible until the end buffer
    if (!map.getPane("coveredTrail")) {
      map.createPane("coveredTrail");
      const pane = map.getPane("coveredTrail");
      pane.style.zIndex = 660;
      pane.style.pointerEvents = "none";
    }
  }, [map]);
  return null;
}

function ZoomWatcher({ onZoom }) {
  const map = useMap();
  useEffect(() => {
    const update = () => onZoom(map.getZoom());
    map.on("zoomend", update);
    update();
    return () => map.off("zoomend", update);
  }, [map, onZoom]);
  return null;
}

function visibleAtZoom(cls, zoom, { overviewMode, detailMode }) {
  if (!overviewMode && !detailMode) return true;
  if (overviewMode) {
    if (cls === "nh") return true;
    if (cls === "sh") return zoom >= 8;
    if (cls === "mdr") return zoom >= 9;
    return zoom >= 10;
  }
  if (cls === "nh") return true;
  if (cls === "sh") return zoom >= 9;
  if (cls === "mdr") return zoom >= 10;
  return zoom >= 11;
}

function segmentStyle(feature, { overviewMode, detailMode, zoom, selectedId, selectedIds }) {
  const p = feature.properties || {};
  const cls = p.road_class || "other";
  const id = p.id || p.segment_id;
  const isCovered = p.route_kind === "covered"
    || p.status === "completed" || p.status === "verified" || p.status === "approved";
  const isContinuous = p.route_kind === "continuous" || p.route_kind === "auto_track";
  const forceShow = isCovered || isContinuous
    || p.status === "assigned" || p.status === "approved" || p.status === "pending_approval"
    || p.status === "completed" || p.status === "verified" || p._drawn;
  if (!forceShow && !visibleAtZoom(cls, zoom, { overviewMode, detailMode })) return HIDDEN;

  const status = SEGMENT_STATUS[p.status] || SEGMENT_STATUS.available;
  const road = ROAD_CLASS[cls] || ROAD_CLASS.other;
  const inDrawn = (selectedIds && selectedIds.has?.(id)) || (selectedIds && Array.isArray(selectedIds) && selectedIds.includes(id));
  const isSelected = (selectedId && id === selectedId) || inDrawn || p._drawn;
  // Thin corridor strokes — sealed GIS grey stays readable; raw trail overlays thinner
  const isTrailOverlay = Boolean(p.trail_overlay);
  const weight = isCovered
    ? (isTrailOverlay ? 4 : 5)
    : isContinuous
      ? (detailMode ? 5 : 6)
      : isSelected ? 8 : cls === "nh" ? 6 : cls === "sh" ? 5 : cls === "mdr" ? 4 : 3;

  let color;
  if (isCovered) {
    color = COMPLETED_COLOR;
  } else if (isContinuous) {
    color = ASSIGNMENT_COLOR;
  } else if (isSelected && p._drawn) {
    color = "#7c3aed";
  } else if (status.color === SEGMENT_STATUS.available.color) {
    color = road.color;
  } else {
    color = status.color;
  }

  return {
    color,
    weight,
    opacity: isCovered
      ? (isTrailOverlay ? 0.75 : 0.9)
      : isContinuous
        ? (detailMode ? 0.75 : 0.92)
        : cls === "other" ? 0.92 : detailMode && p.status === "available" && !isSelected ? 0.7 : 0.95,
    dashArray: isCovered || isContinuous
      ? undefined
      : p.status === "assigned" && !p._drawn ? "8 6" : cls === "other" ? "4 4" : undefined,
    interactive: true,
    // Canvas tolerance made corridors look “off road” when zooming; SVG for route overlays
    renderer: (isContinuous || isCovered) ? undefined : roadRenderer,
    pane: isCovered ? "coveredTrail" : undefined,
  };
}

function applyLayerInteractivity(layer, style) {
  const on = style?.interactive !== false;
  layer.options.interactive = on;
  if (layer._path) {
    layer._path.style.pointerEvents = on ? "auto" : "none";
  }
  if (typeof layer._renderer?._updateTolerance === "function") {
    // no-op; tolerance set on shared canvas renderer
  }
}

function fitMapToContent(map, { bounds, features, fitToFeatures, track, drivers, routeOverlays }) {
  try {
    let result = null;

    if (fitToFeatures && features?.length) {
      const layer = L.geoJSON({ type: "FeatureCollection", features });
      const featureBounds = layer.getBounds();
      if (featureBounds.isValid()) result = featureBounds;
    }

    const overlayFeats = [];
    (routeOverlays || []).forEach((r) => {
      const feats = r.features || r.geojson?.features || [];
      overlayFeats.push(...feats);
    });
    if (overlayFeats.length) {
      const layer = L.geoJSON({ type: "FeatureCollection", features: overlayFeats });
      const ob = layer.getBounds();
      if (ob.isValid()) result = result ? result.extend(ob) : ob;
    }

    const pts = [];
    const pushTrack = (t) => {
      if (t?.trail?.length) {
        t.trail.forEach((p) => {
          const ll = safeLatLng(p.lat, p.lon);
          if (ll) pts.push(ll);
        });
      }
      const lastLl = safeLatLng(t?.last?.lat, t?.last?.lon);
      if (lastLl) pts.push(lastLl);
    };
    pushTrack(track);
    (drivers || []).forEach(pushTrack);

    if (pts.length) {
      const trackBounds = L.latLngBounds(pts);
      result = result ? result.extend(trackBounds) : trackBounds;
    }

    if (result?.isValid()) {
      const maxZoom = pts.length && features?.length ? 12 : pts.length ? 15 : 14;
      map.fitBounds(result, { padding: [36, 36], maxZoom });
      return;
    }

    if (bounds) {
      map.fitBounds(bounds, { padding: [20, 20], maxZoom: 7 });
    }
  } catch {
    /* bad GPS / geometry must not blank the Tracking page */
  }
}

function MapBounds({ bounds, features, fitToFeatures, track, drivers, trackingMode, fitKey, routeOverlays }) {
  const map = useMap();
  const fittedKeyRef = useRef(null);
  const sawDriverRef = useRef(false);
  const contentRef = useRef({ bounds, features, fitToFeatures, track, drivers, routeOverlays });
  contentRef.current = { bounds, features, fitToFeatures, track, drivers, routeOverlays };

  const runFit = useCallback(() => {
    fitMapToContent(map, contentRef.current);
  }, [map]);

  // Fit once per selection (fitKey). Do NOT re-fit when progressive road batches arrive —
  // that felt like "auto zoom back" while zooming/panning.
  useEffect(() => {
    if (trackingMode) return;
    const key = String(fitKey ?? "default");
    const hasOverlays = (routeOverlays || []).some(
      (r) => (r.features || r.geojson?.features || []).length > 0,
    );
    const hasContent =
      (fitToFeatures && features?.length > 0)
      || hasOverlays
      || Boolean(bounds)
      || (track?.last?.lat != null)
      || (drivers || []).some((d) => d?.last?.lat != null);
    if (!hasContent) return;
    if (fittedKeyRef.current === key) return;
    // For feature fits, wait until first batch has arrived (unless overlays present)
    if (fitToFeatures && !(features?.length > 0) && !hasOverlays) return;
    fittedKeyRef.current = key;
    runFit();
  }, [trackingMode, fitKey, fitToFeatures, features?.length, bounds, track?.last?.lat, drivers, routeOverlays, runFit]);

  useEffect(() => {
    if (!trackingMode) return;
    const hasContent =
      (features?.length > 0)
      || (track?.last?.lat != null && track?.last?.lon != null)
      || (track?.trail?.length > 0)
      || (drivers || []).some((d) => validLatLon(d?.last?.lat, d?.last?.lon));
    if (!hasContent) return;
    const key = String(fitKey ?? "");
    if (fittedKeyRef.current !== key) {
      fittedKeyRef.current = key;
      sawDriverRef.current = false;
      runFit();
    }
  }, [trackingMode, fitKey, features?.length, track?.last?.lat, track?.last?.lon, track?.trail?.length, drivers, runFit]);

  useEffect(() => {
    if (!trackingMode) return;
    if (!validLatLon(track?.last?.lat, track?.last?.lon)) return;
    if (sawDriverRef.current) return;
    sawDriverRef.current = true;
    runFit();
  }, [trackingMode, track?.last?.lat, track?.last?.lon, runFit]);

  // Tracking only: snap back if user zooms out to world view (not payload-related)
  useEffect(() => {
    if (!trackingMode) return undefined;
    const onZoomEnd = () => {
      if (map.getZoom() < TRACKING_MIN_ZOOM) runFit();
    };
    map.on("zoomend", onZoomEnd);
    return () => map.off("zoomend", onZoomEnd);
  }, [trackingMode, map, runFit]);

  return null;
}

export default function RoadSurveyMap({
  features = [],
  potholes = [],
  track = null,
  /** Multiple drivers for overview tracking map: [{ user_id, live, last, trail?, name }] */
  drivers = null,
  center = STATE_META.andhra.center,
  zoom = STATE_META.andhra.zoom,
  height = 420,
  overviewMode = false,
  detailMode = false,
  bounds = null,
  fitToFeatures = true,
  trackingMode = false,
  fitKey = null,
  hideLegend = false,
  selectedId = null,
  selectedIds = null,
  onSelect,
  assignmentSummary = null,
  emptyMessage = "Select a state or district to view roads.",
  /** Called with Leaflet zoom level whenever zoom changes (for zoom-gated data loads). */
  onZoomChange = null,
  markers = null,
  drawMode = false,
  /** Alternate A→B routes: [{ id, color, features, selected }] */
  routeOverlays = null,
  onRouteSelect = null,
}) {
  const [mapZoom, setMapZoom] = useState(zoom);
  const geoRef = useRef(null);
  const selectedIdSet = useMemo(() => {
    if (!selectedIds) return null;
    return selectedIds instanceof Set ? selectedIds : new Set(selectedIds);
  }, [selectedIds]);
  const collection = useMemo(() => ({ type: "FeatureCollection", features }), [features]);
  const overlays = routeOverlays || [];

  const reportZoom = useCallback((z) => {
    setMapZoom(z);
    onZoomChange?.(z);
  }, [onZoomChange]);

  const styleFn = useCallback(
    (feature) => segmentStyle(feature, {
      overviewMode, detailMode, zoom: mapZoom, selectedId, selectedIds: selectedIdSet,
    }),
    [overviewMode, detailMode, mapZoom, selectedId, selectedIdSet],
  );

  const onEach = useCallback(
    (feature, layer) => {
      const p = feature.properties || {};
      layer.bindPopup(roadSegmentPopupHtml(p), { maxWidth: 320, autoPan: true });
      layer.on("click", async (e) => {
        L.DomEvent.stopPropagation(e);
        onSelect?.(p.id || p.segment_id, p);
        layer.setPopupContent(roadSegmentPopupHtml(p));
        layer.openPopup(e.latlng);
        const latlng = e.latlng;
        if (!latlng) return;
        const key = `${latlng.lat.toFixed(4)},${latlng.lng.toFixed(4)}`;
        let place = reverseCache.get(key);
        if (place === undefined) {
          layer.setPopupContent(`${roadSegmentPopupHtml(p)}<br/><em class="muted">Looking up location…</em>`);
          try {
            const res = await api.surveyReverseGeocode(latlng.lat, latlng.lng);
            place = res?.display_name || "";
            reverseCache.set(key, place);
          } catch {
            place = "";
            reverseCache.set(key, "");
          }
        }
        if (!layer.isPopupOpen()) return;
        const enriched = place
          ? { ...p, place_name: place, display_name: place }
          : p;
        layer.setPopupContent(roadSegmentPopupHtml(enriched));
      });
    },
    [onSelect],
  );

  // Restyle in place on zoom/selection — avoid full GeoJSON remount (was killing clicks + perf)
  useEffect(() => {
    const root = geoRef.current;
    if (!root) return;
    root.eachLayer((layer) => {
      if (!layer.feature) return;
      // Point markers (auto_track start/end) — setStyle is for paths only
      if (layer.feature?.geometry?.type === "Point" || typeof layer.setStyle !== "function") return;
      const style = styleFn(layer.feature);
      try {
        layer.setStyle(style);
        applyLayerInteractivity(layer, style);
      } catch {
        /* ignore bad layers */
      }
    });
  }, [styleFn, mapZoom, selectedId, selectedIdSet]);

  const endPin = useMemo(() => {
    const end = assignmentSummary?.end;
    return safeLatLng(end?.lat, end?.lon ?? end?.lng);
  }, [assignmentSummary?.end]);
  const hasCoveredTrail = useMemo(
    () => (features || []).some((f) => String(f?.properties?.route_kind || "") === "covered"),
    [features],
  );
  const trailSegments = useMemo(
    () => trailSegmentsFromPoints(track?.trail),
    [track?.trail],
  );
  const lastPos = safeLatLng(track?.last?.lat, track?.last?.lon);
  const driverList = (drivers || []).filter((d) => validLatLon(d?.last?.lat, d?.last?.lon));
  const hasTrack = Boolean(lastPos || trailSegments.length || driverList.length);
  const markerList = (markers || []).filter((m) => validLatLon(m?.lat, m?.lon));

  if (!features.length && !potholes.length && !hasTrack && !markerList.length && !overlays.length) {
    return (
      <div className="map-box empty" style={{ height }}>
        {emptyMessage}
      </div>
    );
  }

  const hint = overlays.length
    ? "Route options on map · tap a card or line to choose"
    : trackingMode
      ? "Zoom freely · snaps back only if you zoom out to world view"
      : overviewMode
        ? "State view · NH only"
        : detailMode
          ? "District · click near a road (wider hit area)"
          : null;

  // Stable key — do NOT include mapZoom (remounting on zoom broke clicks)
  const geoKey = trackingMode
    ? `track-${fitKey ?? "x"}-${features.length}`
    : `roads-${features.length}-${features[0]?.properties?.id || "x"}`;

  const driverCenter = (() => {
    const d = driverList.find((x) => validLatLon(x?.last?.lat, x?.last?.lon));
    return d ? safeLatLng(d.last.lat, d.last.lon) : null;
  })();
  const mapCenter = lastPos || driverCenter || center;

  return (
    <div className="map-box survey-map-wrap" style={{ height }}>
      {hint && <div className="survey-map-hint">{hint}</div>}
      <MapContainer center={mapCenter} zoom={zoom} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
        <TileLayer url={MAP_TILE_URL} attribution={MAP_TILE_ATTRIBUTION} />
        <ZoomWatcher onZoom={reportZoom} />
        <TrailPane />
        <MapBounds
          bounds={bounds}
          features={features}
          fitToFeatures={fitToFeatures}
          track={track}
          drivers={driverList}
          trackingMode={trackingMode}
          fitKey={fitKey}
          routeOverlays={overlays}
        />
        {features.length > 0 && (
          <GeoJSON
            key={geoKey}
            ref={geoRef}
            data={collection}
            style={styleFn}
            onEachFeature={onEach}
          />
        )}
        {[...overlays].sort((a, b) => Number(a.selected) - Number(b.selected)).map((r) => {
          const feats = r.features || r.geojson?.features || [];
          if (!feats.length) return null;
          const color = r.selected ? (r.activeColor || "#1a73e8") : (r.color || "#9aa0a6");
          const weight = r.selected ? 7 : 4;
          const selected = Boolean(r.selected);
          return (
            <GeoJSON
              key={`route-${r.id}-${selected ? "on" : "off"}`}
              data={{ type: "FeatureCollection", features: feats }}
              style={() => ({
                color,
                weight,
                opacity: selected ? 0.95 : 0.5,
                interactive: true,
              })}
              onEachFeature={(_f, layer) => {
                layer.on("click", (e) => {
                  L.DomEvent.stopPropagation(e);
                  onRouteSelect?.(r.id);
                });
              }}
            />
          );
        })}
        {trailSegments.map((seg, i) => (
          <Polyline
            key={`trail-seg-${i}-${seg.length}`}
            positions={seg}
            pathOptions={{
              color: TRAIL_COLOR,
              weight: 5,
              opacity: 0.95,
              lineCap: "round",
              lineJoin: "round",
            }}
            pane="gpsTrail"
          />
        ))}
        {lastPos && (
          <Marker position={lastPos} icon={driverIcon(Boolean(track?.live))}>
            <Popup>
              <strong>{track.live ? "Live" : "Last known"}</strong>
              <br />
              {lastPos[0].toFixed(6)}, {lastPos[1].toFixed(6)}
              {track?.last?.ts && <><br />{track.last.ts}</>}
            </Popup>
          </Marker>
        )}
        {markerList.map((m, i) => (
          <CircleMarker
            key={`mark-${m.kind || i}-${m.lat}-${m.lon}`}
            center={[m.lat, m.lon]}
            radius={10}
            pathOptions={{
              color: "#16202a",
              weight: 2,
              fillColor: m.kind === "end" ? "#b3261e" : m.kind === "turn" ? "#f59e0b" : "#2563eb",
              fillOpacity: 0.95,
            }}
          >
            <Popup>
              <strong>{m.label || m.kind || "Point"}</strong>
              <br />
              {Number(m.lat).toFixed(5)}, {Number(m.lon).toFixed(5)}
            </Popup>
          </CircleMarker>
        ))}
        {driverList.map((d) => {
          const pos = safeLatLng(d?.last?.lat, d?.last?.lon);
          if (!pos) return null;
          return (
            <Marker key={`drv-${d.user_id}`} position={pos} icon={driverIcon(Boolean(d.live))}>
              <Popup>
                <strong>{d.name || d.full_name || d.username || `User ${d.user_id}`}</strong>
                <br />
                {d.live ? "Live" : "Last known"}
                {d.district_name ? <><br />{d.district_name}</> : null}
                {d.covered_km != null ? <><br />Covered {Number(d.covered_km).toFixed(1)} km</> : null}
              </Popup>
            </Marker>
          );
        })}
        {potholes.filter((p) => p.lat && p.lon).map((p, i) => (
          <CircleMarker
            key={`pothole-${i}`}
            center={[p.lat, p.lon]}
            radius={9}
            pathOptions={{
              color: "#16202a",
              weight: 2,
              fillColor: POTHOLE_COLORS[p.severity] || "#8a97a3",
              fillOpacity: 0.9,
            }}
          >
            <Popup>
              {p.frame_s3_url && (
                <img
                  src={p.frame_s3_url}
                  alt="Pothole"
                  style={{ width: 180, maxWidth: "100%", borderRadius: 6, marginBottom: 6, display: "block" }}
                />
              )}
              <strong>{p.severity || "—"}</strong>
              {(p.work_status || p.status) && <><br />{p.work_status || p.status}</>}
              {p.map_link && (
                <><br /><a href={p.map_link} target="_blank" rel="noreferrer">Open in Maps</a></>
              )}
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
      {(features.length > 0 || potholes.length > 0 || hasTrack) && !hideLegend && (
        <div className="survey-legend">
          {features.length > 0 && Object.entries(ROAD_CLASS).map(([k, v]) => (
            <span key={k} className="survey-legend-item">
              <i style={{ background: v.color }} /> {v.label}
            </span>
          ))}
          {detailMode && Object.entries(SEGMENT_STATUS).map(([k, v]) => (
            <span key={k} className="survey-legend-item">
              <i style={{ background: v.color }} /> {v.label}
            </span>
          ))}
          {potholes.length > 0 && Object.entries(POTHOLE_COLORS).map(([k, color]) => (
            <span key={k} className="survey-legend-item">
              <i style={{ background: color, borderRadius: "50%" }} /> Pothole {k}
            </span>
          ))}
          {assignmentSummary?.segment_count > 0 && (
            <span className="survey-legend-item survey-legend-assignment">
              <i className="survey-legend-swatch-existing" /> Today&apos;s assignment ({assignmentSummary.segment_count} seg)
            </span>
          )}
          {assignmentSummary?.over_target && (
            <span className="survey-legend-item survey-legend-over-target">
              <i className="survey-legend-swatch-over" /> Over target (+{assignmentSummary.over_by_km} km)
            </span>
          )}
          {hasTrack && (
            <>
              <span className="survey-legend-item">
                <i style={{ background: TRAIL_COLOR }} /> GPS trail
              </span>
              <span className="survey-legend-item">
                <i style={{ background: "#E8B40A", borderRadius: "50%" }} /> Live driver
              </span>
              <span className="survey-legend-item">
                <i style={{ background: ASSIGNMENT_COLOR }} /> Assigned route
              </span>
              <span className="survey-legend-item">
                <i style={{ background: COMPLETED_COLOR }} /> GPS-covered
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
