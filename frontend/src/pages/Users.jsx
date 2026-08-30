import { useMemo, useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import Modal from "../components/Modal";
import { MotionCard } from "../components/PageTransition";
import StatusBadge from "../components/StatusBadge";
import { STATE_META } from "../data/surveyConstants";

const EMPTY_FORM = {
  username: "",
  password: "",
  full_name: "",
  email: "",
  role: "Allocator",
  vendor_id: "",
  state_id: "1",
};

function stateLabelForUser(u) {
  if (u.state_id === 2) return "Telangana";
  if (u.state_id === 1) return "Andhra Pradesh";
  return "—";
}

function districtIdsForUser(u) {
  if (u?.district_ids?.length) return u.district_ids.map(Number);
  if (u?.district_id != null) return [Number(u.district_id)];
  return [];
}

export default function Users() {
  const { data: users, loading, error, reload } = useAsync(() => api.users(), []);
  const { data: vendors } = useAsync(() => api.vendors({ status: "Active" }), []);
  const { data: apDistricts } = useAsync(() => api.surveyDistricts("andhra"), []);
  const { data: tgDistricts } = useAsync(() => api.surveyDistricts("telangana"), []);
  const [show, setShow] = useState(false);
  const [editUser, setEditUser] = useState(null);
  const [districtUser, setDistrictUser] = useState(null);
  const [districtPage, setDistrictPage] = useState(1);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [editStateId, setEditStateId] = useState("1");
  const [err, setErr] = useState("");
  const [editErr, setEditErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [editSaving, setEditSaving] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(null);

  const districtNameById = useMemo(() => {
    const map = new Map();
    for (const d of [...(apDistricts || []), ...(tgDistricts || [])]) {
      const id = Number(d.district_id);
      if (Number.isFinite(id)) map.set(id, d.name || String(id));
    }
    return map;
  }, [apDistricts, tgDistricts]);

  const close = () => {
    setShow(false);
    setErr("");
  };

  const closeEdit = () => {
    setEditUser(null);
    setEditErr("");
  };

  const setRole = (role) => {
    setForm((f) => ({
      ...f,
      role,
      vendor_id: role === "Vendor" ? f.vendor_id : "",
      state_id: role === "Videographer" ? (f.state_id || "1") : "",
    }));
  };

  const districtSummary = (u) => {
    const ids = districtIdsForUser(u);
    if (!ids.length) return "None";
    return `${ids.length} district${ids.length === 1 ? "" : "s"}`;
  };

  const districtNamesFor = (u) => {
    const ids = districtIdsForUser(u);
    return ids.map((id) => ({
      id,
      name: districtNameById.get(id) || `District ${id}`,
    }));
  };

  const DISTRICT_PAGE_SIZE = 7;
  const districtRows = districtUser ? districtNamesFor(districtUser) : [];
  const districtPageCount = Math.max(1, Math.ceil(districtRows.length / DISTRICT_PAGE_SIZE));
  const districtSafePage = Math.min(districtPage, districtPageCount);
  const districtPageRows = districtRows.slice(
    (districtSafePage - 1) * DISTRICT_PAGE_SIZE,
    districtSafePage * DISTRICT_PAGE_SIZE,
  );

  const openDistricts = (u) => {
    setDistrictUser(u);
    setDistrictPage(1);
  };

  const openEdit = (u) => {
    setEditUser(u);
    setEditStateId(u.state_id != null ? String(u.state_id) : "1");
    setEditErr("");
  };

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      if (form.role === "Videographer" && !form.state_id) {
        setErr("Select a state for the videographer.");
        setSaving(false);
        return;
      }
      await api.createUser({
        ...form,
        vendor_id: form.vendor_id || null,
        state_id: form.role === "Videographer" ? form.state_id : null,
        district_ids: [],
        district_id: null,
      });
      close();
      setForm({ ...EMPTY_FORM });
      reload();
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setSaving(false);
    }
  };

  const submitEdit = async (e) => {
    e.preventDefault();
    if (!editUser) return;
    setEditSaving(true);
    setEditErr("");
    try {
      await api.updateUserState(editUser.id, { state_id: Number(editStateId) });
      closeEdit();
      reload();
    } catch (ex) {
      setEditErr(ex.message);
    } finally {
      setEditSaving(false);
    }
  };

  const removeUser = async (u) => {
    if (!window.confirm(`Delete user ${u.username}? This cannot be undone.`)) return;
    setDeleteBusy(u.id);
    try {
      await api.deleteUser(u.id);
      reload();
    } catch (ex) {
      window.alert(ex.message);
    } finally {
      setDeleteBusy(null);
    }
  };

  return (
    <PageShell
      title="User management"
      titleIcon={<i className="bi bi-people page-title-icon" aria-hidden />}
      loading={loading}
      error={error}
      skeleton="table"
      actions={<button type="button" className="btn btn-primary btn-sm" onClick={() => setShow(true)}>Add user</button>}
    >
      <MotionCard className="card" delay={0.05}>
        <div className="table-wrap table-template">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Username</th><th>Full name</th><th>Email</th>
                <th>Role</th><th>Vendor</th><th>State</th><th>District(s)</th><th>Active</th>
                <th>Edit</th><th>Delete</th>
              </tr>
            </thead>
            <tbody>
              {(users || []).map((u) => (
                <tr key={u.id}>
                  <td>{u.id}</td>
                  <td><strong>{u.username}</strong></td>
                  <td>{u.full_name || "—"}</td>
                  <td>{u.email || "—"}</td>
                  <td><StatusBadge status={u.role} /></td>
                  <td>{u.vendor_id || "—"}</td>
                  <td>{u.role === "Videographer" ? stateLabelForUser(u) : "—"}</td>
                  <td>
                    {u.role === "Videographer" ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => openDistricts(u)}
                      >
                        {districtSummary(u)}
                      </button>
                    ) : "—"}
                  </td>
                  <td><StatusBadge status={u.is_active ? "Active" : "Inactive"} /></td>
                  <td>
                    {u.role === "Videographer" ? (
                      <button type="button" className="btn btn-sm" onClick={() => openEdit(u)}>
                        Edit
                      </button>
                    ) : "—"}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={deleteBusy === u.id}
                      onClick={() => removeUser(u)}
                    >
                      {deleteBusy === u.id ? "…" : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
              {!users?.length && (
                <tr><td colSpan={11} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>No users yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </MotionCard>

      <Modal open={show} onClose={close} title="Add user" className="modal-form">
        <form onSubmit={submit}>
          {err && <div className="alert alert-error">{err}</div>}
          <div className="form-row modal-form-scroll">
            <div className="form-group">
              <label className="label">Username <span className="text-danger">*</span></label>
              <input className="input" required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </div>
            <div className="form-group">
              <label className="label">Password <span className="text-danger">*</span></label>
              <input className="input" type="password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
            <div className="form-group">
              <label className="label">Full name</label>
              <input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div className="form-group">
              <label className="label">Email</label>
              <input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div className="form-group">
              <label className="label">Role <span className="text-danger">*</span></label>
              <select className="select" value={form.role} onChange={(e) => setRole(e.target.value)}>
                {[
                  { value: "DevAdmin", label: "Dev Admin" },
                  { value: "Admin", label: "Admin" },
                  { value: "Allocator", label: "Allocator" },
                  { value: "Supervisor", label: "Supervisor" },
                  { value: "Vendor", label: "Vendor" },
                  { value: "Videographer", label: "Videographer" },
                ].map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>
            {form.role === "Vendor" && (
              <div className="form-group">
                <label className="label">Vendor account <span className="text-danger">*</span></label>
                <select className="select" required value={form.vendor_id} onChange={(e) => setForm({ ...form, vendor_id: e.target.value })}>
                  <option value="">— Select —</option>
                  {(vendors || []).map((v) => <option key={v.id} value={v.id}>{v.company_name}</option>)}
                </select>
              </div>
            )}
            {form.role === "Videographer" && (
              <div className="form-group">
                <label className="label">State <span className="text-danger">*</span></label>
                <select className="select" value={form.state_id} onChange={(e) => setForm({ ...form, state_id: e.target.value })}>
                  <option value="1">{STATE_META.andhra?.name || "Andhra Pradesh"}</option>
                  <option value="2">{STATE_META.telangana?.name || "Telangana"}</option>
                </select>
              </div>
            )}
          </div>
          <div className="modal-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Creating…" : "Create user"}</button>
            <button type="button" className="btn" onClick={close}>Cancel</button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(editUser)}
        onClose={closeEdit}
        title={editUser ? `State — ${editUser.username}` : "State"}
        className="modal-form"
      >
        <form onSubmit={submitEdit}>
          {editErr && <div className="alert alert-error">{editErr}</div>}
          <div className="form-group">
            <label className="label">State</label>
            <select className="select" value={editStateId} onChange={(e) => setEditStateId(e.target.value)}>
              <option value="1">{STATE_META.andhra?.name || "Andhra Pradesh"}</option>
              <option value="2">{STATE_META.telangana?.name || "Telangana"}</option>
            </select>
          </div>
          <div className="modal-actions">
            <button type="submit" className="btn btn-primary" disabled={editSaving}>
              {editSaving ? "Saving…" : "Save state"}
            </button>
            <button type="button" className="btn" onClick={closeEdit}>Cancel</button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(districtUser)}
        onClose={() => { setDistrictUser(null); setDistrictPage(1); }}
        title={districtUser ? `Districts — ${districtUser.username}` : "Districts"}
        className="modal-form"
      >
        {districtUser && (
          <>
            <div className="table-wrap table-template">
              <table>
                <thead>
                  <tr><th>District</th></tr>
                </thead>
                <tbody>
                  {districtPageRows.map((d) => (
                    <tr key={d.id}>
                      <td>{d.name}</td>
                    </tr>
                  ))}
                  {!districtRows.length && (
                    <tr>
                      <td className="muted" style={{ textAlign: "center", padding: "1rem" }}>
                        No districts assigned yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {districtRows.length > DISTRICT_PAGE_SIZE && (
              <div
                className="det-images-pager"
                style={{ marginTop: "0.75rem", justifyContent: "space-between" }}
              >
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={districtSafePage <= 1}
                  onClick={() => setDistrictPage((p) => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <span className="muted" style={{ fontSize: "0.85rem" }}>
                  {districtSafePage} / {districtPageCount}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={districtSafePage >= districtPageCount}
                  onClick={() => setDistrictPage((p) => Math.min(districtPageCount, p + 1))}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="btn"
            onClick={() => { setDistrictUser(null); setDistrictPage(1); }}
          >
            Close
          </button>
        </div>
      </Modal>
    </PageShell>
  );
}
