import { NavLink, Outlet } from "react-router-dom";
import { useReporterAuth } from "../../reporter/ReporterAuth";

export default function ReporterLayout() {
  const { reporter, logout } = useReporterAuth();

  return (
    <div className="reporter-shell">
      <header className="reporter-header">
        <div className="reporter-header-inner">
          <NavLink to="/filecomplaint" className="reporter-header-brand">
            <img src="/logo.png" alt="" />
            <span>Citizen Reporting</span>
          </NavLink>
          <div className="reporter-header-actions">
            {reporter?.mobile_masked && (
              <span className="reporter-mobile">{reporter.mobile_masked}</span>
            )}
            <button type="button" className="btn btn-ghost btn-sm reporter-signout" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <div className="reporter-body">
        <aside className="reporter-sidebar">
          <nav className="reporter-sidebar-nav">
            <NavLink
              to="/filecomplaint"
              className={({ isActive }) => `reporter-nav-link${isActive ? " active" : ""}`}
              end
            >
              File complaint
            </NavLink>
            <NavLink
              to="/trackcomplaint"
              className={({ isActive }) => `reporter-nav-link${isActive ? " active" : ""}`}
            >
              Track complaint
            </NavLink>
          </nav>
        </aside>
        <main className="reporter-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
