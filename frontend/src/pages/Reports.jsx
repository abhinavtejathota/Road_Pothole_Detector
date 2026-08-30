import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import PageShell from "../components/PageShell";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";

/**
 * Admin Reports — browse DOCX detection reports by videographer (Legacy for unassigned).
 */
export default function Reports() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [groups, setGroups] = useState([]);
  const [selectedUser, setSelectedUser] = useState("");
  const [selectedSession, setSelectedSession] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.reportsList();
      const list = res.videographers || [];
      setGroups(list);
      if (list.length) {
        setSelectedUser((prev) => prev || list[0].username);
      }
    } catch (e) {
      setError(e.message || "Failed to load reports");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const sessions = useMemo(() => {
    const g = groups.find((x) => x.username === selectedUser);
    return g?.sessions || [];
  }, [groups, selectedUser]);

  useEffect(() => {
    if (sessions.length) {
      setSelectedSession(String(sessions[0].id));
    } else {
      setSelectedSession("");
    }
  }, [selectedUser, sessions]);

  const openReport = async () => {
    if (!selectedSession) return;
    setBusy(true);
    setStatus("Opening report…");
    try {
      const res = await api.reportsOpen(Number(selectedSession));
      if (res?.url) {
        const href = res.url.startsWith("http")
          ? res.url
          : `${window.location.origin}${res.url}`;
        window.open(href, "_blank", "noopener,noreferrer");
        setStatus("Report opened in a new tab.");
      } else if (res?.ok === false) {
        setStatus(res.error || "Report unavailable");
      } else {
        setStatus("Report downloaded / opened.");
      }
    } catch (e) {
      setStatus(e.message || "Open failed");
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    if (!selectedSession) return;
    setBusy(true);
    setStatus("Generating report…");
    try {
      const res = await api.reportsGenerate(Number(selectedSession));
      if (res?.ok) {
        setStatus(`Report ready: ${res.report_s3_key || res.display_name}`);
        await load();
      } else {
        setStatus(res?.error || "Generate failed");
      }
    } catch (e) {
      setStatus(e.message || "Generate failed");
    } finally {
      setBusy(false);
    }
  };

  if (!user?.is_admin && !user?.is_dev_admin) return <Navigate to="/" replace />;

  return (
    <PageShell title="Reports" subtitle="Detection DOCX reports by videographer">
      <MotionCard className="card" delay={0}>
        <div className="card-body">
          <p className="muted" style={{ marginBottom: "1rem" }}>
            Generated DOCX reports only (S3 <code>smartroad-reports/&lt;username&gt;/</code>).
            Use Detection for raw videos; <strong>Generate / refresh</strong> creates a report
            from a processed session when needed.
          </p>

          {loading ? (
            <Loader />
          ) : error ? (
            <div className="alert alert-error">{error}</div>
          ) : (
            <div className="reports-form">
              <div className="form-group">
                <label className="label" htmlFor="rep-user">Videographer</label>
                <select
                  id="rep-user"
                  className="input"
                  value={selectedUser}
                  onChange={(e) => setSelectedUser(e.target.value)}
                >
                  {!groups.length && <option value="">No sessions yet</option>}
                  {groups.map((g) => (
                    <option key={g.username} value={g.username}>
                      {g.username} ({g.sessions?.length || 0})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="label" htmlFor="rep-file">Detected video / report</label>
                <select
                  id="rep-file"
                  className="input"
                  value={selectedSession}
                  onChange={(e) => setSelectedSession(e.target.value)}
                  disabled={!sessions.length}
                >
                  {!sessions.length && <option value="">No files</option>}
                  {sessions.map((s) => (
                    <option key={s.id} value={s.id} title={[s.filename, s.route_short].filter(Boolean).join(" · ")}>
                      #{s.id} · {s.filename}
                      {s.route_short ? ` · ${s.route_short}` : ""}
                      {s.has_report ? "" : " · (no report)"}
                      {s.is_legacy ? " · Legacy" : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div className="det-dash-actions" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                <button type="button" className="btn btn-primary" onClick={openReport} disabled={busy || !selectedSession}>
                  Open report
                </button>
                <button type="button" className="btn" onClick={generate} disabled={busy || !selectedSession}>
                  Generate / refresh
                </button>
                <button type="button" className="btn" onClick={load} disabled={busy}>
                  Refresh list
                </button>
              </div>
              {status ? <p className="det-status-line text-success">{status}</p> : null}

              {selectedSession && (
                <div className="muted" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
                  {(() => {
                    const s = sessions.find((x) => String(x.id) === String(selectedSession));
                    if (!s) return null;
                    return (
                      <>
                        Session #{s.id}
                        {s.start_label && s.end_label ? ` · ${s.start_label} → ${s.end_label}` : ""}
                        {" · "}
                        {s.total_potholes} potholes · {s.processed_at}
                      </>
                    );
                  })()}
                </div>
              )}
            </div>
          )}
        </div>
      </MotionCard>
    </PageShell>
  );
}
