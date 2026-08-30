import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api";
import { useAuth } from "../App";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";
import Modal from "../components/Modal";
import "./Detection.css";
const RESULT_COLUMNS = ["class", "conf", "severity", "lat", "lon", "map_link"];
const SESSION_PAGE_SIZE = 10;
const RESULTS_PAGE_SIZE = 10;

/** Build page number tokens: 1 … 4 5 6 … 20 (ellipsis is a jump target). */
function pageTokens(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const set = new Set([1, total, current, current - 1, current + 1, current - 2, current + 2]);
  const nums = [...set].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  const out = [];
  let prev = 0;
  for (const n of nums) {
    if (prev && n - prev > 1) out.push("…");
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
    <div className="det-images-pager" style={{ marginTop: "0.75rem" }}>
      <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        Previous
      </button>
      <div className="det-page-nums" style={{ display: "flex", gap: "0.25rem", alignItems: "center", flexWrap: "wrap" }}>
        {pageTokens(page, pageCount).map((tok, i) => (
          tok === "…" ? (
            <button key={`e-${i}`} type="button" className="btn btn-sm" onClick={jump} title="Jump to page">
              …
            </button>
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
      <button
        type="button"
        className="btn btn-sm"
        disabled={page >= pageCount}
        onClick={() => onChange(page + 1)}
      >
        Next
      </button>
      <span className="muted" style={{ fontSize: "0.85rem" }}>
        {total} total
      </span>
    </div>
  );
}

function isImageKey(name) {
  return /\.(jpe?g|png|webp|gif|bmp)$/i.test(String(name || ""));
}

function isVideoKey(name) {
  return /\.(mp4|webm|mov|mkv|avi|m4v)$/i.test(String(name || ""));
}

function MediaPreview({
  url,
  name,
  mime,
  className = "det-media",
  empty = "No preview",
}) {
  if (!url) return <p className="det-preview-empty">{empty}</p>;
  const asImage =
    (mime && String(mime).startsWith("image/"))
    || isImageKey(name)
    || /\.(jpe?g|png|webp|gif)(\?|$)/i.test(String(url));
  if (asImage) {
    return <img src={url} alt="Preview" className={className} />;
  }
  return (
    <video
      src={url}
      className={className}
      controls
      playsInline
      preload="metadata"
    />
  );
}

function StatusLine({ status }) {
  if (!status?.message) return null;
  const kind = status.kind === "error" ? "text-danger" : status.kind === "warn" ? "text-warn" : "text-success";
  return <p className={`det-status-line ${kind}`}>{status.message}</p>;
}

function MapLinksBlock({ links, potholes }) {
  const items = (links && links.length)
    ? links
    : (potholes || [])
      .map((p, i) => ({
        index: i,
        url: p.map_link
          || (p.lat != null && p.lon != null ? `https://www.google.com/maps?q=${p.lat},${p.lon}` : null),
        lat: p.lat,
        lon: p.lon,
      }))
      .filter((x) => x.url);

  if (!items.length) return <p className="muted">No map links.</p>;
  return (
    <ul className="det-map-links">
      {items.map((m) => (
        <li key={`${m.index}-${m.url}`}>
          <a href={m.url} target="_blank" rel="noreferrer" className="det-map-link">
            <span className="det-map-link-title">Pothole {m.index + 1}</span>
            <span className="det-map-link-url">{m.url}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

const IMAGES_PAGE_SIZE = 5;

function buildImageRows(potholes, mapLinks) {
  const holes = potholes || [];
  const links = mapLinks || [];
  const n = Math.max(holes.length, links.length);
  const rows = [];
  for (let i = 0; i < n; i += 1) {
    const p = holes[i] || {};
    const link = links[i]?.url
      || p.map_link
      || (p.lat != null && p.lon != null ? `https://www.google.com/maps?q=${p.lat},${p.lon}` : null);
    const img = p.s3_url || null;
    if (!img && !link) continue;
    rows.push({ index: i, img, link });
  }
  return rows;
}

function ImagesTableModal({ open, onClose, title = "Detected images", potholes, mapLinks, gallery }) {
  const [page, setPage] = useState(1);

  const rows = useMemo(() => {
    if (gallery?.length && !(potholes || []).some((p) => p.s3_url)) {
      return gallery.map((url, i) => ({
        index: i,
        img: url,
        link: (mapLinks || [])[i]?.url || (potholes || [])[i]?.map_link || null,
      }));
    }
    return buildImageRows(potholes, mapLinks);
  }, [potholes, mapLinks, gallery]);

  const pageCount = Math.max(1, Math.ceil(rows.length / IMAGES_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const slice = rows.slice(
    (safePage - 1) * IMAGES_PAGE_SIZE,
    safePage * IMAGES_PAGE_SIZE,
  );

  useEffect(() => {
    if (open) setPage(1);
  }, [open]);

  return (
    <Modal open={open} onClose={onClose} title={title} className="modal-form modal-images">
      <div className="det-images-table-wrap table-wrap">
        <table className="det-images-table">
          <thead>
            <tr>
              <th>Detected images</th>
              <th>Map links</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((row) => (
              <tr key={row.index}>
                <td className="det-images-col-img">
                  {row.img ? (
                    <a href={row.img} target="_blank" rel="noreferrer">
                      <img src={row.img} alt={`Detection ${row.index + 1}`} className="det-images-thumb" />
                    </a>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  {row.link ? (
                    <div className="det-images-map-cell">
                      <span>Pothole {row.index + 1}</span>
                      <a href={row.link} target="_blank" rel="noreferrer">{row.link}</a>
                    </div>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
            {!rows.length && (
              <tr>
                <td colSpan={2} className="muted" style={{ textAlign: "center", padding: "1rem" }}>
                  No detection images.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > IMAGES_PAGE_SIZE && (
        <div className="det-images-pager">
          <button
            type="button"
            className="btn btn-sm"
            disabled={safePage <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            Page {safePage} of {pageCount} · {rows.length} total
          </span>
          <button
            type="button"
            className="btn btn-sm"
            disabled={safePage >= pageCount}
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
          >
            Next
          </button>
        </div>
      )}
    </Modal>
  );
}

function ResultsTable({ rows, severityFilter }) {
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => {
    if (!severityFilter) return rows || [];
    return (rows || []).filter(
      (r) => String(r.severity || "").toLowerCase() === severityFilter.toLowerCase(),
    );
  }, [rows, severityFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / RESULTS_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * RESULTS_PAGE_SIZE;
    return filtered.slice(start, start + RESULTS_PAGE_SIZE);
  }, [filtered, safePage]);

  useEffect(() => {
    setPage(1);
  }, [severityFilter, rows]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <>
      <div className="table-wrap table-template det-table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              {RESULT_COLUMNS.map((h) => <th key={h}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, i) => {
              const idx = (safePage - 1) * RESULTS_PAGE_SIZE + i;
              return (
                <tr key={idx}>
                  <td>{idx}</td>
                  {RESULT_COLUMNS.map((h) => (
                    <td key={h} className={h === "map_link" ? "det-cell-link" : undefined}>
                      {h === "map_link" && row[h] ? (
                        <a href={row[h]} target="_blank" rel="noreferrer">Open</a>
                      ) : h === "lat" || h === "lon" ? (
                        row[h] != null && row[h] !== "" ? Number(row[h]).toFixed(5) : "—"
                      ) : h === "conf" && row[h] != null && row[h] !== "" ? (
                        Number(row[h]).toFixed(2)
                      ) : (
                        row[h] ?? "—"
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {!filtered.length && (
              <tr>
                <td colSpan={RESULT_COLUMNS.length + 1} className="muted" style={{ textAlign: "center", padding: "1rem" }}>
                  No detections match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <TablePager
        page={safePage}
        pageCount={pageCount}
        total={filtered.length}
        onChange={setPage}
      />
    </>
  );
}

function parseLatLon(text) {
  const s = String(text || "").trim();
  let m = s.match(/(-?\d+\.?\d+)\s*,\s*(-?\d+\.?\d+)/);
  if (!m) m = s.match(/My location[^\d-]*(-?\d+\.?\d+)\s*,\s*(-?\d+\.?\d+)/i);
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

async function resolvePlaceLabel(raw, potholeCoords) {
  const coords = parseLatLon(raw) || potholeCoords || null;
  if (!coords) return raw ? String(raw) : "";
  try {
    const res = await api.surveyReverseGeocode(coords.lat, coords.lon, { preferPlaces: true });
    return res?.display_name || `${coords.lat.toFixed(5)}, ${coords.lon.toFixed(5)}`;
  } catch {
    return `${coords.lat.toFixed(5)}, ${coords.lon.toFixed(5)}`;
  }
}

function SessionDashboard({
  sourceKind,
  singular,
  sessions,
  status,
  loading,
  onRefresh,
}) {
  const [filterUser, setFilterUser] = useState("");
  const [tablePage, setTablePage] = useState(1);
  const [detail, setDetail] = useState(null);
  const [potholes, setPotholes] = useState([]);
  const [mapLinks, setMapLinks] = useState([]);
  const [routeLabel, setRouteLabel] = useState("");
  const [detailBusy, setDetailBusy] = useState(null);
  const [showDetail, setShowDetail] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const [detailStatus, setDetailStatus] = useState(null);

  const accounts = useMemo(() => {
    const set = new Set();
    for (const s of sessions) {
      const u = (s.username || "").trim();
      if (u) set.add(u);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [sessions]);

  const rows = useMemo(() => {
    if (!filterUser) return [];
    return sessions.filter((s) => s.username === filterUser);
  }, [sessions, filterUser]);

  const pageCount = Math.max(1, Math.ceil(rows.length / SESSION_PAGE_SIZE));
  const safePage = Math.min(tablePage, pageCount);
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * SESSION_PAGE_SIZE;
    return rows.slice(start, start + SESSION_PAGE_SIZE);
  }, [rows, safePage]);

  useEffect(() => {
    setFilterUser("");
    setTablePage(1);
    setDetail(null);
    setPotholes([]);
    setShowDetail(false);
    setShowImages(false);
  }, [sourceKind]);

  useEffect(() => {
    setTablePage(1);
  }, [filterUser]);

  const openDetails = async (session) => {
    setDetailBusy(session.id);
    setDetailStatus(null);
    try {
      const res = await api.detectionSessionDetail(session.id);
      const sess = res.session || session;
      const holes = res.potholes || [];
      setDetail(sess);
      setPotholes(holes);
      setMapLinks(res.map_links || []);
      setDetailStatus(res.status);
      const withGps = holes.filter((p) => p.lat != null && p.lon != null);
      const first = withGps[0];
      const last = withGps[withGps.length - 1];
      const start = await resolvePlaceLabel(
        sess.start_label,
        first ? { lat: Number(first.lat), lon: Number(first.lon) } : null,
      );
      const end = await resolvePlaceLabel(
        sess.end_label,
        last ? { lat: Number(last.lat), lon: Number(last.lon) } : null,
      );
      if (start && end && start !== end) setRouteLabel(`${start} → ${end}`);
      else setRouteLabel(start || end || sess.route_short || "—");
      setShowDetail(true);
    } catch (e) {
      setDetailStatus({ kind: "error", message: e.message });
    } finally {
      setDetailBusy(null);
    }
  };

  const isImageSession = detail?.media_kind === "image"
    || isImageKey(detail?.original_filename || detail?.filename);

  return (
    <MotionCard className="card det-card det-dash-wrap" delay={0}>
      <div className="card-body">
        <div className="det-dash-actions">
          <button type="button" className="btn btn-primary" onClick={onRefresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <StatusLine status={status} />
        </div>

        <div className="form-group" style={{ maxWidth: 280 }}>
          <label className="label" htmlFor={`det-filter-${sourceKind}`}>{singular}</label>
          <select
            id={`det-filter-${sourceKind}`}
            className="select"
            value={filterUser}
            disabled={loading}
            onChange={(e) => setFilterUser(e.target.value)}
          >
            <option value="">
              {loading ? `Loading ${singular.toLowerCase()}s…` : `— Select ${singular.toLowerCase()} —`}
            </option>
            {!loading && accounts.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </div>

        {filterUser ? (
          <>
            <div className="table-wrap table-template det-table-wrap" style={{ marginTop: "1rem" }}>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Video / image</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((s) => (
                    <tr key={s.id}>
                      <td>{s.id}</td>
                      <td title={s.filename}>{s.filename}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={detailBusy === s.id}
                          onClick={() => openDetails(s)}
                        >
                          {detailBusy === s.id ? "Loading…" : "View details"}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!rows.length && (
                    <tr>
                      <td colSpan={3} className="muted" style={{ textAlign: "center", padding: "1rem" }}>
                        No processed media for this {singular.toLowerCase()}.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <TablePager
              page={safePage}
              pageCount={pageCount}
              total={rows.length}
              onChange={setTablePage}
            />
          </>
        ) : (
          <p className="muted" style={{ marginTop: "1rem" }}>
            Select a {singular.toLowerCase()} to list processed media.
          </p>
        )}
        <StatusLine status={detailStatus} />
      </div>

      <Modal
        open={showDetail}
        onClose={() => { setShowDetail(false); setShowImages(false); }}
        title={detail ? `Session #${detail.id}` : "Details"}
        className="modal-form modal-detail"
      >
        {detail && (
          <div className="det-detail-grid">
            <div className="det-detail-left">
              {detail.output_video_url ? (
                <video src={detail.output_video_url} controls className="det-detail-media" />
              ) : detail.output_image_url ? (
                <img src={detail.output_image_url} alt="Annotated" className="det-detail-media" />
              ) : (
                <p className="muted">Processed media not found in S3 (it may still be local-only).</p>
              )}
            </div>
            <div className="det-detail-right">
              <div className="det-detail-block">
                <span className="label">Route</span>
                <p className="det-detail-value">{routeLabel || detail.route_short || "—"}</p>
              </div>
              <div className="det-detail-block">
                <span className="label">User</span>
                <p className="det-detail-value">{detail.username || "—"}</p>
              </div>
              <div className="det-detail-block">
                <span className="label">Potholes</span>
                <p className="det-detail-value">{detail.total_potholes ?? potholes.length}</p>
              </div>
              <div className="det-detail-block">
                {isImageSession ? (
                  <>
                    <span className="label">Map link</span>
                    <MapLinksBlock links={mapLinks} potholes={potholes} />
                  </>
                ) : (
                  <button type="button" className="btn btn-primary" onClick={() => setShowImages(true)}>
                    View images
                  </button>
                )}
              </div>
              {(detail.output_file_url || detail.output_video_url || detail.output_image_url) && (
                <div className="det-detail-block">
                  <a
                    className="btn btn-sm btn-primary"
                    href={detail.output_file_url || detail.output_video_url || detail.output_image_url}
                    target="_blank"
                    rel="noreferrer"
                    download
                  >
                    Download annotated
                  </a>
                </div>
              )}
            </div>
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={() => { setShowDetail(false); setShowImages(false); }}>
            Close
          </button>
        </div>
      </Modal>

      <ImagesTableModal
        open={showImages}
        onClose={() => setShowImages(false)}
        potholes={potholes}
        mapLinks={mapLinks}
      />
    </MotionCard>
  );
}

export default function Detection() {
  const { user } = useAuth();
  const [tab, setTab] = useState("detection");

  const [catalog, setCatalog] = useState({ users: [] });
  const [catalogStatus, setCatalogStatus] = useState(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  /** Source the current catalog payload belongs to — ignore stale lists after a switch. */
  const [catalogSource, setCatalogSource] = useState(null);
  const [sourceKind, setSourceKind] = useState("videographer");
  const [selectedUser, setSelectedUser] = useState("");
  const [selectedFolder, setSelectedFolder] = useState("");
  const [s3Key, setS3Key] = useState("");
  const [mediaUrl, setMediaUrl] = useState(null);
  const [mediaJson, setMediaJson] = useState("");
  const [captureMode, setCaptureMode] = useState("walking");
  const [running, setRunning] = useState(false);
  const [runStatus, setRunStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [panelActive, setPanelActive] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [severityFilter, setSeverityFilter] = useState("");
  const [showLocalModal, setShowLocalModal] = useState(false);
  const [localFile, setLocalFile] = useState(null);
  const [localPreview, setLocalPreview] = useState(null);
  const [gpsFile, setGpsFile] = useState(null);
  const [localCaptureMode, setLocalCaptureMode] = useState("walking");
  const [localRunStatus, setLocalRunStatus] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const [vgSessions, setVgSessions] = useState([]);
  const [vgStatus, setVgStatus] = useState(null);
  const [vgLoading, setVgLoading] = useState(false);

  const [userSessions, setUserSessions] = useState([]);
  const [userStatus, setUserStatus] = useState(null);
  const [userLoading, setUserLoading] = useState(false);

  const sourceKindRef = useRef(sourceKind);
  sourceKindRef.current = sourceKind;

  const loadCatalog = useCallback(async () => {
    const kind = sourceKind;
    setCatalogLoading(true);
    setCatalog({ users: [] });
    setCatalogSource(null);
    setCatalogStatus({
      kind: "ok",
      message: `Loading ${kind === "user" ? "users" : "videographers"}…`,
    });
    try {
      const res = await api.detectionS3Catalog(kind);
      // Drop stale response if the user switched source mid-flight.
      if (kind !== sourceKindRef.current) return;
      setCatalog(res);
      setCatalogSource(kind);
      setCatalogStatus(res.status);
    } catch (e) {
      if (kind !== sourceKindRef.current) return;
      setCatalog({ users: [] });
      setCatalogSource(null);
      setCatalogStatus({ kind: "error", message: e.message });
    } finally {
      if (kind === sourceKindRef.current) setCatalogLoading(false);
    }
  }, [sourceKind]);

  const catalogReady = !catalogLoading && catalogSource === sourceKind;
  const catalogUsers = useMemo(
    () => (
      catalogReady
        ? (catalog.users || []).filter((u) => String(u.username || "").toLowerCase() !== "legacy")
        : []
    ),
    [catalog.users, catalogReady],
  );

  const userRow = useMemo(
    () => catalogUsers.find((u) => u.username === selectedUser),
    [catalogUsers, selectedUser],
  );
  const folders = userRow?.folders || [];

  useEffect(() => {
    setSelectedFolder("");
    setS3Key("");
    setMediaUrl(null);
    setMediaJson("");
  }, [selectedUser, sourceKind]);

  useEffect(() => {
    setSelectedUser("");
    setSelectedFolder("");
    setS3Key("");
    setMediaUrl(null);
    setMediaJson("");
    setResult(null);
    setPanelActive(false);
  }, [sourceKind]);

  useEffect(() => {
    if (!s3Key) {
      setMediaUrl(null);
      setMediaJson("");
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.detectionS3Preview(s3Key);
        if (cancelled) return;
        setMediaUrl(res.video_url || null);
        setMediaJson(res.json_text || "");
      } catch {
        if (!cancelled) {
          setMediaUrl(null);
          setMediaJson("");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [s3Key]);

  const onLocalFile = (e) => {
    const f = e.target.files?.[0];
    setLocalFile(f || null);
    if (localPreview) URL.revokeObjectURL(localPreview);
    setLocalPreview(f ? URL.createObjectURL(f) : null);
    setLocalRunStatus(null);
  };

  const resetLocalForm = () => {
    if (localPreview) URL.revokeObjectURL(localPreview);
    setLocalFile(null);
    setLocalPreview(null);
    setGpsFile(null);
    setLocalCaptureMode("walking");
    setLocalRunStatus(null);
  };

  const loadVgDashboard = useCallback(async () => {
    setVgLoading(true);
    try {
      const res = await api.detectionSessions("videographer");
      setVgSessions(res.sessions || []);
      setVgStatus(res.status);
    } catch (e) {
      setVgStatus({ kind: "error", message: e.message });
    } finally {
      setVgLoading(false);
    }
  }, []);

  const loadUserDashboard = useCallback(async () => {
    setUserLoading(true);
    try {
      const res = await api.detectionSessions("user");
      setUserSessions(res.sessions || []);
      setUserStatus(res.status);
    } catch (e) {
      setUserStatus({ kind: "error", message: e.message });
    } finally {
      setUserLoading(false);
    }
  }, []);

  useEffect(() => { loadCatalog(); }, [loadCatalog]);
  useEffect(() => {
    if (tab === "dashboard") loadVgDashboard();
  }, [tab, loadVgDashboard]);
  useEffect(() => {
    if (tab === "citizen") loadUserDashboard();
  }, [tab, loadUserDashboard]);

  const runDetection = async ({ source = "s3" } = {}) => {
    if (source === "s3") {
      if (!s3Key) {
        setRunStatus({ kind: "error", message: "Select a media file first." });
        return;
      }
    } else if (!localFile) {
      setLocalRunStatus({ kind: "error", message: "Choose a local image or video first." });
      return;
    }

    if (source === "local") setShowLocalModal(false);

    setRunning(true);
    setRunStatus(null);
    setLocalRunStatus(null);
    setPanelActive(true);
    setResult(null);
    try {
      const fd = new FormData();
      if (source === "s3") {
        fd.append("s3_key", s3Key);
        fd.append("capture_mode", captureMode);
      } else {
        fd.append("media", localFile);
        if (gpsFile) fd.append("gps_log", gpsFile);
        fd.append("capture_mode", localCaptureMode);
      }
      const res = await api.detectionRun(fd);
      setRunStatus(res.status);
      if (res.status?.kind === "ok") {
        setResult(res);
        if (source === "s3") loadCatalog();
        else resetLocalForm();
      }
    } catch (e) {
      setRunStatus({ kind: "error", message: e.message });
    } finally {
      setRunning(false);
    }
  };

  const deleteSelected = async () => {
    if (!s3Key) return;
    setDeleting(true);
    try {
      const res = await api.detectionS3Delete(s3Key, sourceKind);
      setRunStatus(res.status);
      setS3Key("");
      setSelectedFolder("");
      setMediaUrl(null);
      setMediaJson("");
      setResult(null);
      setPanelActive(false);
      setShowDeleteConfirm(false);
      await loadCatalog();
    } catch (e) {
      setRunStatus({ kind: "error", message: e.message });
    } finally {
      setDeleting(false);
    }
  };

  const reportDownloadUrl = result?.session_id
    ? `/api/reports/${result.session_id}/download`
    : null;

  const outputActions = result ? (
    <div className="det-output-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "0.75rem" }}>
      <button type="button" className="btn btn-sm" onClick={() => setShowImages(true)}>Show images</button>
      <button type="button" className="btn btn-sm" onClick={() => setShowResults(true)}>View results</button>
      {result.output_file_url && (
        <a
          className="btn btn-sm btn-primary"
          href={result.output_file_url}
          target="_blank"
          rel="noreferrer"
          download
        >
          Download annotated
        </a>
      )}
      {reportDownloadUrl && (
        <a className="btn btn-sm" href={reportDownloadUrl} download>Download report</a>
      )}
    </div>
  ) : null;

  if (!user?.is_dev_admin) return <Navigate to="/" replace />;

  return (
    <PageShell title="SmartRoad Detection" subtitle="YOLOv12 inference · S3 field videos">
      <div className="det-page">
        <nav className="det-tabs" role="tablist" aria-label="Detection area">
          {[
            ["detection", "Detection"],
            ["dashboard", "Dashboard"],
            ["citizen", "Citizen dashboard"],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              className={`det-tab${tab === id ? " active" : ""}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>

        {tab === "detection" && (
          <div className="det-detect-row">
            <MotionCard className="card det-card" delay={0}>
              <CardHeader title="Source" actions={<button type="button" className="btn btn-sm" onClick={loadCatalog}>↻</button>} />
              <div className="card-body">
                <StatusLine status={catalogStatus} />

                <div className="form-group">
                  <label className="label" htmlFor="det-source-kind">Source</label>
                  <select
                    id="det-source-kind"
                    className="select"
                    value={sourceKind}
                    onChange={(e) => setSourceKind(e.target.value)}
                  >
                    <option value="videographer">Videographer</option>
                    <option value="user">User</option>
                  </select>
                </div>

                <div className="form-group">
                  <label className="label" htmlFor="det-user">
                    {sourceKind === "videographer" ? "Videographer" : "User"}
                  </label>
                  <select
                    id="det-user"
                    className="select"
                    value={selectedUser}
                    disabled={!catalogReady}
                    onChange={(e) => {
                      setSelectedUser(e.target.value);
                      setResult(null);
                      setPanelActive(false);
                    }}
                  >
                    <option value="">
                      {catalogLoading
                        ? `Loading ${sourceKind === "videographer" ? "videographers" : "users"}…`
                        : `— Select ${sourceKind === "videographer" ? "videographer" : "user"} —`}
                    </option>
                    {catalogUsers.map((u) => (
                      <option key={u.username} value={u.username}>{u.username}</option>
                    ))}
                  </select>
                </div>

                <AnimatePresence>
                  {selectedUser && catalogReady ? (
                    <motion.div
                      key={`media-${selectedUser}`}
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.22 }}
                    >
                      <div className="form-group">
                        <label className="label" htmlFor="det-video">Media</label>
                        <select
                          id="det-video"
                          className="select"
                          value={selectedFolder}
                          disabled={!catalogReady}
                          onChange={(e) => {
                            const folder = e.target.value;
                            setSelectedFolder(folder);
                            const row = folders.find((f) => f.folder === folder);
                            setS3Key(row?.keys?.[0] || "");
                            setResult(null);
                            setPanelActive(false);
                          }}
                        >
                          <option value="">— Select media —</option>
                          {!folders.length && <option value="" disabled>No media for this {sourceKind}</option>}
                          {folders.map((f) => (
                            <option key={f.folder} value={f.folder}>{f.label || f.folder}</option>
                          ))}
                        </select>
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>

                <fieldset className="form-group det-fieldset">
                  <legend className="label">Capture mode</legend>
                  <div className="det-radio-row">
                    <label className="det-radio">
                      <input type="radio" name="s3-capture" value="walking" checked={captureMode === "walking"} onChange={() => setCaptureMode("walking")} />
                      walking
                    </label>
                    <label className="det-radio">
                      <input type="radio" name="s3-capture" value="vehicle" checked={captureMode === "vehicle"} onChange={() => setCaptureMode("vehicle")} />
                      vehicle
                    </label>
                  </div>
                </fieldset>

                <div className="det-action-row">
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => runDetection({ source: "s3" })}
                    disabled={running || !s3Key}
                  >
                    {running && s3Key ? "Running detection…" : "Run detection"}
                  </button>
                  <button
                    type="button"
                    className="btn-delete-x"
                    onClick={() => setShowDeleteConfirm(true)}
                    disabled={running || deleting || !s3Key}
                    aria-label="Delete from S3"
                    title="Delete from S3"
                  >
                    ×
                  </button>
                </div>
                <StatusLine status={runStatus} />

                {s3Key && (
                  <div className="form-group" style={{ marginTop: "1rem" }}>
                    <span className="label">Selected media</span>
                    <p className="muted" style={{ fontSize: "0.78rem", wordBreak: "break-all" }}>{s3Key}</p>
                    <div className="det-preview-panel det-preview-panel--media">
                      <MediaPreview
                        url={mediaUrl}
                        name={s3Key}
                        empty="Could not load preview."
                      />
                    </div>
                    {mediaJson ? (
                      <div className="form-group" style={{ marginTop: "0.75rem" }}>
                        <span className="label">Sibling JSON</span>
                        <div className="det-json-snippet" tabIndex={0}>
                          <pre className="det-code">{mediaJson}</pre>
                        </div>
                      </div>
                    ) : (
                      <p className="muted" style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>
                        No sibling JSON found for this media.
                      </p>
                    )}
                  </div>
                )}

                <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border, #e5e7eb)" }}>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => { setShowLocalModal(true); setLocalRunStatus(null); }}
                    disabled={running}
                  >
                    Upload from locally
                  </button>
                </div>
              </div>
            </MotionCard>

            <MotionCard className="card det-card det-card-relative" delay={0.05}>
              <CardHeader title="Output" />
              <div className="card-body">
                {!panelActive && (
                  <p className="muted">Run detection to load annotated output here.</p>
                )}
                {running && <Loader label="Running YOLO detection" />}
                {!running && panelActive && !result?.output_video_url && !result?.output_image_url && (
                  <p className="muted">Processing…</p>
                )}
                {result?.output_video_url && (
                  <video src={result.output_video_url} controls className="det-media" />
                )}
                {result?.output_image_url && (
                  <img src={result.output_image_url} alt="Annotated" className="det-media" />
                )}
                {outputActions}
              </div>
            </MotionCard>
          </div>
        )}

        {tab === "dashboard" && (
          <SessionDashboard
            sourceKind="videographer"
            singular="Videographer"
            sessions={vgSessions}
            status={vgStatus}
            loading={vgLoading}
            onRefresh={loadVgDashboard}
          />
        )}

        {tab === "citizen" && (
          <SessionDashboard
            sourceKind="user"
            singular="User"
            sessions={userSessions}
            status={userStatus}
            loading={userLoading}
            onRefresh={loadUserDashboard}
          />
        )}

        <Modal
          open={showDeleteConfirm}
          onClose={() => !deleting && setShowDeleteConfirm(false)}
          title="Delete from S3?"
          className="modal-form"
        >
          <p className="modal-body">
            Remove this media (and sibling GPS/JSON if present) from S3?
            {s3Key ? (
              <>
                <br />
                <strong style={{ wordBreak: "break-all" }}>{s3Key}</strong>
              </>
            ) : null}
          </p>
          <div className="modal-actions">
            <button
              type="button"
              className="btn btn-danger"
              disabled={deleting || !s3Key}
              onClick={deleteSelected}
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={deleting}
              onClick={() => setShowDeleteConfirm(false)}
            >
              Cancel
            </button>
          </div>
        </Modal>

        <Modal open={showLocalModal} onClose={() => setShowLocalModal(false)} title="Local upload" className="modal-form">
          <div className="modal-form-scroll">
            <div className="form-group">
              <label className="label" htmlFor="local-media">Image or video</label>
              <input id="local-media" type="file" accept="image/*,video/*" onChange={onLocalFile} />
            </div>
            {localPreview && (
              <div className="det-preview-panel det-preview-panel--media" style={{ marginBottom: "0.75rem" }}>
                <MediaPreview
                  url={localPreview}
                  name={localFile?.name}
                  mime={localFile?.type}
                />
              </div>
            )}
            <div className="form-group">
              <label className="label" htmlFor="local-gps">GPS log (optional for images)</label>
              <input id="local-gps" type="file" accept=".csv,.xlsx,.xls,.json" onChange={(e) => setGpsFile(e.target.files?.[0] || null)} />
            </div>
            <fieldset className="form-group det-fieldset">
              <legend className="label">Capture mode</legend>
              <div className="det-radio-row">
                <label className="det-radio">
                  <input type="radio" name="local-capture" value="walking" checked={localCaptureMode === "walking"} onChange={() => setLocalCaptureMode("walking")} />
                  walking
                </label>
                <label className="det-radio">
                  <input type="radio" name="local-capture" value="vehicle" checked={localCaptureMode === "vehicle"} onChange={() => setLocalCaptureMode("vehicle")} />
                  vehicle
                </label>
              </div>
            </fieldset>
            <StatusLine status={localRunStatus} />
          </div>
          <div className="modal-actions">
            <button type="button" className="btn btn-primary" disabled={running || !localFile} onClick={() => runDetection({ source: "local" })}>
              Run detection
            </button>
            <button type="button" className="btn" onClick={() => setShowLocalModal(false)}>Cancel</button>
          </div>
        </Modal>

        <ImagesTableModal
          open={showImages}
          onClose={() => setShowImages(false)}
          title="Detection images"
          potholes={(result?.detections || []).map((d, i) => ({
            ...d,
            s3_url: d.s3_url || (result?.gallery || [])[i] || null,
          }))}
          gallery={result?.gallery || []}
        />

        <Modal open={showResults} onClose={() => setShowResults(false)} title="Results" className="modal-form modal-wide">
          <div className="form-group">
            <label className="label" htmlFor="sev">Severity filter</label>
            <select id="sev" className="select" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option value="">All</option>
              <option value="High">High</option>
              <option value="Medium">Medium</option>
              <option value="Low">Low</option>
            </select>
          </div>
          <ResultsTable rows={result?.detections || []} severityFilter={severityFilter} />
          <div className="modal-actions">
            <button type="button" className="btn" onClick={() => setShowResults(false)}>Close</button>
          </div>
        </Modal>
      </div>
    </PageShell>
  );
}
