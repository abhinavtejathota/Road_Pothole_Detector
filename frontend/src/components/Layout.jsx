/**
 * Authenticated app shell: navy sidebar (collapsible, off-canvas) + main content area.
 */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "../App";
import { usePageTitle } from "../hooks/usePageTitle";
import "./Layout.css";
const NAV_MAIN = [
  { to: "/", label: "Dashboard", icon: "bi-speedometer2" },
  { to: "/survey/admin", label: "Survey admin", icon: "bi-geo-alt", roles: ["is_admin", "is_dev_admin"] },
  { to: "/tracking", label: "Tracking", icon: "bi-geo", roles: ["is_admin", "is_dev_admin"] },
  { to: "/videographers", label: "Videographers", icon: "bi-person-badge", roles: ["is_admin", "is_dev_admin"] },
  { to: "/reports", label: "Reports", icon: "bi-file-earmark-text", roles: ["is_admin", "is_dev_admin"] },
  { to: "/survey", label: "Survey", icon: "bi-geo-alt", roles: ["is_videographer"] },
  { to: "/complaints", label: "Complaints", icon: "bi-chat-left-text", roles: ["is_dev_admin"] },
  { to: "/vendors", label: "Vendors", icon: "bi-building", staff: true },
  { to: "/tasks", label: "Tasks", icon: "bi-clipboard-check", staff: true },
  { to: "/review", label: "Review", icon: "bi-camera-video", roles: ["is_supervisor"] },
  { to: "/users", label: "Users", icon: "bi-people", roles: ["is_dev_admin"] },
];

/** DevAdmin tools — pinned bottom of sidebar */
const NAV_BOTTOM_ADMIN = [
  { to: "/detection", label: "Detection", icon: "bi-bullseye", roles: ["is_dev_admin"] },
  { to: "/model-bench", label: "Model testing", icon: "bi-cpu", roles: ["is_dev_admin"] },
];

/** Field tools — pinned bottom for videographers */
const NAV_BOTTOM_FIELD = [
  { to: "/capture", label: "Capture", icon: "bi-camera-video", roles: ["is_videographer"] },
  { to: "/upload", label: "Upload", icon: "bi-cloud-upload", roles: ["is_videographer"] },
];

function NavItem({ item, index, onNavigate }) {
  return (
    <motion.div
      key={item.to}
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.05 * index, duration: 0.35 }}
    >
      <NavLink
        to={item.to}
        end={item.to === "/"}
        onClick={onNavigate}
        className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
      >
        <i className={`bi ${item.icon} nav-link-icon`} aria-hidden />
        {item.label}
      </NavLink>
    </motion.div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  usePageTitle();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = () => setSidebarOpen(false);

  // Close on route change and on Escape, so the drawer never gets left open
  // over a page the user has already navigated away from.
  useEffect(() => { setSidebarOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e) => { if (e.key === "Escape") setSidebarOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  const isVideographerOnly = Boolean(user?.is_videographer && !user?.is_admin && !user?.is_dev_admin);
  const isFieldAdmin = Boolean(user?.is_admin && !user?.is_dev_admin);

  const FIELD_ADMIN_PATHS = ["/", "/tracking", "/survey/admin", "/reports", "/videographers"];

  const show = (item) => {
    if (isFieldAdmin) {
      return FIELD_ADMIN_PATHS.includes(item.to);
    }
    if (isVideographerOnly) {
      return item.to === "/" || item.to === "/survey" || item.to === "/upload" || item.to === "/capture";
    }
    if (item.staff) return true;
    if (!item.roles) return true;
    return item.roles.some((r) => user?.[r]);
  };

  const mainNav = NAV_MAIN.filter(show);
  const adminNav = NAV_BOTTOM_ADMIN.filter(show);
  const fieldNav = NAV_BOTTOM_FIELD.filter(show);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  let navIndex = 0;

  return (
    <div className="app-shell">
      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            key="backdrop"
            className="sidebar-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={closeSidebar}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {sidebarOpen && (
          <motion.aside
            key="sidebar"
            className="sidebar"
            initial={{ x: -240, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: -240, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="sidebar-brand-row">
              <NavLink
                to="/"
                end
                className="sidebar-brand"
                onClick={closeSidebar}
                aria-label="Go to dashboard"
              >
                <img src="/logo.png" alt="" className="sidebar-logo" aria-hidden />
                <div className="sidebar-brand-text">
                  <span className="sidebar-brand-title">SmartRoad</span>
                  <span className="sidebar-brand-sub">Operations Portal</span>
                </div>
              </NavLink>
              <button
                type="button"
                className="sidebar-close"
                onClick={closeSidebar}
                aria-label="Close menu"
              >
                <i className="bi bi-x-lg" aria-hidden />
              </button>
            </div>

            <div className="sidebar-scroll">
              <nav className="sidebar-nav" aria-label="Main">
                {mainNav.map((item) => {
                  const el = <NavItem item={item} index={navIndex} onNavigate={closeSidebar} />;
                  navIndex += 1;
                  return el;
                })}
              </nav>

              {(adminNav.length > 0 || fieldNav.length > 0) && (
                <div className="sidebar-bottom">
                  {adminNav.length > 0 && (
                    <nav className="sidebar-nav sidebar-nav-bottom" aria-label="Admin tools">
                      <div className="sidebar-section-label">Admin</div>
                      {adminNav.map((item) => {
                        const el = <NavItem item={item} index={navIndex} onNavigate={closeSidebar} />;
                        navIndex += 1;
                        return el;
                      })}
                    </nav>
                  )}
                  {fieldNav.length > 0 && (
                    <nav className="sidebar-nav sidebar-nav-bottom" aria-label="Field tools">
                      <div className="sidebar-section-label">Field</div>
                      {fieldNav.map((item) => {
                        const el = <NavItem item={item} index={navIndex} onNavigate={closeSidebar} />;
                        navIndex += 1;
                        return el;
                      })}
                    </nav>
                  )}
                </div>
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="menu-toggle"
            onClick={() => setSidebarOpen((v) => !v)}
            aria-label={sidebarOpen ? "Close menu" : "Open menu"}
            aria-expanded={sidebarOpen}
          >
            <i className="bi bi-list" aria-hidden />
          </button>
          <NavLink to="/" end className="topbar-brand" aria-label="Go to dashboard">
            <img src="/logo.png" alt="" className="topbar-logo" aria-hidden />
            <span className="topbar-brand-title">SmartRoad</span>
          </NavLink>
          <span className="topbar-spacer" />
          <span className="user-chip">{user?.full_name || user?.username}</span>
          <span className="role-badge">{user?.role}</span>
          <button type="button" className="btn btn-sm" onClick={handleLogout}>Logout</button>
        </header>
        <main className={`content${isFieldAdmin ? " content-field-admin" : ""}`}>
          {isFieldAdmin && (
            <div className="field-admin-logo-bg" aria-hidden="true" />
          )}
          <AnimatePresence mode="wait">
            <Outlet key={location.pathname} />
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}




