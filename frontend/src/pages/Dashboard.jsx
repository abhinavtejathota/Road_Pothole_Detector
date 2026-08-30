import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from "chart.js";
import { Doughnut } from "react-chartjs-2";
import { motion } from "framer-motion";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard, stagger } from "../components/PageTransition";
import PlainMap from "../components/PlainMap";
import Loader from "../components/Loader";
import StatusBadge from "../components/StatusBadge";
import {
  INDIA_BOUNDS, INDIA_CENTER, INDIA_ZOOM, STATE_META, ROAD_CLASS,
} from "../data/surveyConstants";
import "./Dashboard.css";

ChartJS.register(ArcElement, Tooltip, Legend);

const GPS_CLASS_CARDS = [
  ["nh", "National highway covered", "kpi-border-danger"],
  ["sh", "State highway covered", "kpi-border-warning"],
  ["mdr", "MDR covered", "kpi-border-info"],
  ["other", "Local roads covered", "kpi-border-muted"],
];

const KPI_SUPERVISOR = [
  ["sla_breached", "SLA Breached", "kpi-border-danger"],
  ["wip", "In Progress", "kpi-border-info"],
  ["verified", "Verified Complete", "kpi-border-success"],
  ["failed", "Failed Validation", "kpi-border-muted"],
  ["total_work_orders", "Total Work Orders", "kpi-border-primary"],
];

const KPI_STAFF = [
  ["wip", "In Progress", "kpi-border-info"],
  ["unassigned_sessions", "Unassigned Sessions", "kpi-border-warning"],
  ["verified", "Verified Complete", "kpi-border-success"],
  ["sla_breached", "SLA Breached", "kpi-border-danger"],
  ["total_work_orders", "Total Work Orders", "kpi-border-primary"],
  ["total_potholes", "Total Potholes", "kpi-border-primary"],
];

const KPI_LINK = {
  nh: "/tracking",
  sh: "/tracking",
  mdr: "/tracking",
  other: "/tracking",
  total_potholes: "/tasks",
  unassigned_sessions: "/tasks",
  wip: "/tasks?status=WIP",
  verified: "/tasks?status=Verified",
  sla_breached: "#sla-breaches",
  failed: "/tasks?status=Failed",
  total_work_orders: "/tasks",
  total_sessions: "/tasks",
};

function onTimePct(v) {
  const verified = v.verified_tasks || 0;
  if (!verified) return "—";
  return `${Math.round(((v.on_time_tasks || 0) / verified) * 100)}%`;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function RoadLengthPanel({ lengths, loading }) {
  const by = lengths?.km_by_class || {};
  const rows = [
    ["nh", "National highways"],
    ["sh", "State highways"],
    ["mdr", "MDR"],
    ["other", "Local roads"],
  ];
  return (
    <div className="road-length-panel">
      <div className="road-length-panel-title">{lengths?.label || "Road network length"}</div>
      {loading ? (
        <Loader label="Loading lengths" />
      ) : (
        <ul className="road-length-list">
          {rows.map(([key, label]) => (
            <li key={key}>
              <span className="road-length-swatch" style={{ background: ROAD_CLASS[key]?.color }} />
              <span className="road-length-label">{label}</span>
              <strong className="road-length-km">
                {by[key] != null ? `${Number(by[key]).toLocaleString()} km` : "—"}
              </strong>
            </li>
          ))}
          <li className="road-length-total">
            <span className="road-length-label">Total</span>
            <strong className="road-length-km">
              {lengths?.total_km != null ? `${Number(lengths.total_km).toLocaleString()} km` : "—"}
            </strong>
          </li>
        </ul>
      )}
      {lengths?.note && <p className="muted road-length-note">{lengths.note}</p>}
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const isFieldUser = user?.is_videographer && !user?.is_dev_admin && !user?.is_admin;

  const [country] = useState("IN");
  const [stateKey, setStateKey] = useState(
    isFieldUser && user?.state_id === 2 ? "telangana" : "andhra",
  );
  const [districtId, setDistrictId] = useState(() => {
    if (!isFieldUser) return "";
    const ids = user?.district_ids?.length
      ? user.district_ids
      : (user?.district_id != null ? [user.district_id] : []);
    return ids.length ? String(ids[0]) : "";
  });
  const fieldDistrictIds = useMemo(() => {
    if (!isFieldUser) return null;
    const ids = user?.district_ids?.length
      ? user.district_ids
      : (user?.district_id != null ? [user.district_id] : []);
    return ids.map(String);
  }, [isFieldUser, user?.district_ids, user?.district_id]);
  /** Sticky unlock removed — dashboard map is base tiles only. */
  const { data, loading, error, reload } = useAsync(() => api.dashboard(), []);
  const { data: states } = useAsync(() => api.surveyStates(), []);
  const { data: districts } = useAsync(
    () => (stateKey ? api.surveyDistricts(stateKey) : Promise.resolve([])),
    [stateKey],
  );
  const { data: lengths, loading: loadingLengths } = useAsync(
    () => api.surveyRoadLengths(stateKey || undefined),
    [stateKey],
  );
  const { data: districtLengths, loading: loadingDistrictLengths } = useAsync(
    () => (
      stateKey && districtId
        ? api.surveyRoadLengths(stateKey, districtId)
        : Promise.resolve(null)
    ),
    [stateKey, districtId],
  );

  const selectedDistrict = useMemo(
    () => (districts || []).find((d) => String(d.district_id) === String(districtId)),
    [districts, districtId],
  );

  const mapBounds = useMemo(() => {
    if (selectedDistrict?.center?.length === 2) {
      const [lat, lon] = selectedDistrict.center;
      const pad = 0.35;
      return [[lat - pad, lon - pad], [lat + pad, lon + pad]];
    }
    if (stateKey && STATE_META[stateKey]) return STATE_META[stateKey].bounds;
    return INDIA_BOUNDS;
  }, [stateKey, selectedDistrict]);

  const mapCenter = stateKey && STATE_META[stateKey] ? STATE_META[stateKey].center : INDIA_CENTER;
  const mapZoom = districtId ? 10 : (stateKey && STATE_META[stateKey] ? STATE_META[stateKey].zoom : INDIA_ZOOM);

  const roleView = data?.role_view || (isFieldUser ? "videographer" : "admin");
  const isField = roleView === "videographer";

  const gpsCoverage = useMemo(() => {
    if (!data) return null;
    if (isField) return data.field?.gps_coverage || null;
    // Prefer selected state when it has real coverage/assignments; otherwise overall
    // (AP filter was showing 0.0 while overall had GPS — looked like blank KPIs).
    const byState = stateKey ? data.gps_coverage_by_state?.[stateKey] : null;
    const stateHas =
      byState
      && (Number(byState.covered_km || 0) > 0.01 || Number(byState.assigned_km || 0) > 0.01);
    if (stateHas) return { ...byState, _scope: stateKey };
    const overall = data.gps_coverage || null;
    if (overall) return { ...overall, _scope: "overall" };
    return byState ? { ...byState, _scope: stateKey } : null;
  }, [data, isField, stateKey]);

  const kpiCards = roleView === "supervisor"
    ? KPI_SUPERVISOR
    : roleView === "vendor"
      ? KPI_STAFF
      : isField
        ? null
        : user?.is_dev_admin
          ? null // DevAdmin uses GPS class cards below
          : KPI_STAFF;

  const subtitle = isField
    ? "Your field survey overview"
    : roleView === "supervisor"
      ? "Review & SLA overview"
      : "Live road ops overview";

  const onStateChange = (key) => {
    if (isField) return;
    setStateKey(key);
    setDistrictId("");
  };

  return (
    <PageShell
      title={`${greeting()}, ${user?.full_name || user?.username || "there"}`}
      subtitle={subtitle}
      skeleton="dashboard"
      loading={loading}
      error={error}
      actions={<button type="button" className="btn btn-sm" onClick={reload}>Refresh</button>}
    >
      {data && (
        <>
          {!isField && data.breached?.length > 0 && (
            <motion.div className="alert alert-error" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}>
              <strong>{data.breached.length} task(s) have breached SLA.</strong>
              {" "}
              <a href="#sla-breaches">View breaches →</a>
            </motion.div>
          )}

          {isField ? (
            <motion.div className="grid grid-kpi grid-kpi-field" variants={stagger} initial="initial" animate="animate">
              {[
                ...GPS_CLASS_CARDS.map(([key, label, borderClass]) => {
                  const km = gpsCoverage?.by_class?.[key] ?? data.field?.covered_by_class?.[key] ?? 0;
                  const assigned = Number(gpsCoverage?.assigned_km || data.field?.km_today || 0);
                  const pct = gpsCoverage?.pct_of_corridor?.[key]
                    ?? (assigned > 0 ? Math.round((Number(km) / assigned) * 1000) / 10 : 0);
                  return [
                    `${Number(km).toFixed(1)} km`,
                    `${label} · ${pct}% of overall`,
                    borderClass,
                    "/survey",
                    key,
                  ];
                }),
                [data.field?.videos_uploaded ?? 0, "Videos / uploads", "kpi-border-primary", "/capture", "videos"],
              ].map(([value, label, borderClass, to, key], i) => (
                <MotionCard key={key || label} className={`card card-glow card-interactive ${borderClass}`} delay={i * 0.04}>
                  <Link to={to} style={{ color: "inherit", textDecoration: "none", display: "block", textAlign: "center", padding: "0.25rem 0" }}>
                    <div className="kpi-value" style={{ fontSize: "1.15rem" }}>{value}</div>
                    <div className="kpi-label">{label}</div>
                  </Link>
                </MotionCard>
              ))}
            </motion.div>
          ) : user?.is_dev_admin ? (
            <motion.div className="grid grid-kpi" variants={stagger} initial="initial" animate="animate">
              {(() => {
                const g = gpsCoverage || {};
                const scope =
                  g._scope === "overall" || !stateKey
                    ? "All states"
                    : (STATE_META[stateKey]?.name || stateKey);
                const cards = [
                  ...GPS_CLASS_CARDS.map(([key, label, borderClass]) => {
                    const km = Number(g.by_class?.[key] ?? 0);
                    const assigned = Number(g.assigned_km || 0);
                    const pct = g.pct_of_corridor?.[key]
                      ?? (assigned > 0 ? Math.round((km / assigned) * 1000) / 10 : 0);
                    return {
                      key,
                      borderClass,
                      href: "/tracking",
                      value: `${km.toFixed(1)} km`,
                      label: `${label} · ${pct}% of overall`,
                      sub: `Of ${assigned.toFixed(1)} km assigned · ${scope}`,
                    };
                  }),
                  {
                    key: "total_covered",
                    borderClass: "kpi-border-success",
                    href: "/tracking",
                    value: `${Number(g.covered_km ?? 0).toFixed(1)} km`,
                    label: `Total GPS covered · ${g.pct_of_assigned_total ?? 0}% of overall`,
                    sub: `Of ${Number(g.assigned_km || g.target_km || 0).toFixed(1)} km assigned · ${scope}`,
                  },
                    {
                    key: "assigned_today",
                    borderClass: "kpi-border-info",
                    href: "/survey/admin",
                    value: `${Number(g.assigned_km ?? 0).toFixed(1)} km`,
                    label: "Assigned corridors (overall)",
                    sub: `${g.videographers_with_assignment ?? 0} videographer(s) with assignments · ${scope}`,
                  },
                  {
                    key: "live_crews",
                    borderClass: "kpi-border-warning",
                    href: "/tracking",
                    value: `${g.videographers_live ?? 0} / ${g.videographers ?? 0}`,
                    label: "Live / total videographers",
                    sub: `${g.videographers_with_gps ?? 0} with GPS (overall) · ${scope}`,
                  },
                  {
                    key: "total_potholes",
                    borderClass: "kpi-border-primary",
                    href: "/tasks",
                    value: data.kpi?.total_potholes ?? 0,
                    label: "Potholes discovered",
                    sub: null,
                  },
                ];
                return cards.map((card, i) => (
                  <MotionCard key={card.key} className={`card card-glow card-interactive ${card.borderClass}`} delay={i * 0.04}>
                    <Link to={card.href} style={{ color: "inherit", textDecoration: "none", display: "block", textAlign: "center", padding: "0.25rem 0" }}>
                      <div className="kpi-value" style={{ fontSize: typeof card.value === "string" ? "1.15rem" : undefined }}>
                        {card.value}
                      </div>
                      <div className="kpi-label">{card.label}</div>
                      {card.sub && (
                        <div className="muted" style={{ fontSize: "0.68rem", marginTop: "0.2rem" }}>{card.sub}</div>
                      )}
                    </Link>
                  </MotionCard>
                ));
              })()}
            </motion.div>
          ) : (
            <motion.div className="grid grid-kpi" variants={stagger} initial="initial" animate="animate">
              {(kpiCards || KPI_STAFF).map(([key, label, borderClass], i) => {
                const href = KPI_LINK[key] || "/tasks";
                const isAnchor = href.startsWith("#");
                const Wrapper = isAnchor ? "a" : Link;
                const linkProps = isAnchor ? { href } : { to: href };
                return (
                  <MotionCard key={key} className={`card card-glow card-interactive ${borderClass}`} delay={i * 0.04}>
                    <Wrapper {...linkProps} style={{ color: "inherit", textDecoration: "none", display: "block", textAlign: "center", padding: "0.25rem 0" }}>
                      <div className="kpi-value">{data.kpi[key] ?? 0}</div>
                      <div className="kpi-label">{label}</div>
                    </Wrapper>
                  </MotionCard>
                );
              })}
            </motion.div>
          )}

          <MotionCard className="card" delay={0.1}>
            <CardHeader title={isField ? "Your district" : "Map"} />
            <div className="card-body">
              <div className="dashboard-geo-row" style={{ marginBottom: "0.75rem" }}>
                <label className="dashboard-geo-field">
                  <span className="label">Country</span>
                  <select className="select select-sm" value={country} disabled aria-label="Country">
                    <option value="IN">India</option>
                  </select>
                </label>
                <label className="dashboard-geo-field">
                  <span className="label">State</span>
                  <select
                    className="select select-sm"
                    value={stateKey}
                    onChange={(e) => onStateChange(e.target.value)}
                    disabled={isField}
                    aria-label="State"
                  >
                    {(states || Object.values(STATE_META)).map((s) => (
                      <option key={s.key || s.id} value={s.key}>{s.name}</option>
                    ))}
                  </select>
                </label>
                <label className="dashboard-geo-field">
                  <span className="label">District</span>
                  <select
                    className="select select-sm"
                    value={districtId}
                    onChange={(e) => setDistrictId(e.target.value)}
                    disabled={!stateKey || (isField && (fieldDistrictIds || []).length <= 1)}
                    aria-label="District"
                  >
                    {!isField && (
                      <option value="">{stateKey ? "Entire state" : "Select a state first"}</option>
                    )}
                    {(isField
                      ? (districts || []).filter((d) => (fieldDistrictIds || []).includes(String(d.district_id)))
                      : (districts || [])
                    ).map((d) => (
                      <option key={d.district_id} value={d.district_id}>{d.name}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="dashboard-map-with-lengths">
                <div className="dashboard-map-main">
                  <PlainMap
                    center={mapCenter}
                    zoom={mapZoom}
                    bounds={mapBounds}
                    fitKey={`dash-${stateKey || "in"}-${districtId || "none"}`}
                    height={420}
                    emptyMessage="Select Andhra Pradesh or Telangana, then a district to zoom in."
                  />
                </div>
                <div className="dashboard-length-stack">
                  <RoadLengthPanel lengths={lengths} loading={loadingLengths} />
                  {districtId ? (
                    <RoadLengthPanel
                      lengths={districtLengths}
                      loading={loadingDistrictLengths}
                    />
                  ) : null}
                </div>
              </div>
            </div>
          </MotionCard>

          {!isField && (
            <div className="dashboard-layout">
              <div className="dashboard-stack">
                <MotionCard className="card" delay={0.15}>
                  <CardHeader title="Work order status breakdown" />
                  <div className="card-body chart-box" style={{ height: 220 }}>
                    <Doughnut
                      data={{
                        labels: ["Unassigned", "Allocated", "WIP", "Completed", "Verified", "Failed"],
                        datasets: [{
                          data: [
                            data.kpi.unassigned || 0, data.kpi.allocated || 0, data.kpi.wip || 0,
                            data.kpi.completed || 0, data.kpi.verified || 0, data.kpi.failed || 0,
                          ],
                          backgroundColor: ["#8a97a3", "#5b7ea3", "#c98a1a", "#0f3a5f", "#1e7a4c", "#b3261e"],
                          borderWidth: 0,
                        }],
                      }}
                      options={{
                        plugins: {
                          legend: {
                            position: "bottom",
                            labels: { color: "#5b6b78", boxWidth: 12, font: { size: 11 } },
                          },
                        },
                        maintainAspectRatio: false,
                      }}
                    />
                  </div>
                </MotionCard>
              </div>

              <div className="dashboard-side">
                <MotionCard id="sla-breaches" className="card" delay={0.2}>
                  <CardHeader
                    title="SLA breaches"
                    badge={data.breached?.length ? <span className="badge badge-failed">{data.breached.length}</span> : null}
                  />
                  {data.breached?.length ? (
                    <div className="card-body-flush">
                      <div className="list-group">
                        {data.breached.slice(0, 5).map((b) => (
                          <Link key={b.id} to={`/tasks/${b.id}`} className="list-group-item sla-breach">
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <strong>#{b.id} {b.sla_tier || ""}</strong>
                              <span className="text-danger">{b.hours_overdue != null ? `${Number(b.hours_overdue).toFixed(1)}h over` : "Overdue"}</span>
                            </div>
                            <div className="muted">{b.vendor_name || "Unassigned"} · {b.city || "—"}</div>
                          </Link>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="card-body"><p className="empty-inline">None</p></div>
                  )}
                </MotionCard>

                {data.warranty?.length > 0 && (
                  <MotionCard className="card" delay={0.22}>
                    <CardHeader
                      title="Warranty status"
                      badge={<span className="badge badge-created">{data.warranty.length}</span>}
                    />
                    <div className="card-body-flush">
                      <div className="list-group">
                        {data.warranty.slice(0, 6).map((w) => (
                          <div key={w.id} className="list-group-item">
                            <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
                              <span>WO #{w.work_order_id}</span>
                              <StatusBadge status={w.warranty_status || "Valid"} />
                            </div>
                            <div className="muted" style={{ marginTop: "0.25rem" }}>
                              {w.vendor_name || "—"} · Exp: {w.warranty_expiry_date?.slice?.(0, 10) || w.warranty_expiry_date || "—"}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </MotionCard>
                )}
              </div>
            </div>
          )}

          {!isField && (
            <MotionCard className="card" delay={0.25}>
              <CardHeader
                title="Vendor performance scorecard"
                actions={<Link to="/vendors" className="btn btn-sm">View all</Link>}
              />
              <div className="table-wrap table-template">
                <table>
                  <thead>
                    <tr>
                      <th>Vendor</th><th>Score</th><th>Active</th><th>Verified</th>
                      <th>On-time</th><th>Avg hours</th><th>Failed</th><th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.vendors || []).map((v) => (
                      <tr key={v.id}>
                        <td><Link to={`/vendors/${v.id}`}><strong>{v.company_name}</strong></Link></td>
                        <td>
                          <div className="score-bar" style={{ maxWidth: 100, display: "inline-block", verticalAlign: "middle", marginRight: "0.5rem" }}>
                            <div className="score-fill" style={{ width: `${v.performance_score || 0}%` }} />
                          </div>
                          {Math.round(v.performance_score || 0)}
                        </td>
                        <td>{v.active_tasks ?? 0}</td>
                        <td>{v.verified_tasks ?? 0}</td>
                        <td>{onTimePct(v)}</td>
                        <td>{v.avg_completion_hours ?? "—"}</td>
                        <td>{v.failed_tasks ? <span className="text-danger">{v.failed_tasks}</span> : 0}</td>
                        <td><StatusBadge status={v.status || "Active"} /></td>
                      </tr>
                    ))}
                    {!data.vendors?.length && (
                      <tr><td colSpan={8} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>No vendors yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </MotionCard>
          )}
        </>
      )}
    </PageShell>
  );
}
