import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getReporterToken, reporterApi, setReporterToken } from "./api";

const ReporterAuthContext = createContext(null);

export function useReporterAuth() {
  return useContext(ReporterAuthContext);
}

export function ReporterAuthProvider({ children }) {
  const [reporter, setReporter] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    const tok = getReporterToken();
    if (!tok) {
      setReporter(null);
      setLoading(false);
      return;
    }
    try {
      const u = await reporterApi.me();
      setReporter(u);
    } catch {
      setReporterToken(null);
      setReporter(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loginWithToken = useCallback(
    async (token) => {
      setReporterToken(token);
      setLoading(true);
      await refresh();
    },
    [refresh]
  );

  const logout = useCallback(async () => {
    await reporterApi.logout();
    setReporter(null);
    navigate("/filecomplaint/login", { replace: true });
  }, [navigate]);

  return (
    <ReporterAuthContext.Provider value={{ reporter, loading, loginWithToken, logout, refresh }}>
      {children}
    </ReporterAuthContext.Provider>
  );
}
