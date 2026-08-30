import { Navigate, Route, Routes } from "react-router-dom";
import Loader from "../components/Loader";
import ReporterLogin from "../pages/reporter/ReporterLogin";
import ReporterLayout from "../pages/reporter/ReporterLayout";
import FileComplaint from "../pages/reporter/FileComplaint";
import TrackComplaint from "../pages/reporter/TrackComplaint";
import { ReporterAuthProvider, useReporterAuth } from "./ReporterAuth";
import "../styles/reporter.css";

const LOGIN_PATH = "/filecomplaint/login";

function ReporterProtected({ children }) {
  const { reporter, loading } = useReporterAuth();
  if (loading) return <Loader fullScreen label="Citizen portal" />;
  if (!reporter) return <Navigate to={LOGIN_PATH} replace />;
  return children;
}

function FileComplaintRoutes() {
  return (
    <Routes>
      <Route path="login" element={<ReporterLogin />} />
      <Route
        element={
          <ReporterProtected>
            <ReporterLayout />
          </ReporterProtected>
        }
      >
        <Route index element={<FileComplaint />} />
      </Route>
      <Route path="*" element={<Navigate to={LOGIN_PATH} replace />} />
    </Routes>
  );
}

function TrackComplaintRoutes() {
  return (
    <Routes>
      <Route
        element={
          <ReporterProtected>
            <ReporterLayout />
          </ReporterProtected>
        }
      >
        <Route index element={<TrackComplaint />} />
      </Route>
      <Route path="*" element={<Navigate to={LOGIN_PATH} replace />} />
    </Routes>
  );
}

/** Mounted at /filecomplaint/* or /trackcomplaint/* — use relative child routes. */
export default function ReporterApp({ section = "file" }) {
  return (
    <ReporterAuthProvider>
      {section === "track" ? <TrackComplaintRoutes /> : <FileComplaintRoutes />}
    </ReporterAuthProvider>
  );
}
