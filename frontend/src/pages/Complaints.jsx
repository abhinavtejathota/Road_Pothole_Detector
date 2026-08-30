import { useMemo, useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import Modal from "../components/Modal";
import { MotionCard } from "../components/PageTransition";
import EmptyState from "../components/EmptyState";
import "./Complaints.css";
const FILTERS = [
  { value: "", label: "All" },
  { value: "Submitted", label: "Pending review" },
  { value: "Verified", label: "Accepted" },
  { value: "Rejected", label: "Rejected" },
];

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function statusClass(status) {
  return `complaints-status complaints-status-${String(status || "")
    .toLowerCase()
    .replace(/_/g, "-")}`;
}

function statusText(status) {
  if (status === "Verified") return "Accepted";
  if (!status) return "—";
  return String(status).replace(/_/g, " ");
}

export default function Complaints() {
  const [filter, setFilter] = useState("Submitted");
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState("");
  const [actionOk, setActionOk] = useState("");
  const [remarks, setRemarks] = useState({});
  const [rejectTarget, setRejectTarget] = useState(null);
  const [rejectBusy, setRejectBusy] = useState(false);
  const [rejectError, setRejectError] = useState("");

  const params = useMemo(
    () => (filter ? { status: filter } : {}),
    [filter]
  );
  const { data, loading, error, reload } = useAsync(
    () => api.complaints(params),
    [filter]
  );
  const items = data?.complaints || [];

  const setRemark = (id, value) => {
    setRemarks((prev) => ({ ...prev, [id]: value }));
  };

  const closeRejectModal = () => {
    if (rejectBusy) return;
    setRejectTarget(null);
    setRejectError("");
  };

  const openRejectModal = (c) => {
    const remark = (remarks[c.id] || "").trim();
    if (remark.length < 3) {
      setActionError("Enter a rejection remark explaining why this report is unwanted.");
      setActionOk("");
      return;
    }
    setActionError("");
    setRejectError("");
    setRejectTarget(c);
  };

  const confirmReject = async (e) => {
    e?.preventDefault?.();
    if (!rejectTarget) return;
    const id = rejectTarget.id;
    const remark = (remarks[id] || "").trim();
    if (remark.length < 3) {
      setRejectError("Enter a rejection remark explaining why this report is unwanted.");
      return;
    }
    setRejectBusy(true);
    setRejectError("");
    setBusyId(id);
    setActionError("");
    setActionOk("");
    try {
      const out = await api.rejectComplaint(id, { remark });
      setActionOk(out.message || `Complaint ${out.tracking_number || ""} rejected.`);
      setRemarks((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setRejectTarget(null);
      reload();
    } catch (err) {
      setRejectError(err.message || "Could not reject complaint");
    } finally {
      setRejectBusy(false);
      setBusyId(null);
    }
  };

  const review = async (id, action) => {
    if (action === "reject") return;
    setBusyId(id);
    setActionError("");
    setActionOk("");
    try {
      const out = await api.verifyComplaint(id);
      setActionOk(out.message || `Complaint ${out.tracking_number || ""} accepted.`);
      setRemarks((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      reload();
    } catch (err) {
      setActionError(err.message || "Could not accept complaint");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <PageShell
      title="Citizen complaints"
      titleIcon={<i className="bi bi-chat-left-text page-title-icon" aria-hidden />}
      subtitle="Accept valid reports or reject unwanted ones with a remark — citizens see the result on Track complaint."
      loading={loading}
      error={error}
      skeleton="grid"
      actions={
        <div className="complaints-filters">
          {FILTERS.map((f) => (
            <button
              key={f.value || "all"}
              type="button"
              className={`btn btn-sm${filter === f.value ? " btn-primary" : ""}`}
              onClick={() => setFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      {actionError && <div className="alert alert-error">{actionError}</div>}
      {actionOk && <div className="alert alert-ok">{actionOk}</div>}

      {items.length === 0 ? (
        <EmptyState
          message="No complaints in this filter"
          hint="New citizen reports appear here as Pending review."
        />
      ) : (
        items.map((c, i) => (
          <MotionCard
            key={c.id}
            className="card"
            delay={i * 0.04}
            style={{ marginBottom: "0.85rem" }}
          >
            <CardHeader
              title={c.tracking_number}
              badge={
                <span className={statusClass(c.status)}>{statusText(c.status)}</span>
              }
            />
            <div className="card-body">
              <p className="muted" style={{ margin: "0 0 0.5rem", fontSize: "0.82rem" }}>
                {c.defect_type}
                {c.road_id ? ` · Road ${c.road_id}` : ""}
                {" · "}
                {formatDate(c.created_at)}
                {" · "}
                {c.reporter_mobile_masked || "—"}
              </p>
              {c.description && (
                <p style={{ margin: "0 0 0.65rem", fontSize: "0.88rem" }}>{c.description}</p>
              )}
              {c.status === "Rejected" && c.rejection_remark && (
                <p className="alert alert-warn" style={{ marginBottom: "0.65rem" }}>
                  <strong>Remark:</strong> {c.rejection_remark}
                </p>
              )}
              {c.status === "Submitted" && (
                <div className="form-group" style={{ marginBottom: "0.65rem" }}>
                  <label className="label" htmlFor={`remark-${c.id}`}>
                    Rejection remark (required to reject)
                  </label>
                  <textarea
                    id={`remark-${c.id}`}
                    className="input"
                    rows={2}
                    placeholder="Why is this report unwanted / invalid?"
                    value={remarks[c.id] || ""}
                    onChange={(e) => setRemark(c.id, e.target.value)}
                    disabled={busyId === c.id}
                  />
                </div>
              )}
              <div className="complaints-actions">
                {c.s3_url && (
                  <a
                    href={c.s3_url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn btn-sm"
                  >
                    View media
                  </a>
                )}
                {c.status === "Submitted" && (
                  <>
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      disabled={busyId === c.id}
                      onClick={() => review(c.id, "verify")}
                    >
                      {busyId === c.id ? "Saving…" : "Accept"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={busyId === c.id}
                      onClick={() => openRejectModal(c)}
                    >
                      Reject
                    </button>
                  </>
                )}
              </div>
            </div>
          </MotionCard>
        ))
      )}

      <Modal
        open={Boolean(rejectTarget)}
        onClose={closeRejectModal}
        title="Reject complaint"
        className="modal-form"
      >
        <form onSubmit={confirmReject}>
          {rejectError && <div className="alert alert-error">{rejectError}</div>}
          <div className="modal-body">
            <p>
              Reject complaint{" "}
              <strong>{rejectTarget?.tracking_number || `#${rejectTarget?.id}`}</strong>?
            </p>
            <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
              The citizen will see this remark on Track complaint.
            </p>
            {rejectTarget && (
              <p style={{ marginTop: "0.75rem", fontSize: "0.88rem" }}>
                <strong>Remark:</strong> {(remarks[rejectTarget.id] || "").trim() || "—"}
              </p>
            )}
          </div>
          <div className="modal-actions">
            <button type="submit" className="btn btn-danger" disabled={rejectBusy}>
              {rejectBusy ? "Rejecting…" : "Reject complaint"}
            </button>
            <button type="button" className="btn" onClick={closeRejectModal} disabled={rejectBusy}>
              Cancel
            </button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
