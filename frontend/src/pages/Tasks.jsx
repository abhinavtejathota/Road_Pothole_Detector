import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import Modal from "../components/Modal";
import { MotionCard } from "../components/PageTransition";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";

const EMPTY_FILTERS = { status: "", vendor_id: "", city: "", pin_code: "", date_from: "", date_to: "" };

export default function Tasks() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState({
    ...EMPTY_FILTERS,
    status: searchParams.get("status") || "",
  });
  const [applied, setApplied] = useState({ ...filters, status: searchParams.get("status") || "" });
  const query = Object.fromEntries(Object.entries(applied).filter(([, v]) => v));
  const { data, loading, error, reload } = useAsync(() => api.tasks(query), [JSON.stringify(query)]);
  const [confirmSession, setConfirmSession] = useState(null);
  const [creating, setCreating] = useState(false);
  const [woError, setWoError] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(null);

  const canCreateWo = Boolean(user?.is_allocator || user?.is_dev_admin);
  const canDeleteWo = Boolean(user?.is_dev_admin);

  const apply = (e) => {
    e.preventDefault();
    setApplied({ ...filters });
    reload();
  };

  const openConfirm = (session) => {
    setWoError("");
    setConfirmSession(session);
  };

  const closeConfirm = () => {
    if (!creating) setConfirmSession(null);
  };

  const createWo = async (e) => {
    e.preventDefault();
    if (!confirmSession) return;
    setCreating(true);
    setWoError("");
    try {
      const r = await api.createTask(confirmSession.id);
      setConfirmSession(null);
      navigate(`/tasks/${r.id}`);
    } catch (ex) {
      setWoError(ex.message || "Could not create work order");
    } finally {
      setCreating(false);
    }
  };

  const removeWo = async (wo) => {
    if (!canDeleteWo) return;
    if (!window.confirm(`Delete work order #${wo.id}? This cannot be undone.`)) return;
    setDeleteBusy(wo.id);
    try {
      await api.deleteTask(wo.id);
      reload();
    } catch (ex) {
      window.alert(ex.message || "Could not delete work order");
    } finally {
      setDeleteBusy(null);
    }
  };

  const workOrders = data?.work_orders ?? [];
  const unassigned = data?.unassigned_sessions ?? [];
  const vendors = data?.vendors ?? [];

  return (
    <PageShell title="Task allocation" skeleton="table" loading={loading} error={error}>
      <form className="card filter-card" onSubmit={apply}>
        <div className="filter-row">
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">Status</label>
            <select className="select" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
              <option value="">All</option>
              {["Created", "Allocated", "WIP", "Completed", "Verified", "Failed"].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">Vendor</label>
            <select className="select" value={filters.vendor_id} onChange={(e) => setFilters({ ...filters, vendor_id: e.target.value })}>
              <option value="">All vendors</option>
              {vendors.map((v) => <option key={v.id} value={v.id}>{v.company_name}</option>)}
            </select>
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">City</label>
            <input className="input" placeholder="City" value={filters.city} onChange={(e) => setFilters({ ...filters, city: e.target.value })} />
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">PIN code</label>
            <input className="input" placeholder="PIN" value={filters.pin_code} onChange={(e) => setFilters({ ...filters, pin_code: e.target.value })} />
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">From</label>
            <input className="input" type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="label">To</label>
            <input className="input" type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
          </div>
          <button type="submit" className="btn btn-primary btn-sm">Filter</button>
        </div>
      </form>

      {unassigned.length > 0 && (
        <div className="alert alert-warn">
          <strong>{unassigned.length} video session(s)</strong> have no work order yet.
          {canCreateWo && <a href="#unassigned" style={{ marginLeft: "0.35rem" }}>Create work orders ↓</a>}
        </div>
      )}

      {workOrders.length === 0 ? (
        <EmptyState message="No work orders found" hint="Adjust filters or create a work order from an unassigned session." />
      ) : (
        <MotionCard className="card" delay={0.05}>
          <CardHeader title="Work orders" badge={<span className="badge badge-created">{workOrders.length}</span>} />
          <div className="table-wrap table-template">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Video</th><th>Potholes</th><th>City</th>
                  <th>Status</th><th>SLA</th><th>Vendor</th><th>Budget</th><th>Processed</th><th></th>
                </tr>
              </thead>
              <tbody>
                {workOrders.map((wo) => (
                  <tr key={wo.id} className={wo.sla_breached ? "sla-breach" : ""}>
                    <td>{wo.id}</td>
                    <td style={{ maxWidth: 140 }} className="text-truncate" title={wo.filename}>{wo.filename || "—"}</td>
                    <td><span className="badge badge-created">{wo.total_potholes ?? "—"}</span></td>
                    <td>{wo.city || "—"}</td>
                    <td><StatusBadge status={wo.status} /></td>
                    <td>
                      {wo.sla_tier ? <StatusBadge status={wo.sla_tier} /> : "—"}
                      {wo.sla_breached && <div className="text-danger" style={{ fontSize: "0.72rem" }}>OVERDUE</div>}
                    </td>
                    <td>{wo.vendor_name || <span className="muted">Unassigned</span>}</td>
                    <td>{wo.estimated_budget != null ? `₹${Math.round(wo.estimated_budget).toLocaleString()}` : "—"}</td>
                    <td>{wo.processed_at?.slice?.(0, 10) || wo.processed_at || "—"}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <Link to={`/tasks/${wo.id}`} className="btn btn-sm">View</Link>
                      {canDeleteWo && (
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          style={{ marginLeft: "0.35rem" }}
                          disabled={deleteBusy === wo.id}
                          onClick={() => removeWo(wo)}
                        >
                          {deleteBusy === wo.id ? "…" : "Delete"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </MotionCard>
      )}

      {canCreateWo && unassigned.length > 0 && (
        <MotionCard className="card" delay={0.1} style={{ marginTop: "1rem" }} id="unassigned">
          <CardHeader title="Unassigned video sessions" />
          <div className="table-wrap table-template">
            <table>
              <thead><tr><th>#</th><th>Filename</th><th>Potholes</th><th>Processed</th><th>Action</th></tr></thead>
              <tbody>
                {unassigned.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
                    <td>{s.filename}</td>
                    <td><span className="badge badge-created">{s.total_potholes}</span></td>
                    <td>{s.processed_at?.slice?.(0, 10) || s.processed_at || "—"}</td>
                    <td>
                      <button type="button" className="btn btn-sm btn-primary" onClick={() => openConfirm(s)}>
                        Create work order
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </MotionCard>
      )}

      <Modal open={!!confirmSession} onClose={closeConfirm} title="Create work order">
        <form onSubmit={createWo}>
          {woError && <div className="alert alert-error">{woError}</div>}
          <div className="modal-body">
            <p>Create a work order for session <strong>#{confirmSession?.id}</strong>?</p>
            <p style={{ marginTop: "0.5rem" }}>
              <strong>File:</strong> {confirmSession?.filename || "—"}
              <br />
              <strong>Potholes:</strong> {confirmSession?.total_potholes ?? 0}
            </p>
          </div>
          <div className="modal-actions">
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? "Creating…" : "Create work order"}
            </button>
            <button type="button" className="btn" onClick={closeConfirm} disabled={creating}>Cancel</button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
