import { useCallback, useState } from "react";
import { Navigate, Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import "./FieldCapture.css";
function StatusBanner({ status }) {
  if (!status?.message) return null;
  const kind = status.kind === "error" ? "alert-error" : status.kind === "warn" ? "alert-warn" : "alert-ok";
  return <div className={`det-banner alert ${kind}`}>{status.message}</div>;
}

export default function FieldUpload() {
  const { user } = useAuth();
  const [mediaFile, setMediaFile] = useState(null);
  const [gpsFile, setGpsFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(null);
  const [result, setResult] = useState(null);

  const { data: statusData, loading } = useAsync(() => api.uploadStatus(), []);
  const { data: keysData, reload: reloadKeys } = useAsync(() => api.uploadKeys(), []);

  const submit = useCallback(async (e) => {
    e.preventDefault();
    if (!mediaFile) return;
    setUploading(true);
    setUploadPct(0);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("media", mediaFile);
      if (gpsFile) fd.append("gps_log", gpsFile);
      const res = await api.uploadField(fd, {
        onProgress: ({ percent }) => setUploadPct(percent),
      });
      setUploadPct(100);
      setResult(res);
      reloadKeys();
      setMediaFile(null);
      setGpsFile(null);
    } catch (ex) {
      setResult({ status: { kind: "error", message: ex.message } });
    } finally {
      setUploading(false);
      setUploadPct(null);
    }
  }, [mediaFile, gpsFile, reloadKeys]);

  if (!user?.is_videographer) return <Navigate to="/" replace />;

  return (
    <PageShell
      title="Upload"
      subtitle="Send survey media to S3 for admin Detection"
      loading={loading}
    >
      <div className="field-page">
        <StatusBanner status={statusData?.status} />

        <MotionCard className="card" delay={0}>
          <CardHeader
            title="Upload to S3"
            actions={<Link to="/capture" className="btn btn-sm">Use Capture instead</Link>}
          />
          <div className="card-body">
            <p className="det-hint">
              Files go to the <strong>input S3 bucket</strong>. Admins run them from{" "}
              <strong>Detection → S3 bucket</strong>. For videos, include a GPS log
              (CSV / XLSX / JSON) with matching stem — distance covered and road sealing
              are taken from those lat/longs.
            </p>

            <form onSubmit={submit} className="field-page">
              <div className="form-group">
                <label className="label" htmlFor="up-media">Video or image <span className="text-danger">*</span></label>
                <input
                  id="up-media"
                  type="file"
                  accept="image/*,video/*"
                  required
                  onChange={(e) => setMediaFile(e.target.files?.[0] || null)}
                />
              </div>
              <div className="form-group">
                <label className="label" htmlFor="up-gps">GPS log (recommended for video)</label>
                <input
                  id="up-gps"
                  type="file"
                  accept=".csv,.xlsx,.xls,.json"
                  onChange={(e) => setGpsFile(e.target.files?.[0] || null)}
                />
              </div>
              <div className="field-actions">
                <button type="submit" className="btn btn-primary" disabled={uploading || !mediaFile}>
                  {uploading
                    ? (uploadPct != null ? `Uploading ${uploadPct}%…` : "Uploading…")
                    : "Upload to S3"}
                </button>
              </div>
              {uploading && uploadPct != null && (
                <div className="upload-progress" style={{ marginTop: "0.75rem" }}>
                  <div
                    style={{
                      height: 8,
                      borderRadius: 4,
                      background: "var(--border, #d7dee8)",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${uploadPct}%`,
                        height: "100%",
                        background: "var(--navy, #0b2a4a)",
                        transition: "width 0.15s ease",
                      }}
                    />
                  </div>
                  <p className="muted" style={{ marginTop: 6, fontSize: "0.875rem" }}>
                    {uploadPct}% done · {Math.max(0, 100 - uploadPct)}% left
                  </p>
                </div>
              )}
            </form>

            {result?.status && <StatusBanner status={result.status} />}
            {result?.keys?.length > 0 && (
              <ul className="field-keys">
                {result.keys.map((k) => <li key={k}><code>{k}</code></li>)}
              </ul>
            )}
          </div>
        </MotionCard>

        <MotionCard className="card" delay={0.04}>
          <CardHeader
            title="Recent uploads"
            badge={<span className="badge badge-created">{(keysData?.keys || []).length}</span>}
          />
          <div className="card-body">
            {(keysData?.keys || []).length ? (
              <ul className="field-keys">
                {keysData.keys.map((k) => <li key={k}><code>{k}</code></li>)}
              </ul>
            ) : (
              <p className="field-meta muted">No files listed yet.</p>
            )}
          </div>
        </MotionCard>
      </div>
    </PageShell>
  );
}
