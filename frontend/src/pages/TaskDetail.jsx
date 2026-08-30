import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import PotholeMap from "../components/PotholeMap";
import StatusBadge from "../components/StatusBadge";
import Loader from "../components/Loader";

function fmtDate(v) {
  if (!v) return "—";
  return String(v).slice(0, 16).replace("T", " ");
}

export default function TaskDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const { data, loading, error, reload } = useAsync(() => api.task(id), [id]);
  const [alloc, setAlloc] = useState({ vendor_id: "", estimated_budget: "", remarks: "" });
  const [statusForm, setStatusForm] = useState({ new_status: "", comment: "", actual_spend: "" });
  const [review, setReview] = useState({ final_result: "PASS", comment: "" });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const wo = data?.work_order;
  const suggestions = new Set((data?.suggestions || []).map((s) => s.id));

  const doAllocate = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.allocateTask(id, { ...alloc, vendor_id: parseInt(alloc.vendor_id, 10) });
      setMsg("Vendor allocated");
      reload();
    } finally {
      setBusy(false);
    }
  };

  const doStatus = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.updateTaskStatus(id, { ...statusForm, actual_spend: statusForm.actual_spend || undefined });
      setMsg("Status updated");
      reload();
    } finally {
      setBusy(false);
    }
  };

  const doReview = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.reviewValidation(data.validation.id, { ...review, wo_id: parseInt(id, 10) });
      setMsg("Review saved");
      reload();
    } finally {
      setBusy(false);
    }
  };

  const mapPts = (data?.potholes || []).map((p) => ({ lat: p.lat, lon: p.lon, severity: p.severity, status: p.pothole_status }));
  const validation = data?.validation;
  const warranty = data?.warranty;
  const canAllocate = user?.is_allocator && wo && ["Created", "Failed"].includes(wo.status);
  const showStatusForm = data?.next_statuses?.length > 0
    && (!user?.is_vendor_role || ["Allocated", "WIP"].includes(wo?.status));

  return (
    <PageShell loading={loading} error={error} skeleton="dashboard">
      {wo && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap", marginBottom: "1rem" }}>
            <Link to="/tasks" className="btn btn-sm">← Tasks</Link>
            <h1 className="page-title" style={{ margin: 0 }}>Work order #{wo.id}</h1>
            <StatusBadge status={wo.status} />
            {wo.sla_breached && <span className="badge badge-failed">SLA breached</span>}
            {wo.sla_tier && <StatusBadge status={wo.sla_tier} />}
            {busy && <Loader />}
          </div>

          {msg && <motion.div className="alert alert-ok" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>{msg}</motion.div>}

          <div className="detail-layout">
            <div className="detail-side">
              <MotionCard className="card" delay={0.05}>
                <CardHeader title="Video session" />
                <div className="card-body" style={{ fontSize: "0.85rem", lineHeight: 1.9 }}>
                  <div><strong>File:</strong> {wo.filename || "—"}</div>
                  <div><strong>Processed:</strong> {fmtDate(wo.processed_at)}</div>
                  <div><strong>Potholes:</strong> <span className="badge badge-created">{wo.total_potholes ?? "—"}</span></div>
                  <div><strong>Location:</strong> {wo.city || "—"}</div>
                </div>
              </MotionCard>

              <MotionCard className="card" delay={0.08}>
                <CardHeader title="Budget" />
                <div className="card-body">
                  <div className="grid grid-2" style={{ gap: "0.75rem", textAlign: "center", fontSize: "0.85rem" }}>
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "0.75rem" }}>
                      <div className="kpi-value" style={{ fontSize: "1.1rem", color: "var(--accent)" }}>
                        {wo.estimated_budget != null ? `₹${Math.round(wo.estimated_budget).toLocaleString()}` : "—"}
                      </div>
                      <div className="kpi-label">Estimated</div>
                    </div>
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "0.75rem" }}>
                      <div className="kpi-value" style={{ fontSize: "1.1rem", color: "var(--success)" }}>
                        {wo.actual_spend != null ? `₹${Math.round(wo.actual_spend).toLocaleString()}` : "—"}
                      </div>
                      <div className="kpi-label">Actual</div>
                    </div>
                  </div>
                </div>
              </MotionCard>

              <MotionCard className="card" delay={0.1}>
                <CardHeader title="Vendor" />
                <div className="card-body" style={{ fontSize: "0.85rem", lineHeight: 1.9 }}>
                  {wo.vendor_name ? (
                    <>
                      <div className="fw-semibold">{wo.vendor_name}</div>
                      <div>{wo.vendor_phone || "—"}</div>
                      <div>{wo.vendor_email || "—"}</div>
                    </>
                  ) : (
                    <p className="muted" style={{ margin: 0 }}>No vendor assigned yet.</p>
                  )}
                  {canAllocate && (
                    <form onSubmit={doAllocate} style={{ marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
                      <div className="form-group">
                        <label className="label">Assign vendor</label>
                        <select className="select" required value={alloc.vendor_id} onChange={(e) => setAlloc({ ...alloc, vendor_id: e.target.value })}>
                          <option value="">— Select vendor —</option>
                          {(data.vendors || []).map((v) => (
                            <option key={v.id} value={v.id}>
                              {suggestions.has(v.id) ? "⭐ " : ""}{v.company_name} (score: {Math.round(v.performance_score || 0)})
                            </option>
                          ))}
                        </select>
                      </div>
                      {suggestions.size > 0 && (
                        <p className="muted" style={{ fontSize: "0.75rem", marginBottom: "0.5rem" }}>⭐ = smart suggested vendors</p>
                      )}
                      <div className="form-group">
                        <input className="input" type="number" step="0.01" min="0" placeholder="Estimated budget (₹)" value={alloc.estimated_budget} onChange={(e) => setAlloc({ ...alloc, estimated_budget: e.target.value })} />
                      </div>
                      <div className="form-group">
                        <textarea className="input" rows={2} placeholder="Allocation remarks…" value={alloc.remarks} onChange={(e) => setAlloc({ ...alloc, remarks: e.target.value })} />
                      </div>
                      <button type="submit" className="btn btn-primary btn-sm" style={{ width: "100%" }}>Allocate</button>
                    </form>
                  )}
                </div>
              </MotionCard>

              {wo.sla_due_date && (
                <MotionCard className={`card${wo.sla_breached ? " review-card" : ""}`} delay={0.12}>
                  <CardHeader title="SLA timeline" className={wo.sla_breached ? "text-danger" : ""} />
                  <div className="card-body" style={{ fontSize: "0.85rem", lineHeight: 1.9 }}>
                    <div><strong>Allocated:</strong> {fmtDate(wo.allocated_at)}</div>
                    <div><strong>Due:</strong> <span className={wo.sla_breached ? "text-danger" : ""}>{fmtDate(wo.sla_due_date)}</span></div>
                    {wo.completed_at && <div><strong>Completed:</strong> {fmtDate(wo.completed_at)}</div>}
                    {wo.verified_at && <div><strong>Verified:</strong> {fmtDate(wo.verified_at)}</div>}
                  </div>
                </MotionCard>
              )}

              {warranty && (
                <MotionCard className="card" delay={0.14}>
                  <CardHeader title="Warranty" />
                  <div className="card-body" style={{ fontSize: "0.85rem", lineHeight: 1.9 }}>
                    <div><strong>Status:</strong> <StatusBadge status={warranty.warranty_status || warranty.status || "Valid"} /></div>
                    <div><strong>Expiry:</strong> {warranty.warranty_expiry_date?.slice?.(0, 10) || warranty.warranty_expiry_date || "—"}</div>
                    <div><strong>Next maintenance:</strong> {warranty.next_maintenance_date?.slice?.(0, 10) || warranty.next_maintenance_date || "—"}</div>
                    <div><strong>Claims:</strong> {warranty.claim_count ?? 0}</div>
                  </div>
                </MotionCard>
              )}
            </div>

            <div className="detail-main">
              {showStatusForm && (
                <MotionCard className="card" delay={0.1} style={{ borderColor: "rgba(15, 58, 95, 0.35)" }}>
                  <CardHeader title="Update status" />
                  <div className="card-body">
                    <form onSubmit={doStatus} className="filter-row">
                      <div className="form-group" style={{ margin: 0 }}>
                        <label className="label">Move to</label>
                        <select className="select" required value={statusForm.new_status} onChange={(e) => setStatusForm({ ...statusForm, new_status: e.target.value })}>
                          <option value="">Select…</option>
                          {data.next_statuses.map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                      </div>
                      <div className="form-group" style={{ margin: 0, flex: 2 }}>
                        <label className="label">Comment <span className="text-danger">*</span></label>
                        <input className="input" required placeholder="Required comment…" value={statusForm.comment} onChange={(e) => setStatusForm({ ...statusForm, comment: e.target.value })} />
                      </div>
                      <div className="form-group" style={{ margin: 0 }}>
                        <label className="label">Actual spend (₹)</label>
                        <input className="input" type="number" step="0.01" min="0" placeholder="Optional" value={statusForm.actual_spend} onChange={(e) => setStatusForm({ ...statusForm, actual_spend: e.target.value })} />
                      </div>
                      <button type="submit" className="btn btn-primary btn-sm">Update</button>
                    </form>
                    {wo.status === "Completed" && (
                      <div className="alert alert-warn" style={{ marginTop: "0.75rem", marginBottom: 0, fontSize: "0.82rem" }}>
                        To move to <strong>Verified</strong>, a PASS completion photo validation is required.{" "}
                        <Link to={`/validate/${wo.id}`}>Upload validation photo →</Link>
                      </div>
                    )}
                  </div>
                </MotionCard>
              )}

              <MotionCard className="card" delay={0.12}>
                <CardHeader title="Pothole locations" />
                <div className="card-body-flush" style={{ padding: "0.5rem" }}>
                  <PotholeMap points={mapPts} height={300} />
                </div>
              </MotionCard>

              <MotionCard className="card" delay={0.14}>
                <CardHeader title="Pothole details" badge={<span className="badge badge-created">{(data.potholes || []).length}</span>} />
                <div className="table-wrap table-template">
                  <table>
                    <thead>
                      <tr>
                        <th>#</th><th>Severity</th><th>Confidence</th><th>Location</th><th>Address</th><th>Status</th><th>Map</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.potholes || []).map((p, i) => (
                        <tr key={p.id || i}>
                          <td>{i + 1}</td>
                          <td><StatusBadge status={p.severity} /></td>
                          <td>{p.conf != null ? `${(p.conf * 100).toFixed(1)}%` : "—"}</td>
                          <td>{p.lat ? `${Number(p.lat).toFixed(5)}, ${Number(p.lon).toFixed(5)}` : "—"}</td>
                          <td className="text-truncate" style={{ maxWidth: 160 }} title={p.full_address}>{p.city || "—"}</td>
                          <td><StatusBadge status={p.pothole_status || "Open"} /></td>
                          <td>
                            {p.map_link && (
                              <a href={p.map_link} target="_blank" rel="noreferrer" className="btn btn-sm">Map</a>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </MotionCard>

              {validation ? (
                <MotionCard className="card" delay={0.16}>
                  <CardHeader
                    title="Completion validation"
                    badge={<StatusBadge status={validation.final_result || validation.ai_result || "?"} />}
                  />
                  <div className="card-body" style={{ fontSize: "0.85rem" }}>
                    <div className="validation-steps" style={{ marginBottom: "1rem" }}>
                      <div>
                        <div className="kpi-value" style={{ fontSize: "1.5rem" }}>{validation.resolution_score?.toFixed?.(1) ?? validation.resolution_score}%</div>
                        <div className="kpi-label">Resolution score</div>
                      </div>
                      <div><StatusBadge status={`GPS ${validation.gps_check}`} /></div>
                      <div><StatusBadge status={`TS ${validation.timestamp_check}`} /></div>
                      <div>
                        <StatusBadge status={validation.yolo_pothole_detected ? `YOLO ${Math.round((validation.yolo_confidence || 0) * 100)}%` : "YOLO clear"} />
                      </div>
                    </div>
                    {validation.after_photo_s3_url && (
                      <a href={validation.after_photo_s3_url} target="_blank" rel="noreferrer" className="btn btn-sm">View after photo</a>
                    )}
                    {validation.ai_result === "PARTIAL" && !validation.final_result && user?.is_supervisor && (
                      <form onSubmit={doReview} className="filter-row" style={{ marginTop: "1rem" }}>
                        <div className="form-group" style={{ margin: 0 }}>
                          <label className="label">Override</label>
                          <select className="select" value={review.final_result} onChange={(e) => setReview({ ...review, final_result: e.target.value })}>
                            <option value="PASS">PASS — repair accepted</option>
                            <option value="FAIL">FAIL — repair rejected</option>
                          </select>
                        </div>
                        <div className="form-group" style={{ margin: 0, flex: 2 }}>
                          <label className="label">Comment <span className="text-danger">*</span></label>
                          <input className="input" required value={review.comment} onChange={(e) => setReview({ ...review, comment: e.target.value })} />
                        </div>
                        <button type="submit" className="btn btn-sm btn-primary">Submit override</button>
                      </form>
                    )}
                    {validation.review_comment && (
                      <div className="alert alert-ok" style={{ marginTop: "0.75rem", marginBottom: 0, fontSize: "0.82rem" }}>
                        <strong>Supervisor note:</strong> {validation.review_comment}
                        {validation.reviewed_by && <em> (by {validation.reviewed_by})</em>}
                      </div>
                    )}
                  </div>
                </MotionCard>
              ) : (wo.status === "Completed" || wo.status === "WIP") && (
                <div className="alert alert-warn">
                  No completion validation yet. <Link to={`/validate/${wo.id}`}>Upload completion photo →</Link>
                </div>
              )}

              <MotionCard className="card" delay={0.18}>
                <CardHeader title="Status history" />
                <div className="card-body">
                  {(data.history || []).length === 0 ? (
                    <p className="muted" style={{ margin: 0 }}>No history yet.</p>
                  ) : (
                    (data.history || []).map((h, i) => (
                      <div key={i} style={{ marginBottom: "0.85rem", fontSize: "0.82rem" }}>
                        <StatusBadge status={h.to_status} />
                        {h.from_status && <span className="muted"> ← {h.from_status}</span>}
                        {h.comment && <div className="muted" style={{ marginTop: "0.25rem" }}>{h.comment}</div>}
                        <div className="muted" style={{ fontSize: "0.72rem", marginTop: "0.2rem" }}>{h.changed_by} · {fmtDate(h.changed_at)}</div>
                      </div>
                    ))
                  )}
                </div>
              </MotionCard>
            </div>
          </div>
        </>
      )}
    </PageShell>
  );
}
