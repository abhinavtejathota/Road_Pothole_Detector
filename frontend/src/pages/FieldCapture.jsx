import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import CoveredRibbonModal from "../components/CoveredRibbonModal";
import "./FieldCapture.css";
function StatusBanner({ status }) {
  if (!status?.message) return null;
  const kind = status.kind === "error" ? "alert-error" : status.kind === "warn" ? "alert-warn" : "alert-ok";
  return <div className={`det-banner alert ${kind}`}>{status.message}</div>;
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** idle | recording | paused | ended */
export default function FieldCapture() {
  const { user } = useAuth();
  const videoRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const gpsLogRef = useRef([]);
  const watchIdRef = useRef(null);
  const presenceWatchRef = useRef(null);
  const startedAtRef = useRef(0);
  const pausedAccumMsRef = useRef(0);
  const pauseStartedAtRef = useRef(0);
  const lastFixRef = useRef(null);
  const previewUrlRef = useRef(null);
  const lastPingAtRef = useRef(0);
  const phaseRef = useRef("idle");
  const captureSessionIdRef = useRef(null);

  const [phase, setPhase] = useState("idle"); // idle | recording | paused | ended
  const [gpsOk, setGpsOk] = useState(false);
  const [gpsErr, setGpsErr] = useState("");
  const [liveFix, setLiveFix] = useState(null);
  const [gpsPoints, setGpsPoints] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(null);
  const [result, setResult] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [cameraLive, setCameraLive] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [everPaused, setEverPaused] = useState(false);
  const [ribbonOpen, setRibbonOpen] = useState(false);
  const [ribbonFeatures, setRibbonFeatures] = useState([]);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  const newCaptureSessionId = () => {
    try {
      return crypto.randomUUID();
    } catch {
      return `cap_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    }
  };

  const pushPing = useCallback((fix, isRecording) => {
    if (fix?.lat == null || fix?.lon == null) return;
    const now = Date.now();
    const minGap = isRecording ? 1800 : 8000;
    if (now - lastPingAtRef.current < minGap) return;
    lastPingAtRef.current = now;
    const body = {
      lat: fix.lat,
      lon: fix.lon,
      accuracy: fix.accuracy,
      recording: Boolean(isRecording),
    };
    if (isRecording && captureSessionIdRef.current) {
      body.capture_session_id = captureSessionIdRef.current;
    }
    api.trackingPing(body).catch(() => {});
  }, []);

  const rollbackProvisionalCoverage = useCallback(async () => {
    const sid = captureSessionIdRef.current;
    captureSessionIdRef.current = null;
    if (!sid) return;
    try {
      await api.trackingDiscardSession(sid);
    } catch {
      /* offline — coverage may linger until next discard with connectivity */
    }
  }, []);

  const stopWatch = useCallback(() => {
    if (watchIdRef.current != null && navigator.geolocation) {
      navigator.geolocation.clearWatch(watchIdRef.current);
    }
    watchIdRef.current = null;
  }, []);

  const stopPresenceWatch = useCallback(() => {
    if (presenceWatchRef.current != null && navigator.geolocation) {
      navigator.geolocation.clearWatch(presenceWatchRef.current);
    }
    presenceWatchRef.current = null;
  }, []);

  const ensureGps = useCallback(() => new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Geolocation is not available on this device/browser."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setGpsOk(true);
        setGpsErr("");
        const fix = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
        lastFixRef.current = fix;
        setLiveFix(fix);
        pushPing(fix, false);
        resolve(fix);
      },
      (err) => {
        setGpsOk(false);
        const msg = err?.message || "Location permission denied. Turn on GPS and allow location.";
        setGpsErr(msg);
        reject(new Error(msg));
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 },
    );
  }), [pushPing]);

  useEffect(() => {
    ensureGps().catch(() => {});
    if (navigator.geolocation) {
      presenceWatchRef.current = navigator.geolocation.watchPosition(
        (pos) => {
          const fix = {
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            accuracy: pos.coords.accuracy,
          };
          lastFixRef.current = fix;
          setLiveFix(fix);
          setGpsOk(true);
          setGpsErr("");
          if (phaseRef.current !== "recording") pushPing(fix, false);
        },
        () => {},
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
      );
    }
    return () => {
      stopWatch();
      stopPresenceWatch();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      const fix = lastFixRef.current;
      if (fix?.lat != null && fix?.lon != null) {
        api.trackingPing({
          lat: fix.lat,
          lon: fix.lon,
          accuracy: fix.accuracy,
          recording: false,
        }).catch(() => {});
      }
    };
  }, [ensureGps, stopWatch, stopPresenceWatch, pushPing]);

  const videoElapsedSec = () => {
    if (!startedAtRef.current) return 0;
    const pausedExtra = phaseRef.current === "paused" && pauseStartedAtRef.current
      ? Date.now() - pauseStartedAtRef.current
      : 0;
    const ms = Date.now() - startedAtRef.current - pausedAccumMsRef.current - pausedExtra;
    return Math.max(0, Math.floor(ms / 1000));
  };

  const startGpsWatch = () => {
    stopWatch();
    watchIdRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        if (phaseRef.current !== "recording") return;
        const fix = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
        lastFixRef.current = fix;
        setLiveFix(fix);
        const sec = videoElapsedSec();
        const log = gpsLogRef.current;
        if (!log.length || log[log.length - 1].VideoSecond !== sec) {
          log.push({
            VideoSecond: sec,
            Latitude: fix.lat,
            Longitude: fix.lon,
            AccuracyM: fix.accuracy,
          });
        } else {
          log[log.length - 1] = {
            VideoSecond: sec,
            Latitude: fix.lat,
            Longitude: fix.lon,
            AccuracyM: fix.accuracy,
          };
        }
        setGpsPoints(log.length);
        pushPing(fix, true);
      },
      (err) => {
        setGpsErr(err.message || "GPS tracking lost");
        setGpsOk(false);
      },
      { enableHighAccuracy: true, maximumAge: 500, timeout: 15000 },
    );
  };

  const buildPreviewFromChunks = () => {
    const rec = mediaRecorderRef.current;
    const blob = new Blob(chunksRef.current, { type: rec?.mimeType || "video/webm" });
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    const url = URL.createObjectURL(blob);
    previewUrlRef.current = url;
    setPreviewUrl(url);
    return blob;
  };

  const startRecording = async () => {
    setResult(null);
    setUploadModalOpen(false);
    try {
      await ensureGps();
    } catch (e) {
      setResult({ status: { kind: "error", message: e.message } });
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: true,
      });
    } catch (e) {
      setResult({ status: { kind: "error", message: `Camera error: ${e.message}` } });
      return;
    }

    streamRef.current = stream;
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
      await videoRef.current.play().catch(() => {});
    }
    setCameraLive(true);

    chunksRef.current = [];
    gpsLogRef.current = [];
    setGpsPoints(0);
    startedAtRef.current = Date.now();
    pausedAccumMsRef.current = 0;
    pauseStartedAtRef.current = 0;
    setEverPaused(false);
    captureSessionIdRef.current = newCaptureSessionId();

    const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : MediaRecorder.isTypeSupported("video/webm")
        ? "video/webm"
        : "";
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRecorderRef.current = recorder;
    recorder.ondataavailable = (e) => {
      if (e.data?.size) chunksRef.current.push(e.data);
    };
    recorder.start(1000);
    startGpsWatch();
    setPhase("recording");
  };

  const pauseRecording = () => {
    const rec = mediaRecorderRef.current;
    if (!rec || rec.state !== "recording") return;
    try {
      rec.pause();
    } catch {
      /* some browsers */
    }
    pauseStartedAtRef.current = Date.now();
    setEverPaused(true);
    setPhase("paused");
    if (lastFixRef.current) pushPing(lastFixRef.current, false);
  };

  const resumeRecording = () => {
    const rec = mediaRecorderRef.current;
    if (!rec || rec.state !== "paused") return;
    if (pauseStartedAtRef.current) {
      pausedAccumMsRef.current += Date.now() - pauseStartedAtRef.current;
      pauseStartedAtRef.current = 0;
    }
    try {
      rec.resume();
    } catch {
      /* ignore */
    }
    setPhase("recording");
  };

  const finalizeRecorder = () => new Promise((resolve) => {
    const rec = mediaRecorderRef.current;
    const stream = streamRef.current;
    if (!rec || rec.state === "inactive") {
      stopWatch();
      resolve(buildPreviewFromChunks());
      return;
    }
    rec.onstop = () => {
      stopWatch();
      stream?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setCameraLive(false);
      if (lastFixRef.current) pushPing(lastFixRef.current, false);
      resolve(buildPreviewFromChunks());
    };
    try {
      if (rec.state === "paused") rec.resume();
    } catch { /* ignore */ }
    rec.stop();
  });

  const endRecording = async () => {
    await finalizeRecorder();
    setPhase("ended");
    setUploadModalOpen(true);
  };

  const discardCapture = async () => {
    const rec = mediaRecorderRef.current;
    if (rec && rec.state !== "inactive") {
      try { rec.stop(); } catch { /* ignore */ }
    }
    stopWatch();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraLive(false);
    chunksRef.current = [];
    gpsLogRef.current = [];
    setGpsPoints(0);
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    setPreviewUrl(null);
    mediaRecorderRef.current = null;
    setUploadModalOpen(false);
    setEverPaused(false);
    setPhase("idle");
    setResult(null);
    await rollbackProvisionalCoverage();
  };

  const uploadToServer = async () => {
    if (phase === "recording" || phase === "paused") {
      await finalizeRecorder();
      setPhase("ended");
    }
    if (!chunksRef.current.length && !previewUrlRef.current) {
      setResult({ status: { kind: "error", message: "Nothing to upload yet." } });
      setUploadModalOpen(false);
      return;
    }
    if (!gpsLogRef.current.length) {
      setResult({ status: { kind: "error", message: "No GPS points captured — upload blocked. Enable location and retry." } });
      setUploadModalOpen(false);
      return;
    }

    const blob = new Blob(chunksRef.current, {
      type: mediaRecorderRef.current?.mimeType || "video/webm",
    });
    const base = `capture_${stamp()}`;
    const videoFile = new File([blob], `${base}.webm`, { type: blob.type || "video/webm" });
    const header = "VideoSecond,Latitude,Longitude,AccuracyM\n";
    const body = gpsLogRef.current
      .map((r) => `${r.VideoSecond},${r.Latitude},${r.Longitude},${r.AccuracyM ?? ""}`)
      .join("\n");
    const gpsFile = new File([header + body], `${base}.csv`, { type: "text/csv" });

    setUploading(true);
    setUploadPct(0);
    setUploadModalOpen(false);
    try {
      const fd = new FormData();
      fd.append("media", videoFile);
      fd.append("gps_log", gpsFile);
      // Frame meta JSON — same densified GPS → frame map mobile apps send
      try {
        const frames = gpsLogRef.current.map((r) => ({
          frame: Math.round((r.VideoSecond || 0) * 30),
          t_ms: Math.round((r.VideoSecond || 0) * 1000),
          video_second: Math.round(r.VideoSecond || 0),
          lat: r.Latitude,
          lon: r.Longitude,
          accuracy_m: r.AccuracyM,
        }));
        const metaFile = new File(
          [JSON.stringify({ assumed_fps: 30, frames }, null, 2)],
          `${base}_log.json`,
          { type: "application/json" },
        );
        fd.append("frame_meta", metaFile);
      } catch {
        /* GPS CSV alone is enough for detection */
      }
      if (captureSessionIdRef.current) {
        fd.append("capture_session_id", captureSessionIdRef.current);
      }
      const res = await api.uploadField(fd, {
        onProgress: ({ percent }) => setUploadPct(percent),
      });
      setUploadPct(100);
      setResult(res);
      chunksRef.current = [];
      gpsLogRef.current = [];
      setGpsPoints(0);
      mediaRecorderRef.current = null;
      setEverPaused(false);
      setPhase("idle");
      // Keep coverage — server commits this capture_session_id
      captureSessionIdRef.current = null;
      if ((res?.status?.kind || "ok") !== "error") {
        // Fetch AFTER upload so covered overlays match GPS-sealed segments
        let ribbonFeats = [];
        try {
          const geo = await api.surveyAssignmentGeoJson();
          ribbonFeats = geo?.features || [];
        } catch {
          ribbonFeats = [];
        }
        setRibbonFeatures(ribbonFeats);
        setRibbonOpen(true);
      }
    } catch (ex) {
      setResult({ status: { kind: "error", message: ex.message } });
      setPhase("ended");
    } finally {
      setUploading(false);
      setUploadPct(null);
    }
  };

  const saveLocal = async () => {
    if (phase === "recording" || phase === "paused") {
      await finalizeRecorder();
      setPhase("ended");
    }
    if (!chunksRef.current.length) {
      setResult({ status: { kind: "error", message: "Nothing to save yet." } });
      setUploadModalOpen(false);
      return;
    }
    const blob = new Blob(chunksRef.current, {
      type: mediaRecorderRef.current?.mimeType || "video/webm",
    });
    const base = `capture_${stamp()}`;
    const videoUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = videoUrl;
    a.download = `${base}.webm`;
    a.click();
    URL.revokeObjectURL(videoUrl);

    if (gpsLogRef.current.length) {
      const header = "VideoSecond,Latitude,Longitude,AccuracyM\n";
      const body = gpsLogRef.current
        .map((r) => `${r.VideoSecond},${r.Latitude},${r.Longitude},${r.AccuracyM ?? ""}`)
        .join("\n");
      const gpsBlob = new Blob([header + body], { type: "text/csv" });
      const gpsUrl = URL.createObjectURL(gpsBlob);
      const a2 = document.createElement("a");
      a2.href = gpsUrl;
      a2.download = `${base}.csv`;
      a2.click();
      URL.revokeObjectURL(gpsUrl);
    }
    setUploadModalOpen(false);
    // Save-local is not a commit — drop provisional trail until Upload page re-sends GPS
    await rollbackProvisionalCoverage();
    setResult({
      status: {
        kind: "ok",
        message:
          "Saved video (+ GPS CSV) to your downloads. Provisional coverage was cleared — use Upload with the GPS file to count distance and seal roads.",
      },
    });
  };

  const openUploadModal = () => setUploadModalOpen(true);

  if (!user?.is_videographer) return <Navigate to="/" replace />;

  const showLivePreview = cameraLive || phase === "recording" || phase === "paused";

  return (
    <PageShell
      title="Capture"
      subtitle="Record with GPS — uploads to the same S3 queue as Upload"
    >
      <div className="field-page">
        <MotionCard className="card" delay={0}>
          <CardHeader
            title="Camera + GPS"
            actions={<Link to="/upload" className="btn btn-sm">Upload a file instead</Link>}
          />
          <div className="card-body">
            <p className="det-hint">
              Location <strong>must</strong> stay on. GPS is sent to admin <strong>Tracking</strong> while
              this page is open, and while recording for the upload + Detection.
            </p>

            {!gpsOk && (
              <div className="alert alert-error">
                {gpsErr || "Waiting for location permission…"}
                <div className="field-actions" style={{ marginTop: "0.5rem" }}>
                  <button type="button" className="btn btn-sm" onClick={() => ensureGps().catch(() => {})}>
                    Retry location
                  </button>
                </div>
              </div>
            )}

            {liveFix && (
              <p className="field-meta muted">
                Live GPS: {liveFix.lat.toFixed(6)}, {liveFix.lon.toFixed(6)}
                {liveFix.accuracy != null ? ` (±${Math.round(liveFix.accuracy)} m)` : ""}
                {phase === "recording" || phase === "paused"
                  ? ` · ${gpsPoints} point(s)${phase === "paused" ? " · paused" : ""}`
                  : " · sharing location with Tracking"}
              </p>
            )}

            <div className={`field-preview${showLivePreview ? "" : " field-preview-idle"}`}>
              {(phase === "recording" || phase === "paused") && (
                <div className={`field-rec-badge${phase === "paused" ? " is-paused" : ""}`}>
                  {phase === "paused" ? "PAUSED" : "REC"}
                </div>
              )}
              <video
                ref={videoRef}
                muted
                playsInline
                className="det-media"
                style={{ display: showLivePreview ? "block" : "none" }}
              />
              {!showLivePreview && (
                <p className="field-meta muted" style={{ padding: "0.75rem", textAlign: "center", margin: 0 }}>
                  Camera preview starts when you tap Start
                </p>
              )}
            </div>

            <div className="field-actions">
              {phase === "idle" && (
                <button type="button" className="btn btn-primary" onClick={startRecording} disabled={!gpsOk || uploading}>
                  Start
                </button>
              )}
              {phase === "recording" && (
                <>
                  <button type="button" className="btn btn-danger" onClick={pauseRecording}>
                    Pause / Stop
                  </button>
                  {!everPaused ? (
                    <button type="button" className="btn" onClick={endRecording}>
                      End
                    </button>
                  ) : (
                    <button type="button" className="btn btn-primary" onClick={openUploadModal}>
                      Upload
                    </button>
                  )}
                </>
              )}
              {phase === "paused" && (
                <>
                  <button type="button" className="btn btn-primary" onClick={resumeRecording}>
                    Resume
                  </button>
                  <button type="button" className="btn" onClick={openUploadModal}>
                    Upload
                  </button>
                </>
              )}
              {phase === "ended" && (
                <>
                  <button type="button" className="btn btn-primary" onClick={openUploadModal} disabled={uploading}>
                    Upload
                  </button>
                  <button type="button" className="btn" onClick={discardCapture} disabled={uploading}>
                    Discard
                  </button>
                  <button type="button" className="btn" onClick={startRecording} disabled={!gpsOk || uploading}>
                    Start new
                  </button>
                </>
              )}
              {uploading && <span className="field-meta muted">
                {uploadPct != null
                  ? `Uploading video + GPS… ${uploadPct}% (${Math.max(0, 100 - uploadPct)}% left)`
                  : "Uploading video + GPS…"}
              </span>}
              {uploading && uploadPct != null && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ height: 8, borderRadius: 4, background: "#d7dee8", overflow: "hidden" }}>
                    <div style={{ width: `${uploadPct}%`, height: "100%", background: "#0b2a4a" }} />
                  </div>
                </div>
              )}
            </div>

            {result?.status && <StatusBanner status={result.status} />}
            {result?.keys?.length > 0 && (
              <ul className="field-keys">
                {result.keys.map((k) => <li key={k}><code>{k}</code></li>)}
              </ul>
            )}
          </div>
        </MotionCard>

        {previewUrl && phase !== "recording" && phase !== "paused" && (
          <MotionCard className="card" delay={0.04}>
            <CardHeader title="Last recording" />
            <div className="card-body">
              <div className="field-preview">
                <video src={previewUrl} controls className="det-media" />
              </div>
            </div>
          </MotionCard>
        )}
      </div>

      {uploadModalOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setUploadModalOpen(false)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="capture-upload-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="capture-upload-title" className="modal-title">Upload capture?</h2>
            <div className="modal-body">
              <p className="muted" style={{ marginTop: 0 }}>
                Choose what to do with the current recording.
              </p>
            </div>
            <div className="modal-actions capture-upload-modal-actions">
              <button type="button" className="btn btn-primary" onClick={uploadToServer} disabled={uploading}>
                Upload to server
              </button>
              <button type="button" className="btn" onClick={saveLocal} disabled={uploading}>
                Save
              </button>
              <button type="button" className="btn btn-danger" onClick={discardCapture} disabled={uploading}>
                Discard
              </button>
              <button
                type="button"
                className="capture-upload-dismiss"
                onClick={() => setUploadModalOpen(false)}
                disabled={uploading}
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      <CoveredRibbonModal
        open={ribbonOpen}
        features={ribbonFeatures}
        onClose={() => setRibbonOpen(false)}
        message={
          result?.roads_completed?.message
            || (result?.roads_completed?.count
              ? `Sealed ${result.roads_completed.count}`
                + (result.roads_completed.total_assigned != null
                  ? ` of ${result.roads_completed.total_assigned}`
                  : "")
                + " road segment(s) from GPS. Uncovered assigned roads stay open until you finish them."
              : "No roads sealed from GPS yet — drive the assigned route and upload again.")
        }
      />
    </PageShell>
  );
}
