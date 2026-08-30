import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import { MotionCard } from "../components/PageTransition";
import Loader from "../components/Loader";
import "./ModelBench.css";

function StatusLine({ status }) {
  if (!status?.message) return null;
  const kind = status.kind === "error" ? "text-danger" : "text-success";
  return <p className={`det-status-line ${kind}`}>{status.message}</p>;
}

export default function ModelBench() {
  const { user } = useAuth();
  const [tab, setTab] = useState("camera");
  const [modelId, setModelId] = useState("");
  const [confidence, setConfidence] = useState(0.35);
  const [modelDesc, setModelDesc] = useState("");

  const { data: catalog, loading } = useAsync(() => api.modelBenchModels(), []);

  useEffect(() => {
    if (catalog?.default_id && !modelId) setModelId(catalog.default_id);
  }, [catalog, modelId]);

  useEffect(() => {
    const m = catalog?.models?.find((x) => x.id === modelId);
    if (m) {
      setModelDesc(`${m.description}${m.notes ? ` ${m.notes}` : ""}`);
    }
  }, [catalog, modelId]);

  const [cameraOn, setCameraOn] = useState(false);
  const [camStatus, setCamStatus] = useState(null);
  const [camOutput, setCamOutput] = useState(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const loopRef = useRef(null);
  const frameInFlightRef = useRef(false);

  const stopCamera = useCallback(() => {
    setCameraOn(false);
    if (loopRef.current) clearInterval(loopRef.current);
    loopRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setCamStatus({ kind: "ok", message: "Camera stopped." });
  }, []);

  const openBackCamera = useCallback(async () => {
    const attempts = [
      { video: { facingMode: { exact: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
      { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
      { video: { facingMode: "environment" }, audio: false },
    ];
    let lastErr = null;
    for (const constraints of attempts) {
      try {
        return await navigator.mediaDevices.getUserMedia(constraints);
      } catch (e) {
        lastErr = e;
      }
    }
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      probe.getTracks().forEach((t) => t.stop());
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cams = devices.filter((d) => d.kind === "videoinput");
      const back =
        cams.find((d) => /back|rear|environment|world/i.test(d.label || "")) ||
        cams.find((d) => !/front|user|face|selfie/i.test(d.label || "")) ||
        cams[cams.length - 1];
      if (back?.deviceId) {
        return await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: back.deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
      }
    } catch (e) {
      lastErr = e;
    }
    throw lastErr || new Error("Could not open the back camera.");
  }, []);

  const startCamera = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Camera API not available in this browser.");
      }
      const stream = await openBackCamera();
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.setAttribute("playsinline", "true");
        videoRef.current.setAttribute("webkit-playsinline", "true");
        videoRef.current.muted = true;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setCameraOn(true);
      setCamStatus({
        kind: "ok",
        message: "Back camera on — live pothole boxes update on the right.",
      });
    } catch (e) {
      setCamStatus({ kind: "error", message: e.message || String(e) });
    }
  }, [openBackCamera]);

  useEffect(() => {
    if (!cameraOn || !modelId) return undefined;

    const tick = async () => {
      if (frameInFlightRef.current) return;
      const video = videoRef.current;
      if (!video || video.readyState < 2) return;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      if (!canvas.width || !canvas.height) return;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0);
      const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.82));
      if (!blob) return;
      const fd = new FormData();
      fd.append("frame", blob, "frame.jpg");
      fd.append("model_id", modelId);
      fd.append("confidence", String(confidence));
      fd.append("active", "true");
      frameInFlightRef.current = true;
      try {
        const res = await api.modelBenchRunFrame(fd);
        setCamStatus(res.status);
        if (res.image_data_url) setCamOutput(res.image_data_url);
      } catch (e) {
        setCamStatus({ kind: "error", message: e.message });
      } finally {
        frameInFlightRef.current = false;
      }
    };

    loopRef.current = setInterval(tick, 250);
    return () => clearInterval(loopRef.current);
  }, [cameraOn, modelId, confidence]);

  useEffect(() => () => stopCamera(), [stopCamera]);

  const [imageFile, setImageFile] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [imageOut, setImageOut] = useState(null);
  const [imageStatus, setImageStatus] = useState(null);
  const [imageRunning, setImageRunning] = useState(false);

  const [videoFile, setVideoFile] = useState(null);
  const [videoPreview, setVideoPreview] = useState(null);
  const [videoToken, setVideoToken] = useState(null);
  const [videoOutUrl, setVideoOutUrl] = useState(null);
  const [videoStatus, setVideoStatus] = useState(null);
  const [videoRunning, setVideoRunning] = useState(false);
  const videoTokenRef = useRef(null);

  const discardBenchVideo = useCallback(async (token) => {
    const t = token ?? videoTokenRef.current;
    if (videoOutUrl) {
      try { URL.revokeObjectURL(videoOutUrl); } catch { /* ignore */ }
    }
    setVideoOutUrl(null);
    setVideoToken(null);
    videoTokenRef.current = null;
    if (t) {
      try { await api.modelBenchDiscardVideo(t); } catch { /* ignore */ }
    }
  }, [videoOutUrl]);

  // Discard ephemeral video when leaving the page
  useEffect(() => () => {
    const t = videoTokenRef.current;
    if (t) {
      try { api.modelBenchDiscardVideo(t); } catch { /* ignore */ }
    }
  }, []);

  const onImage = (e) => {
    const f = e.target.files?.[0];
    setImageFile(f || null);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImagePreview(f ? URL.createObjectURL(f) : null);
    setImageOut(null);
  };

  const onVideo = async (e) => {
    const f = e.target.files?.[0];
    await discardBenchVideo();
    setVideoFile(f || null);
    if (videoPreview) URL.revokeObjectURL(videoPreview);
    setVideoPreview(f ? URL.createObjectURL(f) : null);
    setVideoStatus(null);
  };

  const runImage = async () => {
    if (!imageFile) return setImageStatus({ kind: "error", message: "Upload an image first." });
    setImageRunning(true);
    try {
      const fd = new FormData();
      fd.append("image", imageFile);
      fd.append("model_id", modelId);
      fd.append("confidence", String(confidence));
      const res = await api.modelBenchRunImage(fd);
      setImageStatus(res.status);
      setImageOut(res.image_data_url || null);
    } catch (e) {
      setImageStatus({ kind: "error", message: e.message });
    } finally {
      setImageRunning(false);
    }
  };

  const runVideo = async () => {
    if (!videoFile) return setVideoStatus({ kind: "error", message: "Upload a video first." });
    setVideoRunning(true);
    setVideoStatus({ kind: "ok", message: "Processing video… please wait." });
    try {
      await discardBenchVideo();
      const fd = new FormData();
      fd.append("video", videoFile);
      fd.append("model_id", modelId);
      fd.append("confidence", String(confidence));
      const res = await api.modelBenchRunVideo(fd);
      setVideoStatus(res.status);
      if (res.token) {
        setVideoToken(res.token);
        videoTokenRef.current = res.token;
        // Authenticated preview via same-origin cookie session
        setVideoOutUrl(`/api/model-bench/video/${res.token}`);
      }
    } catch (e) {
      setVideoStatus({ kind: "error", message: e.message });
    } finally {
      setVideoRunning(false);
    }
  };

  const downloadVideo = async () => {
    if (!videoToken) return;
    setVideoStatus({ kind: "ok", message: "Preparing download…" });
    try {
      const res = await fetch(`/api/model-bench/video/${videoToken}?download=1`, {
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error(res.status === 404
          ? "Annotated video expired or was discarded — run detection again."
          : `Download failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `annotated_${videoToken.slice(0, 8)}.mp4`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2500);
      setVideoStatus({ kind: "ok", message: "Download started." });
    } catch (e) {
      setVideoStatus({ kind: "error", message: e.message || "Download failed" });
    }
  };

  const downloadImage = () => {
    if (!imageOut) return;
    const a = document.createElement("a");
    a.href = imageOut;
    a.download = "annotated_bench.jpg";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const busy = imageRunning || videoRunning;

  if (!user?.is_dev_admin) return <Navigate to="/" replace />;

  const models = catalog?.models || [];

  return (
    <PageShell
      title="Model Testing Bench"
      subtitle="Compare pothole detectors — back camera, image, or video"
      loading={loading}
    >
      <div className={`det-page bench-page${busy ? " bench-busy" : ""}`}>
        <MotionCard className="card det-card" delay={0}>
          <div className="card-body">
            <div className="bench-controls">
              <div className="form-group">
                <label className="label" htmlFor="bench-model">Model</label>
                <select
                  id="bench-model"
                  className="select"
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  disabled={busy}
                >
                  {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="label" htmlFor="bench-conf">Confidence threshold</label>
                <input
                  id="bench-conf"
                  type="range"
                  min={0.1}
                  max={0.9}
                  step={0.05}
                  value={confidence}
                  onChange={(e) => setConfidence(Number(e.target.value))}
                  disabled={busy}
                />
                <span className="muted" style={{ fontSize: "0.82rem" }}>{confidence.toFixed(2)}</span>
              </div>
            </div>
            {modelDesc && <p className="det-hint" style={{ marginTop: 0 }}>{modelDesc}</p>}
          </div>
        </MotionCard>

        <div className="det-tabs" role="tablist">
          <button type="button" className={`det-tab${tab === "camera" ? " active" : ""}`} disabled={busy} onClick={() => setTab("camera")}>Live back camera</button>
          <button type="button" className={`det-tab${tab === "image" ? " active" : ""}`} disabled={busy} onClick={() => setTab("image")}>Single image</button>
          <button type="button" className={`det-tab${tab === "video" ? " active" : ""}`} disabled={busy} onClick={() => setTab("video")}>Video</button>
        </div>

        {tab === "camera" && (
          <MotionCard className="card det-card" delay={0.05}>
            <CardHeader title="Live back camera" />
            <div className="card-body">
              <p className="det-hint" style={{ marginTop: 0 }}>
                Uses the phone rear camera. Frames are sent to the selected model ~4×/sec; annotated frames with
                pothole boxes appear under <strong>Annotated output</strong>.
              </p>
              <StatusLine status={camStatus} />
              <div className="bench-cam-actions">
                <button type="button" className="btn btn-primary" onClick={startCamera} disabled={cameraOn || busy}>Start back camera</button>
                <button type="button" className="btn" onClick={stopCamera} disabled={!cameraOn || busy}>Stop camera</button>
              </div>
              <div className="bench-cam-grid">
                <div className="bench-cam-panel">
                  <span className="label">Live input (back camera)</span>
                  <div className="bench-cam-frame">
                    <video ref={videoRef} className="det-media bench-cam-media" muted playsInline autoPlay />
                  </div>
                </div>
                <div className="bench-cam-panel">
                  <span className="label">Annotated output (boxes)</span>
                  <div className="bench-cam-frame">
                    {camOutput ? (
                      <img src={camOutput} alt="Annotated" className="det-media bench-cam-media" />
                    ) : (
                      <div className="det-preview-empty">Start the back camera to see live pothole boxes.</div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </MotionCard>
        )}

        {tab === "image" && (
          <MotionCard className="card det-card" delay={0.05}>
            <CardHeader title="Single image" />
            <div className="card-body">
              <div className="bench-cam-grid">
                <div className="form-group bench-cam-panel">
                  <label className="label" htmlFor="bench-img">Upload image</label>
                  <input id="bench-img" type="file" accept="image/*" onChange={onImage} disabled={busy} />
                  {imagePreview && (
                    <div className="bench-cam-frame" style={{ marginTop: "0.5rem" }}>
                      <img src={imagePreview} alt="Input" className="det-media bench-cam-media" />
                    </div>
                  )}
                </div>
                <div className="bench-cam-panel">
                  <span className="label">Annotated output</span>
                  <div className="bench-cam-frame">
                    {imageOut ? (
                      <img src={imageOut} alt="Output" className="det-media bench-cam-media" />
                    ) : (
                      <div className="det-preview-empty">Run detection to see output.</div>
                    )}
                  </div>
                </div>
              </div>
              <StatusLine status={imageStatus} />
              <div className="bench-cam-actions">
                <button type="button" className="btn btn-primary" onClick={runImage} disabled={busy || !imageFile}>
                  {imageRunning ? "Running…" : "Run detection"}
                </button>
                <button type="button" className="btn" onClick={downloadImage} disabled={busy || !imageOut}>
                  Download annotated image
                </button>
              </div>
            </div>
          </MotionCard>
        )}

        {tab === "video" && (
          <MotionCard className="card det-card" delay={0.05}>
            <CardHeader title="Video (ephemeral)" />
            <div className="card-body">
              <p className="det-hint" style={{ marginTop: 0 }}>
                Upload a clip, run detection, preview the annotated video on the right, then download.
                The file is <strong>not stored</strong> — leaving the page, uploading another video, or discarding clears it.
              </p>
              <div className="bench-cam-grid">
                <div className="form-group bench-cam-panel">
                  <label className="label" htmlFor="bench-vid">Upload video</label>
                  <input
                    id="bench-vid"
                    type="file"
                    accept="video/*"
                    onChange={onVideo}
                    disabled={busy}
                  />
                  {videoPreview && (
                    <div className="bench-cam-frame" style={{ marginTop: "0.5rem" }}>
                      <video src={videoPreview} className="det-media bench-cam-media" controls muted playsInline />
                    </div>
                  )}
                </div>
                <div className="bench-cam-panel">
                  <span className="label">Annotated output</span>
                  <div className="bench-cam-frame">
                    {videoOutUrl ? (
                      <video
                        key={videoToken || "out"}
                        src={videoOutUrl}
                        className="det-media bench-cam-media"
                        controls
                        playsInline
                      />
                    ) : (
                      <div className="det-preview-empty">
                        {videoRunning ? "Processing…" : "Run detection to see annotated video."}
                      </div>
                    )}
                  </div>
                </div>
              </div>
              <StatusLine status={videoStatus} />
              <div className="bench-cam-actions">
                <button type="button" className="btn btn-primary" onClick={runVideo} disabled={busy || !videoFile}>
                  {videoRunning ? "Processing…" : "Run detection"}
                </button>
                <button type="button" className="btn btn-primary" onClick={downloadVideo} disabled={busy || !videoToken}>
                  Download annotated video
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => discardBenchVideo()}
                  disabled={busy || (!videoToken && !videoFile)}
                >
                  Discard
                </button>
              </div>
            </div>
          </MotionCard>
        )}

        {busy && (
          <div className="det-running-overlay">
            <Loader label={videoRunning ? "Annotating video — please wait" : "Running model"} />
          </div>
        )}
      </div>
    </PageShell>
  );
}
