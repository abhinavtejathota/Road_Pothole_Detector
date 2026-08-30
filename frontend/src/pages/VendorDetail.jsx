import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import StatusBadge from "../components/StatusBadge";

function scoreClass(score) {
  if (score >= 75) return "text-success";
  if (score >= 50) return "text-warn";
  return "text-danger";
}

export default function VendorDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data, loading, error } = useAsync(() => api.vendor(id), [id]);

  const remove = async () => {
    if (!confirm("Delete this vendor? This cannot be undone.")) return;
    await api.deleteVendor(id);
    navigate("/vendors");
  };

  const vendor = data?.vendor;
  const work_orders = data?.work_orders || [];
  const score = Math.round(vendor?.performance_score || 0);
  const specs = Array.isArray(vendor?.specializations) ? vendor.specializations : [];

  return (
    <PageShell loading={loading} error={error} skeleton="table">
      {vendor && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap", marginBottom: "1.25rem" }}>
            <Link to="/vendors" className="btn btn-sm">← Vendors</Link>
            <h1 className="page-title" style={{ margin: 0 }}>{vendor.company_name}</h1>
            <StatusBadge status={vendor.status} />
            {user?.is_allocator && (
              <Link to={`/vendors/${id}/edit`} className="btn btn-sm" style={{ marginLeft: "auto" }}>Edit</Link>
            )}
            {user?.is_dev_admin && (
              <button type="button" className="btn btn-sm btn-danger" onClick={remove}>Delete</button>
            )}
          </div>

          <div className="detail-layout">
            <div className="detail-side">
              <MotionCard className="card" delay={0.05}>
                <CardHeader title="Company details" />
                <div className="card-body" style={{ fontSize: "0.85rem", lineHeight: 1.9 }}>
                  {vendor.registration_number && <div><strong>Reg. no:</strong> {vendor.registration_number}</div>}
                  {vendor.gst_number && <div><strong>GST:</strong> {vendor.gst_number}</div>}
                  {vendor.pan_number && <div><strong>PAN:</strong> {vendor.pan_number}</div>}
                  <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "0.75rem 0" }} />
                  <div><strong>Contact:</strong> {vendor.contact_person_name || "—"}</div>
                  <div><strong>Phone:</strong> {vendor.contact_phone || "—"}</div>
                  <div><strong>Email:</strong> {vendor.contact_email || "—"}</div>
                  <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "0.75rem 0" }} />
                  <div>
                    <strong>Address:</strong><br />
                    {[vendor.address, vendor.city, vendor.district, vendor.state].filter(Boolean).join(", ")}
                    {vendor.pin_code ? ` — ${vendor.pin_code}` : ""}
                  </div>
                  {specs.length > 0 && (
                    <>
                      <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "0.75rem 0" }} />
                      <strong>Specializations:</strong><br />
                      {specs.map((s) => <span key={s} className="spec-badge">{s}</span>)}
                    </>
                  )}
                </div>
              </MotionCard>

              <MotionCard className="card" delay={0.1}>
                <CardHeader title="Performance" />
                <div className="card-body">
                  <div style={{ textAlign: "center", marginBottom: "1rem" }}>
                    <span className={`kpi-value ${scoreClass(score)}`} style={{ fontSize: "2.5rem" }}>{score}</span>
                    <span className="muted" style={{ fontSize: "1.1rem" }}>/100</span>
                    <div className="score-bar" style={{ marginTop: "0.5rem", height: 8 }}>
                      <div className={`score-fill ${score >= 75 ? "score-fill-good" : score >= 50 ? "score-fill-mid" : "score-fill-low"}`} style={{ width: `${score}%` }} />
                    </div>
                  </div>
                  <div className="grid grid-2" style={{ gap: "0.75rem", textAlign: "center" }}>
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "0.75rem" }}>
                      <div className="kpi-value" style={{ fontSize: "1.25rem", color: "var(--warn)" }}>{vendor.active_tasks ?? 0}</div>
                      <div className="kpi-label">Active</div>
                    </div>
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "0.75rem" }}>
                      <div className="kpi-value" style={{ fontSize: "1.25rem", color: "var(--success)" }}>{vendor.completed_tasks ?? 0}</div>
                      <div className="kpi-label">Verified</div>
                    </div>
                  </div>
                  <p className="muted" style={{ fontSize: "0.78rem", textAlign: "center", marginTop: "0.75rem", marginBottom: 0 }}>
                    Capacity: {vendor.active_tasks ?? 0} / {vendor.max_active_tasks ?? 10} active tasks
                  </p>
                </div>
              </MotionCard>
            </div>

            <div className="detail-main">
              {vendor.description && (
                <MotionCard className="card" delay={0.08}>
                  <CardHeader title="Description" />
                  <div className="card-body" style={{ fontSize: "0.85rem" }}>{vendor.description}</div>
                </MotionCard>
              )}

              <MotionCard className="card" delay={0.12}>
                <CardHeader title="Assigned work orders" badge={<span className="badge badge-created">{work_orders.length}</span>} />
                <div className="table-wrap table-template">
                  <table>
                    <thead>
                      <tr>
                        <th>#</th><th>Video</th><th>Status</th><th>SLA tier</th><th>Due</th><th>Budget</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {work_orders.map((wo) => (
                        <tr key={wo.id} className={wo.sla_breached ? "sla-breach" : ""}>
                          <td>{wo.id}</td>
                          <td className="text-truncate" style={{ maxWidth: 150 }} title={wo.filename}>{wo.filename || "—"}</td>
                          <td><StatusBadge status={wo.status} /></td>
                          <td>{wo.sla_tier || "—"}</td>
                          <td>
                            {wo.sla_breached ? (
                              <span className="text-danger">BREACH</span>
                            ) : (
                              wo.sla_due_date?.slice?.(0, 16) || wo.sla_due_date || "—"
                            )}
                          </td>
                          <td>{wo.estimated_budget != null ? `₹${Math.round(wo.estimated_budget).toLocaleString()}` : "—"}</td>
                          <td><Link to={`/tasks/${wo.id}`} className="btn btn-sm">View</Link></td>
                        </tr>
                      ))}
                      {!work_orders.length && (
                        <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: "1.25rem" }}>No work orders assigned yet.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </MotionCard>
            </div>
          </div>
        </>
      )}
    </PageShell>
  );
}
