import { useState, useEffect, useCallback, createContext, useContext } from "react";
import { Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import { api } from "./api";
import useIdleLogout from "./hooks/useIdleLogout";
import Layout from "./components/Layout";
import Loader from "./components/Loader";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Vendors from "./pages/Vendors";
import VendorForm from "./pages/VendorForm";
import VendorDetail from "./pages/VendorDetail";
import Tasks from "./pages/Tasks";
import TaskDetail from "./pages/TaskDetail";
import Validate from "./pages/Validate";
import ReviewQueue from "./pages/ReviewQueue";
import Users from "./pages/Users";
import Survey from "./pages/Survey";
import SurveyAdmin from "./pages/SurveyAdmin";
import Tracking from "./pages/Tracking";
import Reports from "./pages/Reports";
import Detection from "./pages/Detection";
import ModelBench from "./pages/ModelBench";
import FieldUpload from "./pages/FieldUpload";
import FieldCapture from "./pages/FieldCapture";
import Complaints from "./pages/Complaints";
import AdminDashboard from "./pages/AdminDashboard";
import Videographers from "./pages/Videographers";
import ReporterApp from "./reporter/ReporterApp";

/** Web idle logout — keep in sync with SESSION_IDLE_TIMEOUT_S (default 1 hour). */
const WEB_IDLE_MS = 60 * 60 * 1000;

const AuthContext = createContext(null);

export function useAuth() {
  return useContext(AuthContext);
}

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <Loader fullScreen label="Authenticating" />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function RoleRoute({ allow, children }) {
  const { user } = useAuth();
  if (!allow(user)) return <Navigate to="/" replace />;
  return children;
}

function StaffRoute({ children }) {
  const { user } = useAuth();
  // Field Admin (field Admin) stays on field Admin surfaces only
  if (user?.is_admin && !user?.is_dev_admin) return <Navigate to="/" replace />;
  if (user?.is_videographer && !user?.is_dev_admin) return <Navigate to="/" replace />;
  return children;
}

function isCitizenPath(pathname) {
  return /^\/(filecomplaint|trackcomplaint|report)(\/|$)/.test(pathname || "");
}

export default function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const citizenPath = isCitizenPath(location.pathname);

  const refresh = async () => {
    try {
      const u = await api.me();
      setUser(u);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  const logout = useCallback(async ({ redirect = true } = {}) => {
    setUser(null); // clear first so idle timer stops and UI leaves the app shell
    try {
      const { clearSegmentCache } = await import("./utils/segmentCache");
      clearSegmentCache();
    } catch {
      /* ignore */
    }
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
    try {
      await api.logout();
    } catch {
      /* session may already be dead */
    }
    if (redirect) navigate("/login", { replace: true });
  }, [navigate]);

  useEffect(() => {
    let cancelled = false;
    // Hard ceiling so a hung /api/auth/me never leaves a blank "SmartRoad" loader forever.
    const failSafe = setTimeout(() => {
      if (!cancelled) {
        setUser(null);
        setLoading(false);
      }
    }, 10000);
    (async () => {
      await refresh();
      if (!cancelled) clearTimeout(failSafe);
    })();
    return () => {
      cancelled = true;
      clearTimeout(failSafe);
    };
  }, []);

  // Server returned 401 (idle / SESSION_EPOCH kill) → force login screen.
  useEffect(() => {
    const onExpired = () => {
      if (isCitizenPath(window.location.pathname)) return;
      setUser(null);
      if (!window.location.pathname.startsWith("/login")) {
        navigate("/login", { replace: true });
      }
    };
    window.addEventListener("smartroad:auth-expired", onExpired);
    return () => window.removeEventListener("smartroad:auth-expired", onExpired);
  }, [navigate]);

  useIdleLogout(() => {
    logout({ redirect: true });
  }, WEB_IDLE_MS, Boolean(user) && !loading && !citizenPath);

  const login = async (username, password) => {
    // Ensure any leftover cookie session is cleared before planting a new one.
    try {
      await api.logout();
    } catch {
      /* not logged in */
    }
    try {
      const { clearSegmentCache } = await import("./utils/segmentCache");
      clearSegmentCache();
    } catch {
      /* ignore */
    }
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
    const u = await api.login(username, password);
    if (u?.username && String(u.username).toLowerCase() !== String(username).trim().toLowerCase()) {
      setUser(null);
      throw new Error(`Logged in as ${u.username} instead of ${username}`);
    }
    setUser(u);
    return u;
  };

  if (loading && !citizenPath) return <Loader fullScreen label="SmartRoad" />;

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      <AnimatePresence mode="wait">
        <Routes location={location} key={location.pathname}>
          <Route path="/login" element={user ? <Navigate to="/" /> : <Login />} />
          <Route path="/filecomplaint/*" element={<ReporterApp section="file" />} />
          <Route path="/trackcomplaint/*" element={<ReporterApp section="track" />} />
          <Route path="/report/*" element={<Navigate to="/filecomplaint/login" replace />} />
          <Route path="/" element={<Protected><Layout /></Protected>}>
            <Route index element={user?.is_admin && !user?.is_dev_admin ? <AdminDashboard /> : <Dashboard />} />
            <Route path="vendors" element={<StaffRoute><Vendors /></StaffRoute>} />
            <Route path="vendors/new" element={<StaffRoute><VendorForm /></StaffRoute>} />
            <Route path="vendors/:id" element={<StaffRoute><VendorDetail /></StaffRoute>} />
            <Route path="vendors/:id/edit" element={<StaffRoute><VendorForm edit /></StaffRoute>} />
            <Route path="tasks" element={<StaffRoute><Tasks /></StaffRoute>} />
            <Route path="tasks/:id" element={<StaffRoute><TaskDetail /></StaffRoute>} />
            <Route path="validate/:woId" element={<StaffRoute><Validate /></StaffRoute>} />
            <Route path="review" element={<RoleRoute allow={(u) => u?.is_supervisor}><ReviewQueue /></RoleRoute>} />
            <Route path="users" element={<RoleRoute allow={(u) => u?.is_dev_admin}><Users /></RoleRoute>} />
            <Route path="survey" element={<RoleRoute allow={(u) => u?.is_videographer}><Survey /></RoleRoute>} />
            <Route path="survey/admin" element={<RoleRoute allow={(u) => u?.is_admin || u?.is_dev_admin}><SurveyAdmin /></RoleRoute>} />
            <Route path="tracking" element={<RoleRoute allow={(u) => u?.is_admin || u?.is_dev_admin}><Tracking /></RoleRoute>} />
            <Route path="videographers" element={<RoleRoute allow={(u) => u?.is_admin || u?.is_dev_admin}><Videographers /></RoleRoute>} />
            <Route path="complaints" element={<RoleRoute allow={(u) => u?.is_dev_admin}><Complaints /></RoleRoute>} />
            <Route path="reports" element={<RoleRoute allow={(u) => u?.is_admin || u?.is_dev_admin}><Reports /></RoleRoute>} />
            <Route path="detection" element={<RoleRoute allow={(u) => u?.is_dev_admin}><Detection /></RoleRoute>} />
            <Route path="model-bench" element={<RoleRoute allow={(u) => u?.is_dev_admin}><ModelBench /></RoleRoute>} />
            <Route path="upload" element={<RoleRoute allow={(u) => u?.is_videographer}><FieldUpload /></RoleRoute>} />
            <Route path="capture" element={<RoleRoute allow={(u) => u?.is_videographer}><FieldCapture /></RoleRoute>} />
          </Route>
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </AnimatePresence>
    </AuthContext.Provider>
  );
}
