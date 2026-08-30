import { useState, useMemo, useEffect } from "react";
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from "chart.js";
import { Doughnut } from "react-chartjs-2";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import Modal from "../components/Modal";
import Loader from "../components/Loader";
import { MotionCard, stagger } from "../components/PageTransition";
import "./AdminDashboard.css";

ChartJS.register(ArcElement, Tooltip, Legend);

const RANGE_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "overall", label: "Overall" },
  { value: "custom", label: "Custom" },
];

function pageTokens(page, pageCount) {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i + 1);
  const out = [];
  let prev = null;
  for (const n of [1, page - 1, page, page + 1, pageCount]) {
    if (n < 1 || n > pageCount) continue;
    if (prev != null && n - prev > 1) out.push("…");
    if (prev === n) continue;
    out.push(n);
    prev = n;
  }
  return out;
}

function TablePager({ page, pageCount, total, onChange }) {
  if (pageCount <= 1) return null;
  const jump = () => {
    const raw = window.prompt(`Go to page (1–${pageCount}):`, String(page));
    if (raw == null) return;
    const n = Number.parseInt(String(raw).trim(), 10);
    if (Number.isFinite(n) && n >= 1 && n <= pageCount) onChange(n);
  };
  return (
    <div className="admin-dash-pager">
      <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        Previous
      </button>
      <div className="admin-dash-page-nums">
        {pageTokens(page, pageCount).map((tok, i) => (
          tok === "…" ? (
            <button key={`e-${i}`} type="button" className="btn btn-sm" onClick={jump} title="Jump to page">…</button>
          ) : (
            <button
              key={tok}
              type="button"
              className={`btn btn-sm${tok === page ? " btn-primary" : ""}`}
              onClick={() => onChange(tok)}
              aria-current={tok === page ? "page" : undefined}
            >
              {tok}
            </button>
          )
        ))}
      </div>
      <button type="button" className="btn btn-sm" disabled={page >= pageCount} onClick={() => onChange(page + 1)}>
        Next
      </button>
      <span className="muted admin-dash-pager-total">{total} total</span>
    </div>
  );
}

function PanelShell({ title, onClose, children }) {
  return (
    <div className="card admin-dash-inline-panel">
      <div className="admin-dash-inline-header">
        <h3 className="admin-dash-inline-title">{title}</h3>
        <button type="button" className="btn btn-sm" onClick={onClose} aria-label="Close panel">
          Close
        </button>
      </div>
      <div className="admin-dash-inline-body">
        {children}
      </div>
    </div>
  );
}

function VideosListPanel({ active, rangeQs, onOpenDetails, onClose, onRequeued }) {
  const [page, setPage] = useState(1);
  const [requeueRow, setRequeueRow] = useState(null);
  const [requeueBusy, setRequeueBusy] = useState(false);
  const [requeueError, setRequeueError] = useState("");

  useEffect(() => {
    if (active) setPage(1);
  }, [active, rangeQs]);

  const { data, loading, error, reload } = useAsync(
    () => (active ? api.adminDashboardVideos(`?${rangeQs}&page=${page}`) : Promise.resolve(null)),
    [active, rangeQs, page],
  );

  const closeRequeue = () => {
    if (requeueBusy) return;
    setRequeueRow(null);
    setRequeueError("");
  };

  const confirmRequeue = async () => {
    if (!requeueRow) return;
    setRequeueBusy(true);
    setRequeueError("");
    try {
      await api.adminDashboardVideoRequeue(requeueRow.id);
      setRequeueRow(null);
      reload();
      onRequeued?.();
    } catch (err) {
      setRequeueError(err?.message || "Requeue failed");
    } finally {
      setRequeueBusy(false);
    }
  };

  return (
    <PanelShell title="Videos done" onClose={onClose}>
      {loading && <Loader label="Loading videos" />}
      {error && <p className="alert alert-error">{String(error.message || error)}</p>}
      {!loading && data && (
        <>
          <div className="table-wrap admin-dash-videos-table">
            <table>
              <thead>
                <tr>
                  <th>Video</th>
                  <th>From → To</th>
                  <th style={{ width: 140 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {(data.items || []).map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong className="admin-dash-video-name">{row.video_name}</strong>
                      <div className="muted admin-dash-video-meta">{row.processed_at || "—"}</div>
                    </td>
                    <td>
                      <div className="admin-dash-route-cell">
                        <span>{row.from_label}</span>
                        <span className="muted"> → </span>
                        <span>{row.to_label}</span>
                      </div>
                    </td>
                    <td>
                      <div className="admin-dash-video-actions">
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={() => onOpenDetails(row.id)}
                        >
                          Details
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm admin-dash-requeue-btn"
                          title="Requeue for re-detection"
                          aria-label={`Requeue ${row.video_name}`}
                          onClick={() => {
                            setRequeueError("");
                            setRequeueRow(row);
                          }}
                        >
                          <i className="bi bi-arrow-clockwise" aria-hidden />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!data.items?.length && (
                  <tr>
                    <td colSpan={3} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                      No videos for this range.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <TablePager
            page={data.page || page}
            pageCount={data.page_count || 1}
            total={data.total || 0}
            onChange={setPage}
          />
        </>
      )}

      <Modal
        open={Boolean(requeueRow)}
        onClose={closeRequeue}
        title="Requeue video for re-detection"
      >
        <div className="modal-body">
          {requeueError && <div className="alert alert-error">{requeueError}</div>}
          <p>
            Move <strong>{requeueRow?.video_name || "this video"}</strong> back to the input
            bucket for re-detection?
          </p>
          <p style={{ marginTop: "0.65rem" }}>
            This will restore the video to <strong>smart-road-videos</strong>, permanently delete
            it from <strong>smart-road-videos-processed</strong>, and remove its database records.
            This cannot be undone.
          </p>
        </div>
        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-danger"
            onClick={confirmRequeue}
            disabled={requeueBusy}
          >
            {requeueBusy ? "Requeueing…" : "Confirm requeue"}
          </button>
          <button type="button" className="btn" onClick={closeRequeue} disabled={requeueBusy}>
            Cancel
          </button>
        </div>
      </Modal>
    </PanelShell>
  );
}

function RoadConditionChart({ detail }) {
  const chartData = useMemo(() => {
    if (!detail) return null;
    const good = Math.max(0, Number(detail.good_pct) || 0);
    const bad = Math.max(0, Number(detail.damaged_pct) || 0);
    return {
      labels: ["Good / clear", "Damaged"],
      datasets: [{
        data: [good, bad],
        backgroundColor: ["#1e7a4c", "#b3261e"],
        borderWidth: 0,
      }],
    };
  }, [detail]);

  if (!detail || !chartData) return null;

  return (
    <div className="admin-dash-video-graph">
      <div className="admin-dash-graph-box admin-dash-graph-box-inline">
        <Doughnut
          data={chartData}
          options={{
            plugins: {
              legend: {
                position: "bottom",
                labels: { color: "#5b6b78", boxWidth: 12, font: { size: 12 } },
              },
              tooltip: {
                callbacks: {
                  label: (ctx) => `${ctx.label}: ${Number(ctx.raw).toFixed(1)}%`,
                },
              },
            },
            maintainAspectRatio: false,
          }}
        />
      </div>
      <div className="admin-dash-graph-stats">
        <div>
          <strong>{Number(detail.proper_km || 0).toFixed(2)} km</strong>
          <span className="muted"> Clear</span>
        </div>
        <div>
          <strong>{Number(detail.affected_km || 0).toFixed(2)} km</strong>
          <span className="muted"> Affected</span>
        </div>
        <div>
          <strong>{Number(detail.km_covered || 0).toFixed(2)} km</strong>
          <span className="muted"> Analysed</span>
        </div>
      </div>
    </div>
  );
}

function VideoDetailModal({ open, sessionId, onClose, onPotholesClick, rangeQs }) {
  const [dlBusy, setDlBusy] = useState(false);
  const [dlError, setDlError] = useState("");
  const { data, loading, error } = useAsync(
    () => (open && sessionId ? api.adminDashboardVideoDetail(sessionId) : Promise.resolve(null)),
    [open, sessionId],
  );

  const mediaUrl = data?.output_video_url || data?.output_image_url || null;
  const isImage = data?.media_kind === "image" && !data?.output_video_url;

  const downloadFrames = async (severity = "all") => {
    if (!sessionId || dlBusy) return;
    setDlBusy(severity);
    setDlError("");
    try {
      let qs = `?${rangeQs || "range=overall"}&session_id=${encodeURIComponent(sessionId)}`;
      if (severity && severity !== "all") qs += `&severity=${encodeURIComponent(severity)}`;
      await api.downloadAdminPotholeImages(qs);
    } catch (e) {
      setDlError(String(e?.message || e));
    } finally {
      setDlBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={data?.video_name || "Video details"}
      className="modal-detail admin-dash-video-detail-modal"
    >
      <div className="modal-body admin-dash-modal-body admin-dash-video-detail-body">
        {loading && <Loader label="Loading details" />}
        {error && <p className="alert alert-error">{String(error.message || error)}</p>}
        {!loading && data && (
          <div className="admin-dash-video-detail-split">
            <div className="admin-dash-video-detail-media">
              {mediaUrl ? (
                isImage ? (
                  <img src={mediaUrl} alt="" className="admin-dash-video-detail-img" />
                ) : (
                  <video src={mediaUrl} controls className="admin-dash-video-detail-player" />
                )
              ) : (
                <div className="admin-dash-video-detail-empty muted">
                  Annotated output not available for this session.
                </div>
              )}
            </div>
            <div className="admin-dash-video-detail-info">
              <div className="admin-dash-report">
                <div className="admin-dash-report-header">
                  <div className="admin-dash-report-avatar" aria-hidden="true">
                    {(data.full_name || data.username || "?").trim().charAt(0).toUpperCase()}
                  </div>
                  <div className="admin-dash-report-header-text">
                    <strong className="admin-dash-report-name">{data.full_name || data.username || "—"}</strong>
                    {data.username && data.full_name && data.username !== data.full_name && (
                      <span className="muted"> @{data.username}</span>
                    )}
                    <div className="admin-dash-report-route">
                      <i className="bi bi-geo-alt" aria-hidden="true" />
                      <span>{data.from_label}</span>
                      <i className="bi bi-arrow-right admin-dash-report-route-arrow" aria-hidden="true" />
                      <span>{data.to_label}</span>
                    </div>
                    {data.processed_at && (
                      <div className="admin-dash-report-date muted">{data.processed_at}</div>
                    )}
                  </div>
                </div>

                <div className="admin-dash-report-hero">
                  <div className="admin-dash-report-hero-stat">
                    <span className="admin-dash-report-hero-value">{Number(data.km_covered || 0).toFixed(2)}</span>
                    <span className="admin-dash-report-hero-label">KM covered</span>
                  </div>
                  <div className="admin-dash-report-hero-divider" aria-hidden="true" />
                  <div className="admin-dash-report-hero-stat">
                    <span className="admin-dash-report-hero-value">{data.potholes?.total ?? 0}</span>
                    <span className="admin-dash-report-hero-label">Potholes found</span>
                  </div>
                </div>

                <div className="admin-dash-report-section">
                  <span className="admin-dash-report-section-title">Potholes by severity</span>
                  <div className="admin-dash-sev-grid">
                    {[
                      { key: "high", label: "High", count: data.potholes?.high ?? 0, cls: "admin-dash-sev-high" },
                      { key: "medium", label: "Medium", count: data.potholes?.medium ?? 0, cls: "admin-dash-sev-med" },
                      { key: "low", label: "Low", count: data.potholes?.low ?? 0, cls: "admin-dash-sev-low" },
                      { key: "all", label: "Total", count: data.potholes?.total ?? 0, cls: "" },
                    ].map((s) => (
                      <div key={s.key} className="admin-dash-sev-with-dl">
                        <button
                          type="button"
                          className={`admin-dash-sev-chip admin-dash-sev-clickable ${s.cls}`.trim()}
                          disabled={!(s.count > 0)}
                          onClick={() => onPotholesClick?.({ sessionId: data.id, severity: s.key })}
                        >
                          <strong>{s.count}</strong>
                          <span>{s.label}</span>
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-secondary admin-dash-sev-dl-btn"
                          disabled={Boolean(dlBusy) || !(s.count > 0)}
                          title={`Download ${s.label} images`}
                          aria-label={`Download ${s.label} images`}
                          onClick={() => downloadFrames(s.key)}
                        >
                          <i className="bi bi-download" aria-hidden="true" />
                          {dlBusy === s.key ? "…" : ""}
                        </button>
                      </div>
                    ))}
                  </div>
                  <p className="muted admin-dash-frames-download-hint">
                    Each download is a zip with <code>with_overlay</code> and <code>without_overlay</code> folders.
                    {dlBusy ? " Preparing zip…" : ""}
                  </p>
                  {dlError && <p className="alert alert-error" style={{ marginTop: "0.5rem" }}>{dlError}</p>}
                </div>

                <div className="admin-dash-report-section">
                  <span className="admin-dash-report-section-title">Road condition</span>
                  <RoadConditionChart detail={data} />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function MembersListPanel({ active, rangeQs, onClose }) {
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (active) setPage(1);
  }, [active, rangeQs]);

  const { data, loading, error } = useAsync(
    () => (active ? api.adminDashboardMembers(`?${rangeQs}&page=${page}`) : Promise.resolve(null)),
    [active, rangeQs, page],
  );

  return (
    <PanelShell title="Members active" onClose={onClose}>
      {loading && <Loader label="Loading members" />}
      {error && <p className="alert alert-error">{String(error.message || error)}</p>}
      {!loading && data && (
        <>
          <div className="table-wrap admin-dash-videos-table">
            <table>
              <thead>
                <tr>
                  <th>Videographer</th>
                  <th>Mobile</th>
                  <th>Village</th>
                  <th>Status</th>
                  <th>Rating</th>
                </tr>
              </thead>
              <tbody>
                {(data.items || []).map((row) => (
                  <tr key={row.user_id}>
                    <td>
                      <strong>{row.name}</strong>
                      <div className="muted admin-dash-video-meta">
                        @{row.username}
                        {row.day_videos > 0
                          ? ` · ${row.day_videos} video${row.day_videos === 1 ? "" : "s"} uploaded`
                          : ` · ${Number(row.covered_km || 0).toFixed(1)} km tracking`}
                      </div>
                    </td>
                    <td>{row.mobile}</td>
                    <td>{row.village}</td>
                    <td>
                      <span className={`admin-dash-status ${row.status === "Active" ? "admin-dash-status-active" : "admin-dash-status-capturing"}`}>
                        {row.status || "Capturing"}
                      </span>
                    </td>
                    <td>
                      <span className="admin-dash-rating" title={`${row.days_active_14}/14 capture days · ${row.videos_14} videos (14d)`}>
                        <strong>{Number(row.rating).toFixed(1)}</strong>
                        <span className="muted"> / 5</span>
                      </span>
                    </td>
                  </tr>
                ))}
                {!data.items?.length && (
                  <tr>
                    <td colSpan={5} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                      No members started capture this day.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <TablePager
            page={data.page || page}
            pageCount={data.page_count || 1}
            total={data.total || 0}
            onChange={setPage}
          />
        </>
      )}
    </PanelShell>
  );
}

function formatSharePct(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return null;
  const n = Number(pct);
  if (n <= 0) return null;
  if (n < 0.01) return `${n.toFixed(4)}%`;
  if (n < 1) return `${n.toFixed(2)}%`;
  return `${n.toFixed(1)}%`;
}

function KmClassDetailModal({ open, onClose, rangeQs, roadClass, label }) {
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (open) setPage(1);
  }, [open, rangeQs, roadClass]);

  const { data, loading, error } = useAsync(
    () => (
      open && roadClass
        ? api.adminDashboardKmCoverageClass(roadClass, `?${rangeQs}&page=${page}`)
        : Promise.resolve(null)
    ),
    [open, rangeQs, roadClass, page],
  );

  const showNumber = roadClass === "nh" || roadClass === "sh" || roadClass === "mdr";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`${label || "Roads"} — detail`}
      className="modal-detail admin-dash-videos-modal"
    >
      <div className="modal-body admin-dash-modal-body">
        {loading && <Loader label="Loading roads" />}
        {error && <p className="alert alert-error">{String(error.message || error)}</p>}
        {!loading && data && (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              {Number(data.covered_km || 0).toFixed(2)} km in this class
              {data.day_total_km != null && (
                <> · day total {Number(data.day_total_km || 0).toFixed(2)} km</>
              )}
            </p>
            <p className="muted admin-dash-km-hint">
              Share of day = this road’s km ÷ day’s total covered km.
              Network % (when shown) = covered ÷ that road’s GIS length — often tiny on local roads.
            </p>
            <div className="table-wrap admin-dash-videos-table">
              <table>
                <thead>
                  <tr>
                    {showNumber && <th>No.</th>}
                    <th>Road</th>
                    <th>Videographers</th>
                    <th>Covered</th>
                    <th>Share of day</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.items || []).map((row, i) => (
                    <tr key={`${row.ref || row.name}-${i}`}>
                      {showNumber && (
                        <td>
                          <strong>{row.number || row.ref || "—"}</strong>
                        </td>
                      )}
                      <td>
                        <strong>{row.name}</strong>
                        {row.district_name && (
                          <div className="muted admin-dash-video-meta">{row.district_name}</div>
                        )}
                        {row.pct_covered != null && row.total_km != null && (
                          <div className="muted admin-dash-video-meta">
                            Network {formatSharePct(row.pct_covered)} of {Number(row.total_km).toFixed(1)} km
                          </div>
                        )}
                      </td>
                      <td>
                        {(row.contributors || []).length ? (
                          <ul className="admin-dash-km-users">
                            {(row.contributors || []).map((c) => (
                              <li key={c.user_id}>
                                <strong>@{c.username}</strong>
                                {c.full_name && c.full_name !== c.username && (
                                  <span className="muted"> · {c.full_name}</span>
                                )}
                                <div className="muted admin-dash-video-meta">
                                  {Number(c.covered_km || 0).toFixed(2)} km
                                  {formatSharePct(c.share_of_day_pct) && (
                                    <> · {formatSharePct(c.share_of_day_pct)} of day</>
                                  )}
                                </div>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>{Number(row.covered_km || 0).toFixed(2)} km</td>
                      <td>
                        {formatSharePct(row.share_of_day_pct) || <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                  {!data.items?.length && (
                    <tr>
                      <td colSpan={showNumber ? 5 : 4} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                        No road coverage in this class for the day.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <TablePager
              page={data.page || page}
              pageCount={data.page_count || 1}
              total={data.total || 0}
              onChange={setPage}
            />
          </>
        )}
      </div>
    </Modal>
  );
}

function KmCoveragePanel({ active, rangeQs, onViewDetail, onClose }) {
  const { data, loading, error } = useAsync(
    () => (active ? api.adminDashboardKmCoverage(`?${rangeQs}`) : Promise.resolve(null)),
    [active, rangeQs],
  );

  return (
    <PanelShell title="KM covered" onClose={onClose}>
      {loading && <Loader label="Loading coverage" />}
      {error && <p className="alert alert-error">{String(error.message || error)}</p>}
      {!loading && data && (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            Total {Number(data.total_km || 0).toFixed(2)} km
          </p>
          <p className="muted admin-dash-km-hint">
            Share = class km ÷ total covered km for the selected range.
          </p>
          <div className="table-wrap admin-dash-videos-table">
            <table>
              <thead>
                <tr>
                  <th>Class</th>
                  <th>Covered</th>
                  <th>Share of day</th>
                  <th style={{ width: 120 }} />
                </tr>
              </thead>
              <tbody>
                {(data.classes || []).map((row) => (
                  <tr key={row.key}>
                    <td><strong>{row.label}</strong></td>
                    <td>{Number(row.covered_km || 0).toFixed(2)} km</td>
                    <td>{Number(row.pct_of_total || 0).toFixed(1)}%</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => onViewDetail(row)}
                        disabled={!(row.covered_km > 0)}
                      >
                        View detail
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </PanelShell>
  );
}

function PotholesSummaryPanel({ active, rangeQs, onSeverityClick, onClose }) {
  const [dlBusy, setDlBusy] = useState(false);
  const [dlError, setDlError] = useState("");
  const { data, loading, error } = useAsync(
    () => (active ? api.adminDashboardPotholes(`?${rangeQs}`) : Promise.resolve(null)),
    [active, rangeQs],
  );

  const downloadFrames = async (severity = "all") => {
    if (dlBusy) return;
    setDlBusy(severity);
    setDlError("");
    try {
      let qs = `?${rangeQs}`;
      if (severity && severity !== "all") qs += `&severity=${encodeURIComponent(severity)}`;
      await api.downloadAdminPotholeImages(qs);
    } catch (e) {
      setDlError(String(e?.message || e));
    } finally {
      setDlBusy(false);
    }
  };

  return (
    <PanelShell title="Potholes" onClose={onClose}>
      {loading && <Loader label="Loading potholes" />}
      {error && <p className="alert alert-error">{String(error.message || error)}</p>}
      {!loading && data && (
        <>
          <div className="admin-dash-sev-grid">
            {(data.severities || []).map((s) => (
              <div key={s.key} className="admin-dash-sev-with-dl">
                <button
                  type="button"
                  className={`admin-dash-sev-chip admin-dash-sev-clickable ${
                    s.key === "high" ? "admin-dash-sev-high" : s.key === "medium" ? "admin-dash-sev-med" : "admin-dash-sev-low"
                  }`}
                  disabled={!(s.count > 0)}
                  onClick={() => onSeverityClick(s.key)}
                >
                  <strong>{s.count}</strong>
                  <span>{s.label}</span>
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary admin-dash-sev-dl-btn"
                  disabled={Boolean(dlBusy) || !(s.count > 0)}
                  title={`Download ${s.label} images`}
                  aria-label={`Download ${s.label} images`}
                  onClick={() => downloadFrames(s.key)}
                >
                  <i className="bi bi-download" aria-hidden="true" />
                  {dlBusy === s.key ? "…" : ""}
                </button>
              </div>
            ))}
            <div className="admin-dash-sev-with-dl">
              <div className="admin-dash-sev-chip">
                <strong>{data.total ?? 0}</strong>
                <span>Total</span>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-secondary admin-dash-sev-dl-btn"
                disabled={Boolean(dlBusy) || !(data.total > 0)}
                title="Download all images"
                aria-label="Download all images"
                onClick={() => downloadFrames("all")}
              >
                <i className="bi bi-download" aria-hidden="true" />
                {dlBusy === "all" ? "…" : ""}
              </button>
            </div>
          </div>
          <p className="muted admin-dash-frames-download-hint">
            Download High / Medium / Low / Total separately — zip has <code>with_overlay</code> and <code>without_overlay</code>.
            {dlBusy ? " Preparing zip…" : ""}
          </p>
          {dlError && <p className="alert alert-error" style={{ marginTop: "0.5rem" }}>{dlError}</p>}
        </>
      )}
    </PanelShell>
  );
}

const SEVERITY_TITLES = {
  high: "High potholes",
  medium: "Medium potholes",
  low: "Low potholes",
  all: "Pothole details",
};

function PotholesDetailModal({ open, onClose, rangeQs, severity = "all", sessionId = null, title }) {
  const [page, setPage] = useState(1);
  const [lightboxUrl, setLightboxUrl] = useState(null);
  const [dlBusy, setDlBusy] = useState(false);
  const [dlError, setDlError] = useState("");

  useEffect(() => {
    if (open) setPage(1);
    else {
      setLightboxUrl(null);
      setDlError("");
    }
  }, [open, rangeQs, severity, sessionId]);

  useEffect(() => {
    if (!lightboxUrl) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setLightboxUrl(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [lightboxUrl]);

  const query = useMemo(() => {
    let qs = `?${rangeQs}&page=${page}`;
    if (severity && severity !== "all") qs += `&severity=${encodeURIComponent(severity)}`;
    if (sessionId) qs += `&session_id=${encodeURIComponent(sessionId)}`;
    return qs;
  }, [rangeQs, page, severity, sessionId]);

  const zipQuery = useMemo(() => {
    let qs = `?${rangeQs}`;
    if (severity && severity !== "all") qs += `&severity=${encodeURIComponent(severity)}`;
    if (sessionId) qs += `&session_id=${encodeURIComponent(sessionId)}`;
    return qs;
  }, [rangeQs, severity, sessionId]);

  const { data, loading, error } = useAsync(
    () => (open ? api.adminDashboardPotholeDetails(query) : Promise.resolve(null)),
    [open, query],
  );

  const modalTitle = title || SEVERITY_TITLES[severity] || SEVERITY_TITLES.all;

  const downloadFrames = async () => {
    if (dlBusy) return;
    setDlBusy(true);
    setDlError("");
    try {
      await api.downloadAdminPotholeImages(zipQuery);
    } catch (e) {
      setDlError(String(e?.message || e));
    } finally {
      setDlBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={modalTitle} className="modal-detail admin-dash-potholes-modal">
      <div className="modal-body admin-dash-modal-body">
        {loading && <Loader label="Loading details" />}
        {error && <p className="alert alert-error">{String(error.message || error)}</p>}
        {!loading && data && (
          <>
            <div className="admin-dash-frames-download admin-dash-frames-download-top">
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                disabled={dlBusy || !(data.total > 0)}
                onClick={downloadFrames}
              >
                <i className="bi bi-download" aria-hidden="true" />
                {dlBusy ? " Preparing zip…" : " Download images"}
              </button>
              <span className="muted admin-dash-frames-download-hint">
                Zip for this filter: <code>with_overlay</code> + <code>without_overlay</code>
              </span>
              {dlError && <p className="alert alert-error" style={{ marginTop: "0.5rem" }}>{dlError}</p>}
            </div>
            <div className="table-wrap admin-dash-videos-table admin-dash-pothole-table">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 56 }}>Frame</th>
                    <th>Pothole</th>
                    <th>Map</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.items || []).map((row) => (
                    <tr key={row.id}>
                      <td>
                        {row.frame_url ? (
                          <button
                            type="button"
                            className="admin-dash-pothole-thumb-btn"
                            onClick={() => setLightboxUrl(row.frame_url)}
                            aria-label="Enlarge pothole frame"
                          >
                            <img src={row.frame_url} alt="" className="admin-dash-pothole-thumb" loading="lazy" />
                          </button>
                        ) : (
                          <span className="admin-dash-pothole-thumb-empty">—</span>
                        )}
                      </td>
                      <td>
                        <strong className={`admin-dash-sev-inline admin-dash-sev-inline-${String(row.severity || "").toLowerCase()}`}>
                          {row.severity}
                        </strong>
                        <div className="muted admin-dash-video-meta">
                          {row.place || row.video_name || "—"}
                          {row.username ? ` · @${row.username}` : ""}
                        </div>
                      </td>
                      <td>
                        {row.map_link ? (
                          <a href={row.map_link} target="_blank" rel="noreferrer" className="btn btn-sm">
                            Map
                          </a>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!data.items?.length && (
                    <tr>
                      <td colSpan={3} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                        No potholes for this day.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <TablePager
              page={data.page || page}
              pageCount={data.page_count || 1}
              total={data.total || 0}
              onChange={setPage}
            />
          </>
        )}
      </div>

      {lightboxUrl && (
        <div
          className="admin-dash-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="Pothole frame"
          onClick={() => setLightboxUrl(null)}
        >
          <button
            type="button"
            className="admin-dash-lightbox-close"
            onClick={() => setLightboxUrl(null)}
            aria-label="Close"
          >
            ×
          </button>
          <img
            src={lightboxUrl}
            alt="Pothole frame"
            className="admin-dash-lightbox-img"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </Modal>
  );
}

const PANEL_BY_CARD = {
  videos_count: "videos",
  active_members: "members",
  km_covered: "km",
  potholes_count: "potholes",
};

export default function AdminDashboard() {
  const [range, setRange] = useState("today");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [activePanel, setActivePanel] = useState(null);
  const [kmClass, setKmClass] = useState(null);
  const [potholeModal, setPotholeModal] = useState({ open: false, severity: "all", sessionId: null, title: null });
  const [detailId, setDetailId] = useState(null);

  const queryParams = useMemo(() => {
    let qs = `?range=${range}`;
    if (range === "custom" && customStart && customEnd) {
      qs += `&start=${customStart}&end=${customEnd}`;
    }
    return qs;
  }, [range, customStart, customEnd]);

  const rangeQs = useMemo(() => queryParams.replace(/^\?/, ""), [queryParams]);

  const { data, loading, error, reload } = useAsync(
    () => api.adminDashboard(queryParams),
    [queryParams],
  );

  const canDrilldown =
    range === "today" ||
    range === "yesterday" ||
    range === "overall" ||
    (range === "custom" && Boolean(customStart && customEnd));

  const cards = [
    { key: "videos_count", label: "Videos Done", icon: "bi-camera-video", color: "kpi-border-primary", clickable: canDrilldown },
    { key: "active_members", label: "Members Active", icon: "bi-people", color: "kpi-border-success", clickable: canDrilldown },
    { key: "km_covered", label: "KM Covered", icon: "bi-signpost-2", color: "kpi-border-info", clickable: canDrilldown },
    { key: "potholes_count", label: "Potholes Count", icon: "bi-exclamation-triangle", color: "kpi-border-warning", clickable: canDrilldown },
  ];

  const clearDrilldowns = () => {
    setActivePanel(null);
    setKmClass(null);
    setPotholeModal({ open: false, severity: "all", sessionId: null, title: null });
    setDetailId(null);
  };

  const openPotholeModal = ({ severity = "all", sessionId = null, title = null } = {}) => {
    setPotholeModal({ open: true, severity, sessionId, title });
  };

  const onCardClick = (card) => {
    if (!card.clickable) return;
    const panel = PANEL_BY_CARD[card.key];
    if (!panel) return;
    setActivePanel((prev) => (prev === panel ? null : panel));
  };

  return (
    <PageShell
      title="Admin Dashboard"
      subtitle="Field tracking overview"
      skeleton="dashboard"
      loading={loading}
      error={error}
      actions={<button type="button" className="btn btn-sm" onClick={reload}>Refresh</button>}
    >
      <div className="admin-dash-filters">
        {RANGE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={`btn btn-sm${range === opt.value ? " btn-active" : ""}`}
            onClick={() => {
              setRange(opt.value);
              clearDrilldowns();
            }}
          >
            {opt.label}
          </button>
        ))}
        {range === "custom" && (
          <span className="admin-dash-custom-dates">
            <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
            <span>to</span>
            <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
          </span>
        )}
      </div>

      {data && (
        <motion.div className="grid grid-kpi admin-dash-kpi-grid" variants={stagger} initial="initial" animate="animate">
          {cards.map((card, i) => {
            const panel = PANEL_BY_CARD[card.key];
            const selected = canDrilldown && activePanel === panel;
            return (
              <MotionCard
                key={card.key}
                className={`card card-glow ${card.color}${selected ? " admin-dash-kpi-selected" : ""}`}
                delay={i * 0.06}
              >
                <button
                  type="button"
                  className={`admin-dash-kpi-card${card.clickable ? " admin-dash-kpi-clickable" : ""}${selected ? " is-selected" : ""}`}
                  onClick={() => onCardClick(card)}
                  aria-pressed={selected}
                  aria-label={card.clickable ? `Show ${card.label}` : card.label}
                >
                  <i className={`bi ${card.icon} admin-dash-kpi-icon`} aria-hidden />
                  <div className="kpi-value">{data[card.key] ?? 0}</div>
                  <div className="kpi-label">{card.label}</div>
                  {card.clickable && (
                    <span className="admin-dash-kpi-hint">{selected ? "Click to hide" : "Click for details"}</span>
                  )}
                </button>
              </MotionCard>
            );
          })}
        </motion.div>
      )}

      <AnimatePresence mode="wait" initial={false}>
        {canDrilldown && activePanel && (
          <motion.div
            key={activePanel}
            className="admin-dash-inline-slot"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.22, delay: 0.04 }}
            >
              {activePanel === "videos" && (
                <VideosListPanel
                  active
                  rangeQs={rangeQs}
                  onClose={() => setActivePanel(null)}
                  onOpenDetails={(id) => setDetailId(id)}
                  onRequeued={() => {
                    reload();
                    setDetailId(null);
                  }}
                />
              )}
              {activePanel === "members" && (
                <MembersListPanel
                  active
                  rangeQs={rangeQs}
                  onClose={() => setActivePanel(null)}
                />
              )}
              {activePanel === "km" && (
                <KmCoveragePanel
                  active
                  rangeQs={rangeQs}
                  onClose={() => setActivePanel(null)}
                  onViewDetail={(row) => setKmClass(row)}
                />
              )}
              {activePanel === "potholes" && (
                <PotholesSummaryPanel
                  active
                  rangeQs={rangeQs}
                  onClose={() => setActivePanel(null)}
                  onSeverityClick={(sev) => openPotholeModal({ severity: sev })}
                />
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <KmClassDetailModal
        open={Boolean(kmClass)}
        onClose={() => setKmClass(null)}
        rangeQs={rangeQs}
        roadClass={kmClass?.key}
        label={kmClass?.label}
      />
      <PotholesDetailModal
        open={potholeModal.open}
        onClose={() => setPotholeModal({ open: false, severity: "all", sessionId: null, title: null })}
        rangeQs={rangeQs}
        severity={potholeModal.severity}
        sessionId={potholeModal.sessionId}
        title={potholeModal.title}
      />
      <VideoDetailModal
        open={Boolean(detailId)}
        sessionId={detailId}
        rangeQs={rangeQs}
        onClose={() => setDetailId(null)}
        onPotholesClick={({ sessionId, severity }) => {
          const sevTitle = severity === "all"
            ? "Potholes in this video"
            : `${severity.charAt(0).toUpperCase()}${severity.slice(1)} potholes in this video`;
          openPotholeModal({ sessionId, severity, title: sevTitle });
        }}
      />
    </PageShell>
  );
}
