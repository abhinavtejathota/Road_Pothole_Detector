import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import Modal from "../components/Modal";
import RoadSurveyMap from "../components/RoadSurveyMap";
import RoadNetworkLegend from "../components/RoadNetworkLegend";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";
import EmptyState from "../components/EmptyState";
import { todayIST, STATE_META } from "../data/surveyConstants";

export default function SurveyAdmin() {
  const { user } = useAuth();
  const [dailyKm, setDailyKm] = useState(100);
  const [focusClasses, setFocusClasses] = useState(["nh", "sh", "mdr", "other"]);
  const [focusAll, setFocusAll] = useState(true);
  const [msg, setMsg] = useState("");
  const [msgKind, setMsgKind] = useState(null);
  const [previewUserId, setPreviewUserId] = useState(null);
  const [clearRow, setClearRow] = useState(null);
  const [clearMsg, setClearMsg] = useState("");
  const [clearErr, setClearErr] = useState("");
  const [clearing, setClearing] = useState(false);
  const [reopenStateKey, setReopenStateKey] = useState("telangana");
  const [reopenDistrictId, setReopenDistrictId] = useState("");
  const [reopening, setReopening] = useState(false);
  const [reopenMsg, setReopenMsg] = useState("");
  const [reopenErr, setReopenErr] = useState("");
  const [viewDate, setViewDate] = useState(todayIST());
  const [historyOpenDate, setHistoryOpenDate] = useState(null);

  const today = todayIST();

  const { data: overview, loading, error, reload } = useAsync(
    () => api.surveyAssignments(undefined, viewDate),
    [viewDate],
  );

  const { data: reopenDistricts } = useAsync(
    () => (reopenStateKey ? api.surveyDistricts(reopenStateKey) : Promise.resolve(null)),
    [reopenStateKey],
  );

  useEffect(() => {
    const t = setInterval(() => reload(), 8000);
    return () => clearInterval(t);
  }, [reload]);

  useAsync(() => api.surveySettings().then((s) => {
    setDailyKm(s.daily_km ?? 100);
    const all = s.focus_is_all !== false && (!s.focus_road_class || s.focus_road_class === "all");
    setFocusAll(all);
    setFocusClasses(
      all
        ? ["nh", "sh", "mdr", "other"]
        : (s.focus_road_classes?.length ? s.focus_road_classes : String(s.focus_road_class || "nh").split(","))
    );
    return s;
  }), []);

  const previewAssignDate = useMemo(() => {
    const row = (overview?.items || []).find(
      (r) => Number(r.user_id || r.videographer_user_id) === Number(previewUserId),
    );
    return row?.assignment_date || row?.date || viewDate;
  }, [overview, previewUserId, viewDate]);

  const previewRow = useMemo(
    () => (overview?.items || []).find((r) => Number(r.user_id || r.videographer_user_id) === Number(previewUserId)),
    [overview, previewUserId],
  );

  const { data: segments, loading: loadingSeg, error: segError } = useAsync(
    () => (
      previewRow?.state_key && previewRow?.district_id
        ? api.surveySegments(previewRow.state_key, previewRow.district_id)
        : Promise.resolve(null)
    ),
    [previewRow?.state_key, previewRow?.district_id],
  );

  const { data: previewAssign } = useAsync(
    () => (previewUserId ? api.surveyAssignments(previewUserId, previewAssignDate) : Promise.resolve(null)),
    [previewUserId, previewAssignDate],
  );

  const assignedIds = useMemo(
    () => new Set((previewAssign?.assignments || []).map((a) => a.id)),
    [previewAssign],
  );

  const mapFeatures = useMemo(() => {
    if (!previewUserId || !segments?.features) return [];
    const all = segments.features;
    if (!assignedIds.size) return all;
    return all.map((f) => {
      const id = f.properties?.id || f.properties?.segment_id;
      if (!assignedIds.has(id)) {
        return { ...f, properties: { ...f.properties, status: "available" } };
      }
      return { ...f, properties: { ...f.properties, status: "assigned" } };
    });
  }, [previewUserId, segments, assignedIds]);

  const saveKm = useCallback(async () => {
    setMsg("");
    try {
      const focus_road_class = focusAll || focusClasses.length === 4
        ? "all"
        : focusClasses.join(",");
      if (!focusAll && focusClasses.length === 0) {
        setMsg("Select at least one road class, or choose All.");
        setMsgKind("error");
        return;
      }
      await api.updateSurveySettings({
        daily_km: Number(dailyKm),
        focus_road_class,
      });
      setMsg("Saved daily km target and road focus for videographer generate.");
      setMsgKind("success");
      reload();
    } catch (e) {
      setMsg(e.message);
      setMsgKind("error");
    }
  }, [dailyKm, focusAll, focusClasses, reload]);

  const toggleFocusClass = (key) => {
    setFocusAll(false);
    setFocusClasses((prev) => {
      const has = prev.includes(key);
      if (has) return prev.filter((k) => k !== key);
      return [...prev, key];
    });
  };

  const openClearModal = (row) => {
    setClearRow(row);
    setClearMsg("");
    setClearErr("");
  };

  const closeClearModal = () => {
    if (!clearing) setClearRow(null);
  };

  const confirmClear = async (e) => {
    e.preventDefault();
    if (!clearRow) return;
    const uid = clearRow.user_id || clearRow.videographer_user_id;
    const clearDate = clearRow.assignment_date || clearRow.date || viewDate;
    setClearing(true);
    setClearErr("");
    setClearMsg("");
    try {
      const res = await api.clearSurveyAssignment(uid, clearDate);
      setClearMsg(res.message || "Assignment cleared.");
      reload();
      if (Number(previewUserId) === Number(uid)) setPreviewUserId(null);
      if (res.cleared) setTimeout(() => setClearRow(null), 1200);
    } catch (ex) {
      setClearErr(ex.message);
    } finally {
      setClearing(false);
    }
  };

  const confirmReopen = async () => {
    if (!reopenStateKey || !reopenDistrictId) {
      setReopenErr("Pick a state and district.");
      return;
    }
    setReopening(true);
    setReopenErr("");
    setReopenMsg("");
    try {
      const res = await api.reopenDistrictRoads(reopenDistrictId, reopenStateKey);
      setReopenMsg(
        res.message
        || `Reopened ${res.segments_reopened ?? 0} covered road(s) — they can be assigned again.`,
      );
      reload();
    } catch (ex) {
      setReopenErr(ex.message);
    } finally {
      setReopening(false);
    }
  };

  if (!user?.is_admin && !user?.is_dev_admin) return <Navigate to="/" replace />;

  const items = overview?.items || [];
  const carryoverItems = overview?.carryover_items || [];
  const history = overview?.history || [];
  const targetKm = overview?.target_km ?? dailyKm;
  const previewState = previewRow?.state_key ? STATE_META[previewRow.state_key] : null;
  const clearDateLabel = clearRow?.assignment_date || clearRow?.date || viewDate;

  const routeLabel = (row) => {
    const s = row?.start?.label || row?.start?.display_name;
    const e = row?.end?.label || row?.end?.display_name;
    if (s && e) return `${s} → ${e}`;
    if (s || e) return s || e;
    return "—";
  };

  return (
    <PageShell
      title="Survey admin"
      subtitle={`${overview?.date || viewDate} · ${items.length} assignment(s)${carryoverItems.length ? ` · ${carryoverItems.length} carryover` : ""}`}
      loading={loading && !overview}
      error={error}
      actions={<button type="button" className="btn btn-sm" onClick={reload}>Refresh</button>}
    >
      <MotionCard className="card survey-toolbar" delay={0}>
        <CardHeader title="Daily target & road focus" />
        <div className="card-body">
          <div className="form-group" style={{ marginBottom: "0.85rem", maxWidth: 280 }}>
            <label className="label" htmlFor="survey-admin-date">View assignments for date</label>
            <input
              id="survey-admin-date"
              type="date"
              className="input"
              value={viewDate}
              max={today}
              onChange={(e) => {
                setViewDate(e.target.value || today);
                setPreviewUserId(null);
              }}
            />
          </div>
          <p className="muted" style={{ marginBottom: "0.75rem", fontSize: "0.85rem" }}>
            Videographers pick a start → end corridor on <strong>Survey</strong>. Each has their own quota.
            Focus limits which road classes are picked along the corridor.
          </p>
          <div className="survey-admin-controls">
            <div className="form-group">
              <label className="label" htmlFor="daily-km">Km per videographer / day</label>
              <input
                id="daily-km"
                className="input"
                type="number"
                min={10}
                max={500}
                value={dailyKm}
                onChange={(e) => setDailyKm(e.target.value)}
              />
            </div>
            <div className="form-group" style={{ minWidth: 280 }}>
              <span className="label">Road focus (generate)</span>
              <div className="survey-focus-options">
                <label className="survey-focus-option">
                  <input
                    type="checkbox"
                    checked={focusAll}
                    onChange={(e) => {
                      setFocusAll(e.target.checked);
                      if (e.target.checked) setFocusClasses(["nh", "sh", "mdr", "other"]);
                    }}
                  />
                  All (NH → SH → MDR → Local)
                </label>
                {[
                  ["nh", "National highways"],
                  ["sh", "State highways"],
                  ["mdr", "Major district roads"],
                  ["other", "Local roads"],
                ].map(([key, label]) => (
                  <label key={key} className="survey-focus-option">
                    <input
                      type="checkbox"
                      checked={!focusAll && focusClasses.includes(key)}
                      disabled={focusAll}
                      onChange={() => toggleFocusClass(key)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <p className="muted" style={{ fontSize: "0.75rem", margin: "0.35rem 0 0" }}>
                Uncheck All, then combine classes (e.g. NH + SH only).
              </p>
            </div>
            <button type="button" className="btn btn-primary btn-sm" onClick={saveKm}>Save</button>
          </div>
          {msg && msgKind === "success" && <div className="alert alert-ok" style={{ marginTop: "0.75rem", marginBottom: 0 }}>{msg}</div>}
          {msg && msgKind === "error" && <div className="alert alert-error" style={{ marginTop: "0.75rem", marginBottom: 0 }}>{msg}</div>}
        </div>
      </MotionCard>

      <MotionCard className="card" delay={0.03}>
        <CardHeader title="Reopen covered roads (district)" />
        <div className="card-body">
          <p className="muted" style={{ marginBottom: "0.75rem", fontSize: "0.85rem" }}>
            After a videographer uploads Capture, those roads stay <strong>covered</strong> and cannot be
            assigned again (and GPS over them does not count to quota). Use this to reset a district
            so its roads can be surveyed again.
          </p>
          <div className="survey-admin-controls">
            <div className="form-group">
              <label className="label" htmlFor="reopen-state">State</label>
              <select
                id="reopen-state"
                className="select"
                value={reopenStateKey}
                onChange={(e) => {
                  setReopenStateKey(e.target.value);
                  setReopenDistrictId("");
                }}
              >
                {Object.values(STATE_META).map((s) => (
                  <option key={s.key} value={s.key}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group" style={{ minWidth: 260 }}>
              <label className="label" htmlFor="reopen-district">District</label>
              <select
                id="reopen-district"
                className="select"
                value={reopenDistrictId}
                onChange={(e) => setReopenDistrictId(e.target.value)}
              >
                <option value="">— Choose —</option>
                {(Array.isArray(reopenDistricts) ? reopenDistricts : (reopenDistricts?.districts || [])).map((d) => {
                  const id = String(d.id || d.district_id || "");
                  const name = d.name || d.district_name || id;
                  return (
                    <option key={id} value={id}>{name}</option>
                  );
                })}
              </select>
            </div>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={confirmReopen}
              disabled={reopening || !reopenDistrictId}
            >
              {reopening ? "Reopening…" : "Reopen covered roads"}
            </button>
          </div>
          {reopenMsg && <div className="alert alert-ok" style={{ marginTop: "0.75rem", marginBottom: 0 }}>{reopenMsg}</div>}
          {reopenErr && <div className="alert alert-error" style={{ marginTop: "0.75rem", marginBottom: 0 }}>{reopenErr}</div>}
        </div>
      </MotionCard>

      <MotionCard className="card" delay={0.05}>
        <CardHeader
          title={`Field assignments — ${viewDate}`}
          badge={<span className="badge badge-created">{items.length}</span>}
        />
        {viewDate === today && carryoverItems.length > 0 && (
          <div className="card-body" style={{ paddingBottom: 0 }}>
            <div className="alert alert-warn" style={{ marginBottom: "0.75rem" }}>
              {carryoverItems.length} incomplete prior-day assignment(s) still open — shown below and
              blocking new routes until finished (or cleared).
            </div>
          </div>
        )}
        {items.length === 0 ? (
          <div className="card-body">
            <EmptyState
              message={`No videographer assignments on ${viewDate}`}
              hint="Pick another date, or wait for videographers to generate a corridor on Survey."
            />
          </div>
        ) : (
          <div className="table-wrap table-template">
            <table>
              <thead>
                <tr>
                  <th>Videographer</th>
                  <th>Assign date</th>
                  <th>Route</th>
                  <th>District</th>
                  <th>Segments</th>
                  <th>Assigned km</th>
                  <th>Covered</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const uid = row.user_id || row.videographer_user_id;
                  const assignDate = row.assignment_date || row.date || viewDate;
                  const rowKey = `${assignDate}-${uid}-${row.is_carryover ? "c" : "t"}`;
                  return (
                    <tr
                      key={rowKey}
                      className={`${row.over_target ? "sla-breach" : ""}${Number(previewUserId) === Number(uid) ? " survey-row-selected" : ""}`}
                    >
                      <td>{row.videographer_name || row.videographer_username || `User ${uid}`}</td>
                      <td>
                        {assignDate}
                        {row.is_carryover ? (
                          <span className="badge badge-failed" style={{ marginLeft: 6 }}>Prior day</span>
                        ) : null}
                      </td>
                      <td style={{ maxWidth: 220, fontSize: "0.82rem" }}>{routeLabel(row)}</td>
                      <td>{row.district_name || row.district_id || "—"}</td>
                      <td>{row.segment_count ?? 0}</td>
                      <td>{row.total_km ?? 0}</td>
                      <td>{row.covered_km ?? 0}</td>
                      <td>{row.target_km ?? targetKm} km</td>
                      <td>
                        {row.assignment_complete
                          ? <span className="badge badge-partial">Complete</span>
                          : row.quota_incomplete || row.is_carryover
                            ? <span className="badge badge-failed">Incomplete</span>
                            : <span className="badge badge-partial">OK</span>}
                      </td>
                      <td>
                        <div className="table-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => setPreviewUserId(Number(previewUserId) === Number(uid) ? null : uid)}
                          >
                            {Number(previewUserId) === Number(uid) ? "Hide map" : "Map"}
                          </button>
                          <button type="button" className="btn btn-sm btn-danger" onClick={() => openClearModal(row)}>
                            Clear
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </MotionCard>

      {viewDate === today && history.length > 0 && (
        <MotionCard className="card" delay={0.07}>
          <CardHeader title="Previous days" badge={<span className="badge badge-created">{history.length}</span>} />
          <div className="card-body">
            <p className="muted" style={{ marginBottom: "0.75rem", fontSize: "0.85rem" }}>
              Browse every day that has assignments. Open a day for full detail, or use the date picker above.
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
                      <td>{h.date}{h.date === today ? " (today)" : ""}</td>
                      <td>{h.user_count}</td>
                      <td>{h.segment_count}</td>
                      <td>
                        <div className="table-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => {
                              setViewDate(h.date);
                              setPreviewUserId(null);
                              setHistoryOpenDate(historyOpenDate === h.date ? null : h.date);
                            }}
                          >
                            View day
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => setHistoryOpenDate(historyOpenDate === h.date ? null : h.date)}
                          >
                            {historyOpenDate === h.date ? "Hide detail" : "Detail"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {historyOpenDate && (history.find((h) => h.date === historyOpenDate)?.items || []).length > 0 && (
              <div className="table-wrap table-template" style={{ marginTop: "0.85rem" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Videographer</th>
                      <th>Route</th>
                      <th>Covered</th>
                      <th>Assigned</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(history.find((h) => h.date === historyOpenDate)?.items || []).map((row) => {
                      const uid = row.user_id || row.videographer_user_id;
                      return (
                        <tr key={`${historyOpenDate}-${uid}`}>
                          <td>{row.videographer_name || row.videographer_username || `User ${uid}`}</td>
                          <td style={{ maxWidth: 240, fontSize: "0.82rem" }}>{routeLabel(row)}</td>
                          <td>{row.covered_km ?? 0} km</td>
                          <td>{row.total_km ?? 0} km</td>
                          <td>
                            {row.assignment_complete
                              ? <span className="badge badge-partial">Complete</span>
                              : <span className="badge badge-failed">Incomplete</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </MotionCard>
      )}

      {previewUserId && previewRow && (
        <MotionCard className="card" delay={0.1}>
          <CardHeader
            title={`${previewRow.district_name || "District"} — ${previewRow.videographer_name || "assignment"}${previewRow.assignment_date ? ` (${previewRow.assignment_date})` : ""}`}
          />
          {segError && <div className="alert alert-error">{segError}</div>}
          {loadingSeg ? (
            <div className="map-box empty" style={{ height: 480 }}>
              <Loader label="Loading map" />
            </div>
          ) : (
            <div className="card-body">
              <div className="survey-map-legend">
                <RoadNetworkLegend showAssignment showCompleted />
              </div>
              <RoadSurveyMap
                features={mapFeatures}
                center={previewState?.center}
                zoom={previewState?.zoom || 10}
                bounds={previewState?.bounds}
                fitToFeatures
                detailMode
                height={480}
                assignmentSummary={previewAssign?.summary}
                hideLegend
              />
            </div>
          )}
        </MotionCard>
      )}

      <Modal open={Boolean(clearRow)} onClose={closeClearModal} title={null}>
        <div className="modal-warning-header">
          <i className="bi bi-exclamation-triangle-fill modal-warning-icon" aria-hidden />
          <h2 className="modal-title" style={{ margin: 0 }}>Clear survey assignment?</h2>
        </div>
        <div className="modal-body">
          <p>
            This removes the <strong>{clearDateLabel}</strong> assignment for{" "}
            <strong>{clearRow?.videographer_name || `user ${clearRow?.user_id || clearRow?.videographer_user_id}`}</strong>
            {clearRow?.district_name ? ` in ${clearRow.district_name}` : ""}.
          </p>
          <p className="muted" style={{ fontSize: "0.82rem" }}>
            Other dates stay in Postgres (<code>survey_daily_assignments</code>) and the JSON mirror
            <code>data/gis/survey_state.json</code> — only {clearDateLabel} is cleared.
          </p>
          {clearMsg && <div className="alert alert-ok">{clearMsg}</div>}
          {clearErr && <div className="alert alert-error">{clearErr}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={closeClearModal} disabled={clearing}>Cancel</button>
          <button type="button" className="btn btn-danger" onClick={confirmClear} disabled={clearing}>
            {clearing ? "Clearing…" : `Clear ${clearDateLabel}`}
          </button>
        </div>
      </Modal>
    </PageShell>
  );
}
