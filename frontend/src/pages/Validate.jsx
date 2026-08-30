import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";

const STEPS = [
  { icon: "📍", title: "GPS check", desc: "Photo must be within 30m of repair site" },
  { icon: "🕐", title: "Timestamp", desc: "Photo must be taken after task was allocated" },
  { icon: "🤖", title: "YOLO AI", desc: "System checks if pothole is still visible" },
  { icon: "✓", title: "Result", desc: "PASS / PARTIAL / FAIL with score" },
];

export default function Validate() {
  const { woId } = useParams();
  const navigate = useNavigate();
  const { data: ctx, loading, error } = useAsync(() => api.validateContext(woId), [woId]);
  const [file, setFile] = useState(null);
  const [manual, setManual] = useState({ lat: "", lon: "" });
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const onFile = (e) => {
    const f = e.target.files?.[0];
    setFile(f);
    if (f) setPreview(URL.createObjectURL(f));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!file) return setErr("Photo required");
    setBusy(true);
    setErr("");
    const fd = new FormData();
    fd.append("after_photo", file);
    if (manual.lat) fd.append("manual_lat", manual.lat);
    if (manual.lon) fd.append("manual_lon", manual.lon);
    try {
      await api.validateUpload(woId, fd);
      navigate(`/tasks/${woId}`);
    } catch (ex) {
      setErr(ex.message);
      setBusy(false);
    }
  };

  const ref = ctx?.ref_pothole;

  return (
    <PageShell title="Upload completion photo" loading={loading} error={error} skeleton="table">
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap", marginBottom: "1rem" }}>
        <Link to={`/tasks/${woId}`} className="btn btn-sm">← Work order #{woId}</Link>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <MotionCard className="card" delay={0.05} style={{ marginBottom: "1rem", background: "var(--surface-2)" }}>
          <div className="card-body">
            <h3 className="section-label" style={{ marginTop: 0 }}>How AI validation works</h3>
            <div className="validation-steps">
              {STEPS.map((s) => (
                <div key={s.title}>
                  <div className="validation-step-icon">{s.icon}</div>
                  <div><strong>{s.title}</strong><br />{s.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </MotionCard>

        {ref?.frame_s3_url && (
          <MotionCard className="card" delay={0.08} style={{ marginBottom: "1rem" }}>
            <CardHeader title="Reference — before photo" />
            <div className="card-body" style={{ textAlign: "center" }}>
              <img
                src={ref.frame_s3_url}
                alt="Before repair"
                className="preview-img"
                style={{ maxHeight: 280 }}
                onError={(e) => { e.target.style.display = "none"; }}
              />
              <div className="muted" style={{ fontSize: "0.82rem", marginTop: "0.5rem" }}>
                {ref.full_address || ref.city || "Location recorded"}
              </div>
            </div>
          </MotionCard>
        )}

        <MotionCard className="card" delay={0.1}>
          <CardHeader title="Upload after photo" />
          <div className="card-body">
            <form onSubmit={submit}>
              <div className="form-group">
                <label className="label">After-repair photo <span className="text-danger">*</span></label>
                <input type="file" accept="image/*" onChange={onFile} required />
                <p className="muted" style={{ fontSize: "0.78rem", marginTop: "0.35rem" }}>
                  Use a photo taken on-site with GPS enabled. EXIF GPS data will be used automatically.
                </p>
              </div>
              {preview && (
                <motion.div style={{ textAlign: "center", marginBottom: "1rem" }} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <img src={preview} alt="Preview" className="preview-img" style={{ maxHeight: 250 }} />
                </motion.div>
              )}
              <div className="card" style={{ background: "var(--surface-2)", marginBottom: "1rem", padding: "0.85rem" }}>
                <p className="label" style={{ marginBottom: "0.5rem" }}>Manual GPS (if photo has no EXIF location)</p>
                <div className="form-row">
                  <div className="form-group">
                    <input
                      className="input"
                      type="number"
                      step="any"
                      placeholder="Latitude"
                      value={manual.lat}
                      onChange={(e) => setManual({ ...manual, lat: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <input
                      className="input"
                      type="number"
                      step="any"
                      placeholder="Longitude"
                      value={manual.lon}
                      onChange={(e) => setManual({ ...manual, lon: e.target.value })}
                    />
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button type="submit" className="btn btn-primary" disabled={busy}>
                  {busy ? "Processing…" : "Submit for validation"}
                </button>
                <Link to={`/tasks/${woId}`} className="btn">Cancel</Link>
              </div>
              {busy && <Loader />}
            </form>
          </div>
        </MotionCard>
      </div>
    </PageShell>
  );
}
