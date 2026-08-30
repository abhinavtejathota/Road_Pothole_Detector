import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";

export default function ReviewQueue() {
  const { data, loading, error, reload } = useAsync(() => api.reviewQueue(), []);
  const [forms, setForms] = useState({});
  const [busy, setBusy] = useState(null);
  const items = Array.isArray(data) ? data : [];

  const setField = (valId, key, value) => {
    setForms((prev) => ({
      ...prev,
      [valId]: { final_result: "PASS", comment: "", ...prev[valId], [key]: value },
    }));
  };

  const submit = async (e, valId, woId) => {
    e.preventDefault();
    const f = forms[valId] || { final_result: "PASS", comment: "" };
    if (!f.comment?.trim()) return;
    setBusy(valId);
    try {
      await api.reviewValidation(valId, { ...f, wo_id: woId });
      reload();
    } finally {
      setBusy(null);
    }
  };

  return (
    <PageShell
      title="Supervisor review queue"
      titleIcon={<i className="bi bi-camera-video page-title-icon page-title-icon-warn" aria-hidden />}
      loading={loading}
      error={error}
      skeleton="grid"
      actions={items.length > 0 && <span className="badge badge-warn" style={{ fontSize: "0.85rem" }}>{items.length} pending</span>}
    >
      {items.length === 0 ? (
        <EmptyState message="No pending reviews" hint="All validations are resolved." />
      ) : (
        items.map((item, i) => (
          <MotionCard key={item.id} className="card review-card" delay={i * 0.06} style={{ marginBottom: "1rem" }}>
            <CardHeader
              title={<>WO #{item.work_order_id} — {item.filename || "Session"}</>}
              badge={<span className="badge badge-warn">PARTIAL</span>}
            />
            <div className="card-body">
              <div className="validation-steps" style={{ marginBottom: "1rem" }}>
                <div>
                  <div className="kpi-value" style={{ fontSize: "1.35rem", color: "var(--warn)" }}>
                    {item.resolution_score?.toFixed?.(1) ?? item.resolution_score}%
                  </div>
                  <div className="kpi-label">Resolution score</div>
                </div>
                <div>
                  <StatusBadge status={`GPS ${item.gps_check}`} />
                  {item.gps_distance_meters != null && (
                    <div className="muted" style={{ fontSize: "0.72rem", marginTop: "0.25rem" }}>
                      {Number(item.gps_distance_meters).toFixed(1)}m
                    </div>
                  )}
                </div>
                <div><StatusBadge status={`TS ${item.timestamp_check}`} /></div>
                <div><StatusBadge status={`YOLO ${item.yolo_severity || "Partial"}`} /></div>
              </div>
              <div style={{ fontSize: "0.82rem", marginBottom: "1rem" }}>
                <div><strong>Vendor:</strong> {item.vendor_name || "—"}</div>
                <div><strong>Submitted:</strong> {item.validated_at?.slice?.(0, 16) || item.validated_at || "—"}</div>
                {item.after_photo_s3_url && (
                  <a href={item.after_photo_s3_url} target="_blank" rel="noreferrer" className="btn btn-sm" style={{ marginTop: "0.5rem" }}>
                    View photo
                  </a>
                )}
                <Link to={`/tasks/${item.work_order_id}`} className="btn btn-sm" style={{ marginTop: "0.5rem", marginLeft: "0.35rem" }}>
                  Open work order
                </Link>
              </div>
              <form onSubmit={(e) => submit(e, item.id, item.work_order_id)} className="filter-row">
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="label">Your decision <span className="text-danger">*</span></label>
                  <select
                    className="select"
                    required
                    value={(forms[item.id]?.final_result) || "PASS"}
                    onChange={(e) => setField(item.id, "final_result", e.target.value)}
                  >
                    <option value="PASS">PASS — repair accepted</option>
                    <option value="FAIL">FAIL — repair rejected</option>
                  </select>
                </div>
                <div className="form-group" style={{ margin: 0, flex: 2 }}>
                  <label className="label">Review comment <span className="text-danger">*</span></label>
                  <input
                    className="input"
                    required
                    placeholder="Reason for your decision…"
                    value={(forms[item.id]?.comment) || ""}
                    onChange={(e) => setField(item.id, "comment", e.target.value)}
                  />
                </div>
                <button type="submit" className="btn btn-primary btn-sm" disabled={busy === item.id}>
                  {busy === item.id ? "Submitting…" : "Submit decision"}
                </button>
              </form>
            </div>
          </MotionCard>
        ))
      )}
    </PageShell>
  );
}
