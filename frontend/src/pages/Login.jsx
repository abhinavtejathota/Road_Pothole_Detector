import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../App";
import { usePageTitle } from "../hooks/usePageTitle";
import Loader from "../components/Loader";
import EyeIcon from "../components/EyeIcon";
import "./Login.css";
export default function Login() {
  usePageTitle();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-visual">
        <img className="login-bg-image" src="/login-bg.png" alt="" aria-hidden />
        <div className="login-bg-scrim" />
      </div>

      <div className="login-form-panel">
        <div className="login-form-stack">
          <motion.div
            className="login-form-brand"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="login-form-brand-row">
              <img src="/logo.png" alt="" className="login-form-brand-icon" aria-hidden />
              <div className="login-form-brand-text">
                <h1 className="login-form-brand-title">SmartRoad</h1>
                <p className="login-form-brand-subtitle">Road Maintenance Management Platform</p>
              </div>
            </div>
            <p className="login-form-brand-tagline">
              AI-detected potholes, tracked from first scan to verified repair.
            </p>
          </motion.div>

          <motion.form
            className="login-card"
            onSubmit={submit}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="login-brand">
              <h2>Welcome back</h2>
              <p>Sign in to the operations portal</p>
            </div>

            {error && (
              <motion.div className="alert alert-error" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
                {error}
              </motion.div>
            )}

            <div className="form-group">
              <label className="label">Username</label>
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus disabled={busy} />
            </div>

            <div className="form-group">
              <label className="label">Password</label>
              <div className="password-field">
                <input
                  className="input"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="password-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  <EyeIcon open={showPassword} />
                </button>
              </div>
            </div>

            <div className="login-actions">
              <button type="submit" className="btn btn-primary btn-login" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </div>
            {busy && <Loader label="Authenticating" />}
            <p className="login-reporter-link">
              Citizen Reporting? <Link to="/filecomplaint/login">File a complaint</Link>
            </p>
            <p className="login-footer">SmartRoad Application · All Rights Belongs To Dakavara</p>
          </motion.form>
        </div>
      </div>
    </div>
  );
}
