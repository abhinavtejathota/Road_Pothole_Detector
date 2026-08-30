import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import { usePageTitle } from "../hooks/usePageTitle";
import PageShell from "../components/PageShell";
import { MotionCard } from "../components/PageTransition";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";

function scoreClass(score) {
  if (score >= 75) return "score-fill score-fill-good";
  if (score >= 50) return "score-fill score-fill-mid";
  return "score-fill score-fill-low";
}

export default function Vendors() {
  usePageTitle();
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [applied, setApplied] = useState({ q: "", status: "" });
  const { data, loading, error } = useAsync(
    () => api.vendors(applied),
    [applied.q, applied.status],
  );

  const vendors = Array.isArray(data) ? data : [];

  const search = (e) => {
    e.preventDefault();
    setApplied({ q, status });
  };

  return (
    <PageShell
      title="Vendors"
      skeleton="grid"
      loading={loading}
      error={error}
      actions={user?.is_allocator && <Link to="/vendors/new" className="btn btn-primary btn-sm">Add vendor</Link>}
    >
      <form className="filter-row" style={{ marginBottom: "1.25rem" }} onSubmit={search}>
        <input
          className="input"
          style={{ flex: "1 1 240px" }}
          placeholder="Search by name, city, PIN…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="select" style={{ maxWidth: 160 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="Active">Active</option>
          <option value="Suspended">Suspended</option>
          <option value="Blacklisted">Blacklisted</option>
        </select>
        <button type="submit" className="btn btn-primary btn-sm">Search</button>
      </form>

      {vendors.length === 0 ? (
        <EmptyState
          message="No vendors found"
          hint={user?.is_allocator ? "Add your first vendor to get started." : "Vendors will appear here once created."}
        />
      ) : (
        <div className="vendor-grid">
          {vendors.map((v, i) => {
            const score = Math.round(v.performance_score || 0);
            const specs = Array.isArray(v.specializations) ? v.specializations : [];
            return (
              <MotionCard key={v.id} delay={i * 0.05} className="card" style={{ padding: 0, display: "flex", flexDirection: "column" }}>
                <div className="card-body" style={{ flex: 1 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
                    <strong>{v.company_name || "Unnamed"}</strong>
                    <StatusBadge status={v.status || "Active"} />
                  </div>
                  <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.35rem" }}>
                    {[v.city, v.state].filter(Boolean).join(", ")}
                    {v.pin_code ? ` — ${v.pin_code}` : ""}
                  </p>
                  {v.registration_number && (
                    <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.35rem" }}>Reg: {v.registration_number}</p>
                  )}
                  <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.75rem" }}>{v.contact_phone || "—"}</p>
                  <div style={{ marginBottom: "0.5rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "var(--muted)", marginBottom: "0.25rem" }}>
                      <span>Performance score</span>
                      <strong style={{ color: "var(--text)" }}>{score}%</strong>
                    </div>
                    <div className="score-bar">
                      <div className={scoreClass(score)} style={{ width: `${Math.min(100, score)}%` }} />
                    </div>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "var(--muted)", marginBottom: "0.5rem" }}>
                    <span>Active tasks</span>
                    <span>{v.active_tasks ?? 0} / {v.max_active_tasks ?? 10}</span>
                  </div>
                  {specs.length > 0 && (
                    <div>
                      {specs.map((s) => <span key={s} className="spec-badge">{s}</span>)}
                    </div>
                  )}
                </div>
                <div className="vendor-card-actions">
                  <Link to={`/vendors/${v.id}`} className="btn btn-sm" style={{ flex: 1 }}>View</Link>
                  {user?.is_allocator && (
                    <Link to={`/vendors/${v.id}/edit`} className="btn btn-sm">Edit</Link>
                  )}
                </div>
              </MotionCard>
            );
          })}
        </div>
      )}
    </PageShell>
  );
}
