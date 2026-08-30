import { useCallback, useEffect, useRef, useState } from "react";
import { usePageTitle } from "../../hooks/usePageTitle";
import Loader from "../../components/Loader";
import { capturePhotoFromVideo, openBackCamera, stopMediaStream } from "../../reporter/camera";
import { reporterApi } from "../../reporter/api";
import { buildCitizenFrameMeta, citizenFrameMetaFile } from "../../reporter/mediaMeta";

function formatCoord(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toFixed(6);
}

const UPLOAD_ACCEPT = "image/jpeg,image/jpg,image/png,.jpg,.jpeg,.png";

export default function FileComplaint() {
  usePageTitle("File a complaint");
  const [categories, setCategories] = useState([]);
  const [defectType, setDefectType] = useState("");
  const [description, setDescription] = useState("");
  const [gps, setGps] = useState(null);
  const [captureGps, setCaptureGps] = useState(null);
  const [gpsErr, setGpsErr] = useState("");
  const [mediaFile, setMediaFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [cameraErr, setCameraErr] = useState("");
  const watchIdRef = useRef(null);
  const fileInputRef = useRef(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  useEffect(() => {
    reporterApi.categories().then((r) => setCategories(r.categories || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!navigator.geolocation) {
      setGpsErr("Location is not supported on this device.");
      return undefined;
    }
    watchIdRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        setGps({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
          capturedAt: new Date().toISOString(),
        });
        setGpsErr("");
      },
      (err) => setGpsErr(err.message || "Could not get GPS location."),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
    );
    return () => {
      if (watchIdRef.current != null) navigator.geolocation.clearWatch(watchIdRef.current);
    };
  }, []);

  const stopCamera = useCallback(() => {
    stopMediaStream(streamRef.current);
    streamRef.current = null;
    setCameraOpen(false);
    setCameraErr("");
  }, []);

  const startCamera = useCallback(async () => {
    setCameraErr("");
    setCameraBusy(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Camera API not available in this browser.");
      }
      if (!gps) {
        throw new Error("Waiting for GPS — enable location first.");
      }
      const stream = await openBackCamera();
      streamRef.current = stream;
      setCameraOpen(true);
    } catch (e) {
      setCameraErr(e.message || String(e));
      stopCamera();
    } finally {
      setCameraBusy(false);
    }
  }, [gps, stopCamera]);

  useEffect(() => {
    if (!cameraOpen || !videoRef.current || !streamRef.current) return undefined;
    const video = videoRef.current;
    video.setAttribute("playsinline", "true");
    video.setAttribute("webkit-playsinline", "true");
    video.muted = true;
    video.srcObject = streamRef.current;
    video.play().catch(() => {});
    return undefined;
  }, [cameraOpen]);

  useEffect(() => () => stopCamera(), [stopCamera]);

  const applyMedia = useCallback(
    (file, atGps) => {
      if (!atGps) {
        setError("GPS location is required.");
        return;
      }
      setError("");
      setSuccess(null);
      setCaptureGps(atGps);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setMediaFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    },
    [previewUrl]
  );

  const clearMedia = useCallback(() => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setMediaFile(null);
    setCaptureGps(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [previewUrl]);

  const onPickUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!gps) {
      setError("Waiting for GPS — enable location and try again.");
      clearMedia();
      return;
    }
    applyMedia(file, gps);
  };

  const takePhoto = async () => {
    if (!gps) {
      setCameraErr("GPS not ready.");
      return;
    }
    setCameraBusy(true);
    try {
      const file = await capturePhotoFromVideo(videoRef.current);
      applyMedia(file, { ...gps });
      stopCamera();
    } catch (e) {
      setCameraErr(e.message || String(e));
    } finally {
      setCameraBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setSuccess(null);
    const loc = captureGps || gps;
    if (!defectType) {
      setError("Select a complaint category.");
      return;
    }
    if (!loc) {
      setError("GPS location is required. Enable location services.");
      return;
    }
    if (!mediaFile) {
      setError("Capture or upload a photo of the issue.");
      return;
    }

    const form = new FormData();
    form.append("defect_type", defectType);
    form.append("description", description);
    form.append("latitude", String(loc.latitude));
    form.append("longitude", String(loc.longitude));
    if (loc.accuracy != null) form.append("gps_accuracy_m", String(loc.accuracy));
    if (loc.capturedAt) form.append("captured_at", String(loc.capturedAt));
    form.append("media", mediaFile);
    const meta = buildCitizenFrameMeta({
      latitude: loc.latitude,
      longitude: loc.longitude,
      accuracy: loc.accuracy,
      defectType,
      description,
      capturedAt: loc.capturedAt,
      isVideo: Boolean(mediaFile.type?.startsWith("video/")),
    });
    form.append("frame_meta", citizenFrameMetaFile(meta));

    setBusy(true);
    try {
      const out = await reporterApi.submitComplaint(form);
      setSuccess(out);
      setDefectType("");
      setDescription("");
      clearMedia();
    } catch (err) {
      setError(err.message || "Submission failed");
    } finally {
      setBusy(false);
    }
  };

  const isVideo = mediaFile?.type?.startsWith("video/");
  const displayGps = captureGps || gps;

  return (
    <div className="reporter-page">
      <div className="reporter-hero">
        <h2>File a complaint</h2>
        <p>Capture the road issue with your camera or upload a photo. GPS is attached to the report.</p>
      </div>

      <form className="reporter-form card" onSubmit={submit}>
        {error && <div className="alert alert-error">{error}</div>}
        {success && (
          <div className="alert alert-ok">
            Complaint submitted. Tracking ID: <strong>{success.tracking_number}</strong>
          </div>
        )}

        <div className="reporter-gps-strip">
          <span className={`reporter-gps-dot ${gps ? "ok" : "warn"}`} />
          {gps ? (
            <span>
              GPS {formatCoord(gps.latitude)}, {formatCoord(gps.longitude)}
              {gps.accuracy != null ? ` (±${Math.round(gps.accuracy)} m)` : ""}
            </span>
          ) : (
            <span>{gpsErr || "Acquiring GPS…"}</span>
          )}
        </div>

        <div className="form-group">
          <label className="label">Complaint category</label>
          <select
            className="input"
            value={defectType}
            onChange={(e) => setDefectType(e.target.value)}
            disabled={busy}
          >
            <option value="">Select category…</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label className="label">Description (optional)</label>
          <textarea
            className="input"
            rows={3}
            placeholder="Nearby landmark, severity, lane blocked, etc."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
          />
        </div>

        <div className="form-group">
          <label className="label">Photo</label>
          <div className="reporter-capture-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={startCamera}
              disabled={busy || cameraBusy || !gps || cameraOpen}
            >
              Capture
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || !gps}
            >
              Upload
            </button>
            {mediaFile && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={clearMedia} disabled={busy}>
                Remove
              </button>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={UPLOAD_ACCEPT}
            className="sr-only"
            onChange={onPickUpload}
          />

          {cameraOpen && (
            <div className="reporter-camera-panel">
              <video ref={videoRef} className="reporter-camera-video" playsInline muted />
              <div className="reporter-camera-actions">
                <button type="button" className="btn btn-primary" onClick={takePhoto} disabled={cameraBusy}>
                  Take photo
                </button>
                <button type="button" className="btn" onClick={stopCamera} disabled={cameraBusy}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {cameraErr && <p className="reporter-camera-err">{cameraErr}</p>}

          {previewUrl && (
            <div className="reporter-preview">
              {isVideo ? (
                <video src={previewUrl} controls playsInline className="reporter-preview-media" />
              ) : (
                <img src={previewUrl} alt="Captured issue" className="reporter-preview-media" />
              )}
              <p className="reporter-preview-meta">
                Location at capture: {formatCoord(displayGps?.latitude)}, {formatCoord(displayGps?.longitude)}
              </p>
            </div>
          )}
        </div>

        <button
          type="submit"
          className="btn btn-primary btn-block"
          disabled={busy || !gps || !mediaFile || !defectType}
        >
          {busy ? "Submitting…" : "Submit complaint"}
        </button>
        {busy && <Loader label="Uploading" />}
      </form>
    </div>
  );
}
