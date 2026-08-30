import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { motion } from "framer-motion";
import Loader from "../../components/Loader";
import { usePageTitle } from "../../hooks/usePageTitle";
import { useReporterAuth } from "../../reporter/ReporterAuth";
import { reporterApi } from "../../reporter/api";
import { formatIndianMobile, isValidIndianMobile, nationalMobileDigits } from "../../reporter/mobile";

export default function ReporterLogin() {
  usePageTitle("Report a road issue");
  const { reporter, loading, loginWithToken } = useReporterAuth();
  const [mobile, setMobile] = useState("");
  const [otp, setOtp] = useState("");
  const [step, setStep] = useState("mobile");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  if (loading) return <Loader fullScreen label="Citizen portal" />;
  if (reporter) return <Navigate to="/filecomplaint" replace />;

  const onMobileChange = (e) => {
    setMobile(nationalMobileDigits(e.target.value));
  };

  const mobileValid = isValidIndianMobile(mobile);
  const displayMobile = formatIndianMobile(mobile);

  const requestOtp = async (e, { resend = false } = {}) => {
    if (e?.preventDefault) e.preventDefault();
    setBusy(true);
    setError("");
    setHint("");
    if (resend) setOtp("");
    try {
      const out = await reporterApi.requestOtp(mobile, { resend });
      setStep("otp");
      let msg = out.message || "OTP ready.";
      if (out.dev_otp) msg = `${msg} (dev OTP: ${out.dev_otp})`;
      setHint(msg);
    } catch (err) {
      setError(err.message || "Could not send OTP");
    } finally {
      setBusy(false);
    }
  };

  const verifyOtp = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const out = await reporterApi.verifyOtp(mobile, otp);
      await loginWithToken(out.access_token);
    } catch (err) {
      setError(err.message || "Invalid OTP");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="reporter-login-page">
      <div className="reporter-login-card">
        <div className="reporter-brand">
          <img src="/logo.png" alt="" className="reporter-brand-icon" />
          <div>
            <h1>Citizen Reporting</h1>
            <p>Sign in with mobile OTP</p>
          </div>
        </div>

        {error && <div className="alert alert-error">{error}</div>}
        {hint && step === "otp" && <div className="alert alert-ok">{hint}</div>}

        {step === "mobile" ? (
          <motion.form onSubmit={requestOtp} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <div className="form-group">
              <label className="label">Mobile number (India +91)</label>
              <div className="reporter-mobile-field">
                <span className="reporter-mobile-prefix" aria-hidden>
                  +91
                </span>
                <input
                  className="input reporter-mobile-input"
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  placeholder="9876543210"
                  value={mobile}
                  onChange={onMobileChange}
                  maxLength={16}
                  disabled={busy}
                  autoFocus
                />
              </div>
              <p className="reporter-mobile-hint">
                10-digit number. First sign-in sends an OTP; the same code works until you request a new one.
              </p>
            </div>
            <button type="submit" className="btn btn-primary btn-block" disabled={busy || !mobileValid}>
              {busy ? "Please wait…" : "Continue"}
            </button>
          </motion.form>
        ) : (
          <motion.form onSubmit={verifyOtp} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <p className="reporter-otp-sent">
              OTP for <strong>{displayMobile}</strong>{" "}
              <button type="button" className="link-btn" onClick={() => setStep("mobile")}>
                Change
              </button>
            </p>
            <div className="form-group">
              <label className="label">OTP</label>
              <input
                className="input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="Enter OTP"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 8))}
                disabled={busy}
                autoFocus
              />
            </div>
            <button type="submit" className="btn btn-primary btn-block" disabled={busy || !otp.trim()}>
              {busy ? "Verifying…" : "Verify & continue"}
            </button>
            <p className="reporter-mobile-hint" style={{ marginTop: "1rem", textAlign: "center" }}>
              <button
                type="button"
                className="link-btn"
                disabled={busy}
                onClick={() => requestOtp(null, { resend: true })}
              >
                Forgot OTP? Send a new code
              </button>
            </p>
          </motion.form>
        )}

        {busy && <Loader label="Please wait" />}
        <p className="reporter-staff-link">
          Staff login? <Link to="/login">Operations portal</Link>
        </p>
      </div>
    </div>
  );
}
