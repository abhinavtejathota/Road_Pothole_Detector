export default function StatusBadge({ status }) {
  const raw = status || "";
  const display = ({ DevAdmin: "Dev Admin", Admin: "Admin", TrackerAdmin: "Admin" })[raw] || raw;
  const s = raw.toLowerCase().replace(/\s/g, "");
  const cls = {
    created: "badge-created",
    allocated: "badge-allocated",
    wip: "badge-wip",
    completed: "badge-completed",
    verified: "badge-verified",
    failed: "badge-failed",
    pass: "badge-pass",
    fail: "badge-fail",
    partial: "badge-partial",
    active: "badge-pass",
    inactive: "badge-fail",
    suspended: "badge-partial",
    blacklisted: "badge-fail",
    critical: "badge-fail",
    standard: "badge-partial",
    low: "badge-created",
    high: "badge-fail",
    medium: "badge-partial",
    open: "badge-created",
    inprogress: "badge-partial",
    repaired: "badge-pass",
    valid: "badge-pass",
    expiring: "badge-partial",
    admin: "badge-created",
    devadmin: "badge-created",
    trackeradmin: "badge-created",
    allocator: "badge-allocated",
    supervisor: "badge-partial",
    vendor: "badge-created",
    videographer: "badge-created",
  }[s] || "badge-created";
  return <span className={`badge ${cls}`}>{display}</span>;
}
