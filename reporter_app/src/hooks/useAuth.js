/**
 * useAuth — centralised authentication state for the reporter app.
 *
 * Features:
 *  - Boots from persisted JWT on app start.
 *  - Verifies the token by calling /api/reporter/auth/me.
 *  - Exposes `reporter` (null when logged-out), `booting`, `login`, `logout`.
 *  - On 401 from any screen the token is wiped automatically (handled in api.js);
 *    this hook re-checks the token if `externalAuthLost` is set to true from outside.
 */
import { useCallback, useEffect, useState } from 'react';
import { api, getToken, setToken } from '../api';

export function useAuth() {
  const [reporter, setReporter] = useState(null);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState('');

  /** Called once on mount to restore a saved session. */
  const boot = useCallback(async () => {
    setBooting(true);
    setError('');
    try {
      const tok = await getToken();
      if (!tok) {
        setReporter(null);
        return;
      }
      const me = await api.me();
      setReporter(me);
    } catch (e) {
      await setToken('');
      setReporter(null);
      if (e.status !== 401) {
        setError(e.message || 'Session restore failed');
      }
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  /**
   * Called after OTP verification completes.
   * @param {object} authPayload  The object returned by api.verifyOtp.
   */
  const login = useCallback(async (authPayload) => {
    setError('');
    try {
      await setToken(authPayload.access_token);
      const me = await api.me();
      setReporter(me);
    } catch (e) {
      setError(e.message || 'Login failed');
      await setToken('');
      setReporter(null);
    }
  }, []);

  const logout = useCallback(async () => {
    setError('');
    try {
      await api.logout();
    } catch {
      /* server-side logout failure is non-fatal */
    }
    await setToken('');
    setReporter(null);
  }, []);

  /**
   * Silently re-fetch /me to refresh reporter details (e.g. after profile update).
   */
  const refreshMe = useCallback(async () => {
    setError('');
    try {
      const me = await api.me();
      setReporter(me);
    } catch (e) {
      if (e.status === 401) {
        await setToken('');
        setReporter(null);
      } else {
        setError(e.message || 'Refresh failed');
      }
    }
  }, []);

  return {
    reporter,
    booting,
    error,
    login,
    logout,
    refreshMe,
    boot,
  };
}
