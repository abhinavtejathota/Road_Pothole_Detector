/** Back-camera helpers (same approach as ModelBench). */

export async function openBackCamera() {
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
}

export function stopMediaStream(stream) {
  stream?.getTracks().forEach((t) => t.stop());
}

export function capturePhotoFromVideo(videoEl, { quality = 0.92 } = {}) {
  if (!videoEl?.videoWidth) {
    throw new Error("Camera not ready — wait for the live preview.");
  }
  const canvas = document.createElement("canvas");
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(videoEl, 0, 0);
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("Could not capture photo."));
          return;
        }
        const file = new File([blob], `capture_${Date.now()}.jpg`, { type: "image/jpeg" });
        resolve(file);
      },
      "image/jpeg",
      quality
    );
  });
}
