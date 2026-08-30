import { useMemo, useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import Modal from "../components/Modal";
import { MotionCard } from "../components/PageTransition";
import "./Videographers.css";

const EMPTY_DETAILS = { user_id: "", display_name: "", mobile: "", village: "", notes: "" };
const EMPTY_CREATE = { display_name: "", mobile: "", village: "", notes: "", state_id: "1" };

export default function Videographers() {
  const { data, loading, error, reload } = useAsync(() => api.vgDetails(), []);
  const rows = data?.videographers || [];

  const [form, setForm] = useState({ ...EMPTY_DETAILS });
  const [createForm, setCreateForm] = useState({ ...EMPTY_CREATE });
  const [editing, setEditing] = useState(null); // "add" | "edit" | "create" | null
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [createdCreds, setCreatedCreds] = useState(null);

  const selected = useMemo(
    () => rows.find((r) => String(r.id) === String(form.user_id)),
    [rows, form.user_id],
  );

  const openCreate = () => {
    setEditing("create");
    setCreateForm({ ...EMPTY_CREATE });
    setCreatedCreds(null);
    setErr("");
  };

  const openAddDetails = () => {
    setEditing("add");
    setForm({ ...EMPTY_DETAILS });
    setErr("");
  };

  const openEdit = (row) => {
    setEditing("edit");
    setForm({
      user_id: String(row.id),
      display_name: row.display_name || row.full_name || "",
      mobile: row.mobile || "",
      village: row.village || "",
      notes: row.notes || "",
    });
    setErr("");
  };

  const close = () => {
    setEditing(null);
    setErr("");
    setSaving(false);
  };

  const onSelectUser = (userId) => {
    const row = rows.find((r) => String(r.id) === String(userId));
    setForm((f) => ({
      ...f,
      user_id: userId,
      display_name: row?.display_name || row?.full_name || f.display_name || "",
      mobile: row?.mobile || "",
      village: row?.village || "",
      notes: row?.notes || "",
    }));
  };

  const saveDetails = async (e) => {
    e.preventDefault();
    setErr("");
    if (!form.user_id) {
      setErr("Select a videographer");
      return;
    }
    if (!form.mobile.trim() || !form.village.trim()) {
      setErr("Mobile and village are required");
      return;
    }
    setSaving(true);
    try {
      await api.upsertVgDetails(Number(form.user_id), {
        display_name: form.display_name.trim(),
        mobile: form.mobile.trim(),
        village: form.village.trim(),
        notes: form.notes.trim(),
      });
      close();
      reload();
    } catch (ex) {
      setErr(ex.message || "Could not save details");
    } finally {
      setSaving(false);
    }
  };

  const createVg = async (e) => {
    e.preventDefault();
    setErr("");
    if (!createForm.display_name.trim() || !createForm.mobile.trim() || !createForm.village.trim()) {
      setErr("Name, mobile and village are required");
      return;
    }
    setSaving(true);
    try {
      const res = await api.createVg({
        display_name: createForm.display_name.trim(),
        mobile: createForm.mobile.trim(),
        village: createForm.village.trim(),
        notes: createForm.notes.trim(),
        state_id: Number(createForm.state_id) || 1,
      });
      setCreatedCreds({
        username: res.username,
        password: res.password,
        display_name: res.display_name,
      });
      reload();
    } catch (ex) {
      setErr(ex.message || "Could not create videographer");
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageShell
      title="Videographers"
      subtitle="Add videographers and profile details (name, mobile, village)"
      loading={loading}
      error={error}
      actions={
        <>
          <button type="button" className="btn btn-sm" onClick={reload}>Refresh</button>
          <button type="button" className="btn btn-sm" onClick={openAddDetails}>Add details</button>
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>Add videographer</button>
        </>
      }
    >
      <MotionCard className="card" delay={0.05}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Username</th>
                <th>Mobile</th>
                <th>Village</th>
                <th>Details</th>
                <th style={{ width: 110 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <strong>{row.display_name || row.full_name || "—"}</strong>
                  </td>
                  <td className="muted">{row.username}</td>
                  <td>{row.mobile || <span className="muted">—</span>}</td>
                  <td>{row.village || <span className="muted">—</span>}</td>
                  <td>
                    {row.has_details ? (
                      <span className="badge badge-verified">Saved</span>
                    ) : (
                      <span className="badge badge-created">Missing</span>
                    )}
                  </td>
                  <td>
                    <button type="button" className="btn btn-sm" onClick={() => openEdit(row)}>
                      {row.has_details ? "Edit" : "Add details"}
                    </button>
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={6} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                    No videographers found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </MotionCard>

      <Modal
        open={editing === "create"}
        onClose={close}
        title="Add videographer"
        className="modal-form"
      >
        {createdCreds ? (
          <div className="modal-body vg-form">
            <p className="alert alert-success" style={{ marginTop: 0 }}>
              Videographer <strong>{createdCreds.display_name}</strong> created.
            </p>
            <p className="muted" style={{ margin: 0 }}>Login credentials (copy now — password won&apos;t be shown again):</p>
            <div className="vg-creds">
              <div><span className="label">Username</span> <code>{createdCreds.username}</code></div>
              <div><span className="label">Password</span> <code>{createdCreds.password}</code></div>
            </div>
            <div className="modal-footer" style={{ paddingLeft: 0, paddingRight: 0 }}>
              <button type="button" className="btn btn-primary" onClick={close}>Done</button>
            </div>
          </div>
        ) : (
          <form onSubmit={createVg}>
            <div className="modal-body vg-form">
              {err && <p className="alert alert-error">{err}</p>}
              <p className="muted" style={{ margin: 0 }}>
                Creates a <strong>Videographer</strong> account only. Username and password are generated automatically.
              </p>
              <label className="field">
                <span className="label">Videographer name</span>
                <input
                  className="input"
                  value={createForm.display_name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, display_name: e.target.value }))}
                  placeholder="Full name"
                  required
                />
              </label>
              <label className="field">
                <span className="label">Mobile</span>
                <input
                  className="input"
                  value={createForm.mobile}
                  onChange={(e) => setCreateForm((f) => ({ ...f, mobile: e.target.value }))}
                  placeholder="10-digit mobile"
                  required
                />
              </label>
              <label className="field">
                <span className="label">Village</span>
                <input
                  className="input"
                  value={createForm.village}
                  onChange={(e) => setCreateForm((f) => ({ ...f, village: e.target.value }))}
                  placeholder="Village / locality"
                  required
                />
              </label>
              <label className="field">
                <span className="label">State</span>
                <select
                  className="select"
                  value={createForm.state_id}
                  onChange={(e) => setCreateForm((f) => ({ ...f, state_id: e.target.value }))}
                >
                  <option value="1">Andhra Pradesh</option>
                  <option value="2">Telangana</option>
                </select>
              </label>
              <label className="field">
                <span className="label">Notes (optional)</span>
                <textarea
                  className="input"
                  rows={2}
                  value={createForm.notes}
                  onChange={(e) => setCreateForm((f) => ({ ...f, notes: e.target.value }))}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn" onClick={close} disabled={saving}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? "Creating…" : "Create videographer"}
              </button>
            </div>
          </form>
        )}
      </Modal>

      <Modal
        open={editing === "add" || editing === "edit"}
        onClose={close}
        title={editing === "edit" ? "Edit videographer details" : "Add videographer details"}
        className="modal-form"
      >
        <form onSubmit={saveDetails}>
          <div className="modal-body vg-form">
            {err && <p className="alert alert-error">{err}</p>}
            <label className="field">
              <span className="label">Videographer</span>
              <select
                className="select"
                value={form.user_id}
                onChange={(e) => onSelectUser(e.target.value)}
                disabled={editing === "edit"}
                required
              >
                <option value="">Select videographer…</option>
                {rows.map((r) => (
                  <option key={r.id} value={r.id}>
                    {(r.display_name || r.full_name || r.username)}
                    {r.has_details ? " (has details)" : ""}
                    {" — @"}
                    {r.username}
                  </option>
                ))}
              </select>
            </label>
            {selected && (
              <p className="muted vg-selected-hint">
                Account: @{selected.username}
                {selected.full_name ? ` · ${selected.full_name}` : ""}
              </p>
            )}
            <label className="field">
              <span className="label">Display name</span>
              <input
                className="input"
                value={form.display_name}
                onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                placeholder="Videographer name"
              />
            </label>
            <label className="field">
              <span className="label">Mobile</span>
              <input
                className="input"
                value={form.mobile}
                onChange={(e) => setForm((f) => ({ ...f, mobile: e.target.value }))}
                placeholder="10-digit mobile"
                required
              />
            </label>
            <label className="field">
              <span className="label">Village</span>
              <input
                className="input"
                value={form.village}
                onChange={(e) => setForm((f) => ({ ...f, village: e.target.value }))}
                placeholder="Village / locality"
                required
              />
            </label>
            <label className="field">
              <span className="label">Notes (optional)</span>
              <textarea
                className="input"
                rows={3}
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              />
            </label>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn" onClick={close} disabled={saving}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Saving…" : "Save details"}
            </button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
