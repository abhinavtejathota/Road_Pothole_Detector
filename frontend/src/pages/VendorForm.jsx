import { useState, useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import PageShell from "../components/PageShell";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";

export default function VendorForm({ edit }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [form, setForm] = useState({ max_active_tasks: 10, specializations: "", description: "" });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(edit);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (edit && id) {
      api.vendor(id).then((d) => {
        setForm({
          ...d.vendor,
          specializations: (d.vendor.specializations || []).join(", "),
        });
        setLoading(false);
      });
    }
  }, [edit, id]);

  if (edit && loading) {
    return <PageShell title="Edit vendor" loading skeleton="table" error={null} />;
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      if (edit) await api.updateVendor(id, form);
      else await api.createVendor(form);
      navigate(edit ? `/vendors/${id}` : "/vendors");
    } catch (ex) {
      setErr(ex.message);
      setSaving(false);
    }
  };

  const title = edit ? "Edit vendor" : "Add vendor";

  return (
    <PageShell title={title}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem" }}>
        <Link to="/vendors" className="btn btn-sm">← Vendors</Link>
        <h1 className="page-title" style={{ margin: 0 }}>{title}</h1>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      <MotionCard className="card" style={{ maxWidth: 800 }}>
        <form onSubmit={submit} className="card-body">
          <h2 className="form-section">Company information</h2>
          <div className="form-row">
            <div className="form-group">
              <label className="label">Company / vendor name <span className="text-danger">*</span></label>
              <input className="input" required value={form.company_name || ""} onChange={(e) => set("company_name", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">Registration number (CIN/UCIN)</label>
              <input className="input" value={form.registration_number || ""} onChange={(e) => set("registration_number", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">GST number</label>
              <input className="input" value={form.gst_number || ""} onChange={(e) => set("gst_number", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">PAN number</label>
              <input className="input" value={form.pan_number || ""} onChange={(e) => set("pan_number", e.target.value)} />
            </div>
          </div>
          <div className="form-group">
            <label className="label">Description / experience</label>
            <textarea className="input" rows={3} value={form.description || ""} onChange={(e) => set("description", e.target.value)} />
          </div>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label className="label">Specializations <span className="muted">(comma-separated)</span></label>
              <input className="input" placeholder="pothole-patching, resurfacing" value={form.specializations || ""} onChange={(e) => set("specializations", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">Max simultaneous tasks</label>
              <input className="input" type="number" min={1} max={100} value={form.max_active_tasks || 10} onChange={(e) => set("max_active_tasks", e.target.value)} />
            </div>
          </div>

          <h2 className="form-section">Contact information</h2>
          <div className="form-row">
            <div className="form-group">
              <label className="label">Contact person</label>
              <input className="input" value={form.contact_person_name || ""} onChange={(e) => set("contact_person_name", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">Phone</label>
              <input className="input" type="tel" value={form.contact_phone || ""} onChange={(e) => set("contact_phone", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">Email</label>
              <input className="input" type="email" value={form.contact_email || ""} onChange={(e) => set("contact_email", e.target.value)} />
            </div>
          </div>

          <h2 className="form-section">Address</h2>
          <div className="form-group">
            <label className="label">Street address</label>
            <input className="input" value={form.address || ""} onChange={(e) => set("address", e.target.value)} />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="label">City</label>
              <input className="input" value={form.city || ""} onChange={(e) => set("city", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">District</label>
              <input className="input" value={form.district || ""} onChange={(e) => set("district", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">State</label>
              <input className="input" value={form.state || ""} onChange={(e) => set("state", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="label">PIN code</label>
              <input className="input" maxLength={6} value={form.pin_code || ""} onChange={(e) => set("pin_code", e.target.value)} />
            </div>
          </div>

          {edit && (
            <>
              <h2 className="form-section">Status</h2>
              <div className="form-row">
                <div className="form-group">
                  <label className="label">Vendor status</label>
                  <select className="select" value={form.status || "Active"} onChange={(e) => set("status", e.target.value)}>
                    <option>Active</option>
                    <option>Suspended</option>
                    <option>Blacklisted</option>
                  </select>
                </div>
                <div className="form-group" style={{ flex: 2 }}>
                  <label className="label">Status reason</label>
                  <input className="input" placeholder="Reason for suspension/blacklisting…" value={form.status_reason || ""} onChange={(e) => set("status_reason", e.target.value)} />
                </div>
              </div>
            </>
          )}

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "1.25rem" }}>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Saving…" : `${edit ? "Edit" : "Add"} vendor`}
            </button>
            <button type="button" className="btn" onClick={() => navigate(-1)}>Cancel</button>
            {saving && <Loader />}
          </div>
        </form>
      </MotionCard>
    </PageShell>
  );
}
