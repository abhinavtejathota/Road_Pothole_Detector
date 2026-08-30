import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import RoadSurveyMap from "../components/RoadSurveyMap";
import RoadNetworkLegend from "../components/RoadNetworkLegend";
import Loader from "../components/Loader";
import { STATE_META, INDIA_BOUNDS, INDIA_CENTER, INDIA_ZOOM, todayIST } from "../data/surveyConstants";
import "./Tracking.css";
function SideStats({ title, rows, note }) {
  return (
    <div className="tracking-side-panel">
      <h3>{title}</h3>
      <ul className="tracking-stat-list">
        {rows.map(([label, value]) => (
          <li key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </li>
        ))}
      </ul>
      {note ? <p className="tracking-stat-note">{note}</p> : null}
    </div>
  );
}

function hasAssignment(v) {
  const a = v?.assignment || {};
  if (a.has_route) return true;
  if ((a.segment_count || 0) > 0) return true;
  if (a.mode === "auto_track") return true;
  if (a.start && a.end) return true;
  return Number(a.total_km || 0) > 0;
}

function routeLabel(summaryOrAssign) {
  const s = summaryOrAssign?.start?.label || summaryOrAssign?.start?.display_name;
  const e = summaryOrAssign?.end?.label || summaryOrAssign?.end?.display_name;
  if (s && e) return `${s} → ${e}`;
  return s || e || "—";
}

/** Tracking maps: keep assigned corridors; optionally keep GPS-covered grey overlays. */
function trackingRouteFeatures(feats, { stateKey, userId, includeCovered = false } = {}) {
  const seen = new Set();
  return (feats || []).filter((f) => {
    const p = f.properties || {};
    if (!includeCovered && p.route_kind === "covered") return false;
    // Only filter by VG when the property is present (overview geojson). Detail
    // features from get_videographer_track omit videographer_user_id — do not drop them.
    if (
      userId != null
      && p.videographer_user_id != null
      && String(p.videographer_user_id) !== String(userId)
    ) {
      return false;
    }
    if (stateKey) {
      const sk = p.state_key;
      if (sk && sk !== stateKey) return false;
    }
    const uid = p.videographer_user_id ?? userId ?? "";
    const day = p.assignment_date || "";
    const sid = p.id || p.segment_id || "";
    const key = `${uid}|${day}|${sid}|${p.route_kind || ""}`;
    if (sid && seen.has(key)) return false;
    if (sid) seen.add(key);
    return true;
  });
}

export default function Tracking() {
  const { user } = useAuth();
  const [country] = useState("IN");
  const [stateKey, setStateKey] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [viewDate, setViewDate] = useState(todayIST());
  const [detail, setDetail] = useState(null);
  const [detailErr, setDetailErr] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  const today = todayIST();

  const { data, loading, error, reload } = useAsync(
    () => api.trackingVideographers(viewDate),
    [viewDate],
  );

  const loadDetail = useCallback(async (id, { soft = false } = {}) => {
    if (!id) {
      setDetail(null);
      return;
    }
    if (!soft) {
      setDetailLoading(true);
      setDetailErr("");
    }
    try {
      const res = await api.trackingVideographer(id, viewDate);
      setDetail(res);
      setDetailErr("");
    } catch (e) {
      if (!soft) {
        setDetail(null);
        setDetailErr(e.message);
      } else {
        setDetailErr(e.message);
      }
    } finally {
      if (!soft) setDetailLoading(false);
    }
  }, [viewDate]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailErr("");
      return undefined;
    }
    loadDetail(selectedId, { soft: false });
    const isToday = viewDate === today;
    if (!isToday) return undefined;
    const t = setInterval(() => loadDetail(selectedId, { soft: true }), 4000);
    return () => clearInterval(t);
  }, [selectedId, loadDetail, viewDate, today]);

  useEffect(() => {
    const isToday = viewDate === today;
    if (!isToday) return undefined;
    const t = setInterval(() => reload(), 8000);
    return () => clearInterval(t);
  }, [reload, viewDate, today]);

  const list = data?.videographers || [];
  const history = data?.history || [];

  const filtered = useMemo(() => {
    if (!stateKey) return list;
    const wantId = STATE_META[stateKey]?.id;
    return list.filter((v) => Number(v.state_id) === Number(wantId));
  }, [list, stateKey]);

  useEffect(() => {
    if (!selectedId) return;
    if (!filtered.some((v) => String(v.user_id) === String(selectedId))) {
      setSelectedId("");
      setDetail(null);
    }
  }, [filtered, selectedId]);

  const overviewStats = useMemo(() => {
    const target = filtered.reduce((s, v) => s + Number(v.assignment?.target_km || 0), 0);
    const assigned = filtered.reduce((s, v) => s + Number(v.assignment?.total_km || 0), 0);
    const covered = filtered.reduce((s, v) => s + Number(v.covered_km || 0), 0);
    const live = filtered.filter((v) => v.live).length;
    const withAssign = filtered.filter(hasAssignment).length;
    const carryover = filtered.filter((v) => v.assignment?.is_carryover).length;
    return { target, assigned, covered, live, withAssign, carryover, total: filtered.length };
  }, [filtered]);

  // Overview always shows EVERYONE — selecting a VG must not wipe this map
  const overviewFeatures = useMemo(
    () => trackingRouteFeatures(data?.assignments_geojson?.features, { stateKey }),
    [data?.assignments_geojson, stateKey],
  );

  const drivers = useMemo(
    () => filtered
      .filter((v) => {
        const lat = Number(v.last?.lat);
        const lon = Number(v.last?.lon);
        return Number.isFinite(lat) && Number.isFinite(lon);
      })
      .map((v) => ({
        user_id: v.user_id,
        name: v.full_name || v.username,
        live: v.live,
        last: v.last,
        district_name: v.district_name,
        covered_km: v.covered_km,
      })),
    [filtered],
  );

  const detailFeatures = useMemo(
    () => trackingRouteFeatures(detail?.features, { includeCovered: true }),
    [detail?.features],
  );

  const detailTrack = useMemo(() => {
    if (!detail || !selectedId) return null;
    if (String(detail.user_id) !== String(selectedId)) return null;
    return {
      live: Boolean(detail.live),
      last: detail.last,
      trail: Array.isArray(detail.trail) ? detail.trail : [],
    };
  }, [detail, selectedId]);

  const selected = filtered.find((v) => String(v.user_id) === String(selectedId));
  const mapState = stateKey && STATE_META[stateKey]
    ? STATE_META[stateKey]
    : (Number(detail?.state_id) === 2 || Number(selected?.state_id) === 2)
      ? STATE_META.telangana
      : STATE_META.andhra;

  const overviewBounds = stateKey && STATE_META[stateKey]
    ? STATE_META[stateKey].bounds
    : INDIA_BOUNDS;
  const overviewCenter = stateKey && STATE_META[stateKey]
    ? STATE_META[stateKey].center
    : INDIA_CENTER;
  const overviewZoom = stateKey && STATE_META[stateKey]
    ? STATE_META[stateKey].zoom
    : INDIA_ZOOM;

  if (!user?.is_admin && !user?.is_dev_admin) return <Navigate to="/" replace />;

  const corridorLabel = detail?.summary?.start?.label && detail?.summary?.end?.label
    ? `${detail.summary.start.label} → ${detail.summary.end.label}`
    : detail?.summary?.start?.label || detail?.summary?.end?.label
      || routeLabel(selected?.assignment)
      || "—";

  const assignDateNote = detail?.assignment_date || detail?.summary?.assignment_date
    || selected?.assignment?.assignment_date;
  const isCarry = detail?.is_carryover || detail?.summary?.is_carryover || selected?.assignment?.is_carryover;
  const trailCount = detailTrack?.trail?.length || 0;

  return (
    <PageShell
      title="Tracking"
      subtitle={`${data?.date || viewDate} · live Capture GPS + assignments`}
      loading={loading && !data}
      error={error}
      actions={<button type="button" className="btn btn-sm" onClick={reload}>Refresh</button>}
    >
      <MotionCard className="card" delay={0}>
        <CardHeader title="Region & date" />
        <div className="card-body">
          <div className="dashboard-geo-row">
            <label className="dashboard-geo-field">
              <span className="label">Date</span>
              <input
                type="date"
                className="input select-sm"
                value={viewDate}
                max={today}
                onChange={(e) => {
                  setViewDate(e.target.value || today);
                  setSelectedId("");
                  setDetail(null);
                }}
              />
            </label>
            <label className="dashboard-geo-field">
              <span className="label">Country</span>
              <select className="select select-sm" value={country} disabled>
                <option value="IN">India</option>
              </select>
            </label>
            <label className="dashboard-geo-field">
              <span className="label">State</span>
              <select
                className="select select-sm"
                value={stateKey}
                onChange={(e) => setStateKey(e.target.value)}
              >
                <option value="">All (India)</option>
                {Object.values(STATE_META).map((s) => (
                  <option key={s.key} value={s.key}>{s.name}</option>
                ))}
              </select>
            </label>
          </div>
          {viewDate === today && overviewStats.carryover > 0 && (
            <div className="alert alert-warn" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
              {overviewStats.carryover} videographer(s) still have incomplete prior-day assignment(s) —
              shown on today’s map. See previous day assignments below.
            </div>
          )}
        </div>
      </MotionCard>

      <MotionCard className="card" delay={0.05}>
        <CardHeader title="Videographer" />
        <div className="card-body">
          <div className="form-group" style={{ marginBottom: selectedId ? "0.85rem" : "1rem", maxWidth: 480 }}>
            <label className="label" htmlFor="track-vg">Select videographer</label>
            <select
              id="track-vg"
              className="select"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              <option value="">— Overall —</option>
              {filtered.map((v) => {
                const a = v.assignment || {};
                const km = a.total_km;
                const segs = a.segment_count;
                const assignLabel = hasAssignment(v)
                  ? (segs
                    ? ` · ${segs} seg / ${km ?? 0} km`
                    : ` · ${a.mode === "auto_track" ? "custom" : "route"} / ${km ?? 0} km`)
                  : " · no assignment";
                const dayTag = a.is_carryover && a.assignment_date
                  ? ` · prior ${a.assignment_date}`
                  : "";
                return (
                  <option key={v.user_id} value={v.user_id}>
                    {v.live ? "● " : "○ "}
                    {v.full_name || v.username}
                    {assignLabel}
                    {dayTag}
                    {v.live ? " · LIVE" : v.last ? " · last known" : ""}
                  </option>
                );
              })}
            </select>
          </div>

          {!selectedId ? (
            <>
              <div className="survey-map-legend">
                <RoadNetworkLegend showAssignment showCompleted={false} />
              </div>
              <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.75rem" }}>
                Overall view — indigo = assigned routes · Yellow pin = live · Grey pin = last known.
                {" · "}{overviewStats.live} live · {overviewStats.withAssign} with assignment on {viewDate}
                {overviewStats.carryover ? ` · ${overviewStats.carryover} prior-day carryover` : ""}
              </p>
              <div className="tracking-layout">
                <div className="tracking-map-main">
                  <RoadSurveyMap
                    features={overviewFeatures}
                    drivers={drivers}
                    center={overviewCenter}
                    zoom={overviewZoom}
                    bounds={stateKey ? overviewBounds : null}
                    fitToFeatures={overviewFeatures.length > 0 || drivers.length > 0}
                    trackingMode
                    fitKey={`overview-${stateKey || "in"}-${viewDate}-${overviewFeatures.length}-${drivers.length}`}
                    height={420}
                    hideLegend
                    emptyMessage={
                      overviewFeatures.length || drivers.length
                        ? undefined
                        : "No assignments or GPS positions yet. Videographers need a Survey route + Capture with location on."
                    }
                  />
                </div>
                <SideStats
                  title="Overall"
                  rows={[
                    ["Videographers", overviewStats.total],
                    ["Live now", overviewStats.live],
                    ["With assignment", overviewStats.withAssign],
                    ["Prior-day open", overviewStats.carryover],
                    ["Assigned km", `${overviewStats.assigned.toFixed(1)} km`],
                    ["Quota target", `${overviewStats.target.toFixed(1)} km`],
                    ["GPS covered", `${overviewStats.covered.toFixed(1)} km`],
                  ]}
                  note={`Showing ${viewDate}. Live refresh only runs for today.`}
                />
              </div>
            </>
          ) : (
            <>
              <div className="survey-map-legend">
                <RoadNetworkLegend showAssignment showCompleted />
              </div>
              <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.75rem" }}>
                Indigo = assigned corridor (may use side streets) · Grey = path you actually drove (GPS) · Cyan = same trail (raw)
                {detail?.district_name ? ` · ${detail.district_name}` : ""}
                {assignDateNote ? ` · assignment ${assignDateNote}` : ""}
                {isCarry ? " · prior-day carryover" : ""}
                {trailCount ? ` · ${trailCount} trail points` : detail && !detailLoading ? " · no GPS trail for this date" : ""}
              </p>
              {detailErr && <div className="alert alert-error">{detailErr}</div>}
              <div className="tracking-layout">
                <div className="tracking-map-main">
                  {detailLoading && !detail ? (
                    <div className="map-box empty" style={{ height: 420 }}>
                      <Loader label="Loading track" />
                    </div>
                  ) : (
                    <RoadSurveyMap
                      key={`detail-${selectedId}-${viewDate}`}
                      features={detailFeatures}
                      track={detailTrack}
                      center={mapState.center}
                      zoom={mapState.zoom}
                      fitToFeatures
                      trackingMode
                      fitKey={`detail-${selectedId}-${viewDate}-${trailCount}-${detailFeatures.length}`}
                      detailMode
                      height={420}
                      hideLegend
                      assignmentSummary={detail?.summary}
                    />
                  )}
                </div>
                <SideStats
                  title={detail?.full_name || selected?.full_name || "Videographer"}
                  rows={[
                    ["Assign date", assignDateNote || viewDate],
                    ["Daily quota", `${detail?.summary?.target_km ?? selected?.assignment?.target_km ?? "—"} km`],
                    ["Assigned", `${detail?.summary?.total_km ?? selected?.assignment?.total_km ?? 0} km`],
                    ["GPS covered", `${Number(detail?.covered_km ?? selected?.covered_km ?? 0).toFixed(1)} km`],
                    ["Trail points", trailCount],
                    ["Segments", detail?.summary?.segment_count ?? selected?.assignment?.segment_count ?? 0],
                    ["Corridor", corridorLabel],
                    ["Status", detail?.live || selected?.live ? "Live" : detail?.last ? "Last known" : "No GPS"],
                  ]}
                  note={
                    detail?.summary?.assignment_complete
                      ? "Assignment complete — reached endpoint (start→end). Grey = GPS path driven."
                      : isCarry
                        ? `Incomplete prior-day assignment from ${assignDateNote} — finish before a new day route.`
                        : detail?.summary?.quota_incomplete
                          ? "Quota incomplete — covered GPS is below daily target (no penalty)."
                          : detail?.summary?.segment_count || detail?.summary?.mode === "auto_track"
                            ? "Quota on track for this assignment."
                            : "No corridor assignment generated yet."
                  }
                />
              </div>
              {detail?.last && Number.isFinite(Number(detail.last.lat)) && Number.isFinite(Number(detail.last.lon)) && (
                <p className="muted" style={{ fontSize: "0.8rem", marginTop: "0.65rem", marginBottom: 0 }}>
                  Position: {Number(detail.last.lat).toFixed(6)}, {Number(detail.last.lon).toFixed(6)}
                  {detail.last.ts ? ` · ${detail.last.ts}` : ""}
                </p>
              )}
            </>
          )}
        </div>
      </MotionCard>

      {viewDate === today && history.length > 0 && (
        <MotionCard className="card" delay={0.1}>
          <CardHeader
            title="Previous day assignments"
            badge={<span className="badge badge-created">{history.length}</span>}
          />
          <div className="card-body">
            <p className="muted" style={{ marginBottom: "0.75rem", fontSize: "0.85rem" }}>
              Open any past day to see that date’s routes, GPS trails, and coverage (same as the date picker).
            </p>
            <div className="table-wrap table-template">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Videographers</th>
                    <th>Segments</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.date}>
                      <td>
                        {h.date}
                        {h.date === today ? " (today)" : ""}
                      </td>
                      <td>{h.user_count}</td>
                      <td>{h.segment_count}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            setViewDate(h.date);
                            setSelectedId("");
                            setDetail(null);
                          }}
                        >
                          View day
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </MotionCard>
      )}
    </PageShell>
  );
}
