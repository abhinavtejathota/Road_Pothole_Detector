import { useCallback, useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  Modal,
  ActivityIndicator,
  Alert,
  BackHandler,
  AppState,
} from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import * as Location from 'expo-location';
import * as FileSystem from 'expo-file-system/legacy';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { api, isOnline } from '../api';
import { colors, buttonStyles } from '../theme';
import * as offlineCapture from '../offlineCapture';
import { fieldSiblingNames, routeUploadFields } from '../uploadConfig';

/** Poll interval for the offline/online badge — cheap OS call, no socket involved. */
const NET_POLL_MS = 4000;

/**
 * One continuous video until Upload / Save / Discard.
 * Pause/Resume uses toggleRecordingAsync (same clip); Dismiss only closes the modal.
 */
export default function CaptureScreen({ onBack }) {
  const camRef = useRef(null);
  const [camPerm, requestCamPerm] = useCameraPermissions();
  const [micPerm, requestMicPerm] = useMicrophonePermissions();
  const [locOk, setLocOk] = useState(false);
  const [phase, setPhase] = useState('idle'); // idle | recording | paused | ended
  const [everPaused, setEverPaused] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(null); // 0–100 or null
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [fix, setFix] = useState(null);
  const [online, setOnline] = useState(true);
  const [gpsLog, setGpsLog] = useState([]);
  const [videoUri, setVideoUri] = useState(null);
  const [canTogglePause, setCanTogglePause] = useState(true);
  const recordingRef = useRef(false);
  const startedAtRef = useRef(0);
  const pausedAccumRef = useRef(0);
  const pauseStartedRef = useRef(0);
  const gpsLogRef = useRef([]);
  const watchRef = useRef(null);
  const gpsTickRef = useRef(null);
  const fixRef = useRef(null);
  const videoUriRef = useRef(null);
  const phaseRef = useRef('idle');
  const captureSessionIdRef = useRef(null);
  const uploadingRef = useRef(false);
  const lastPingAtRef = useRef(0);
  const nextChunkIndexRef = useRef(0);
  const chunksUploadedRef = useRef(0);
  const chunkSessionReadyRef = useRef(false);
  /** True when we never got (or lost) a server chunk session — store locally until Upload. */
  const localModeRef = useRef(false);
  /** Set true on Discard so in-flight chunk jobs stop and cannot resurrect the session. */
  const discardedRef = useRef(false);
  const wantRecordRef = useRef(false);
  const chunkUploadChainRef = useRef(Promise.resolve());
  // Chunks that exhausted their upload retries — drained before the next
  // chunk and again once recording stops, so a transient outage self-heals.
  const pendingRetryChunksRef = useRef([]);
  const chunkUploadingRef = useRef(false);
  const CHUNK_RETRY_DELAYS_MS = [2000, 5000, 10000, 20000];
  // True only while a recordAsync() call for the current ~60s segment is in
  // flight (i.e. there is a native recording session pause/resume/stop can
  // target). False in the gap between segments — that gap is exactly where
  // pause()/resume() calling toggleRecordingAsync() crashes on Android with
  // "IllegalStateException: The recording has been stopped".
  const nativeRecordingActiveRef = useRef(false);
  // Serializes native camera lifecycle calls (stop/pause/resume) so a
  // double-tap can't fire two overlapping toggleRecordingAsync() calls.
  const cameraOpChainRef = useRef(Promise.resolve());
  /** Set when OS backgrounds/sleeps the app during capture — clip may die early. */
  const backgroundInterruptRef = useRef(false);
  /** True if interrupt happened while paused (do not treat early URI as a finished minute). */
  const interruptedWhilePausedRef = useRef(false);
  const CHUNK_SECONDS = 60;
  const KEEP_AWAKE_TAG = 'smartroad-capture';

  const runCameraOp = useCallback((fn) => {
    const result = cameraOpChainRef.current.then(fn, fn);
    cameraOpChainRef.current = result.then(() => undefined, () => undefined);
    return result;
  }, []);

  const isRecordingAlreadyStoppedError = (e) => {
    const msg = String(e?.message || e || '');
    return /recording has been stopped/i.test(msg) || /IllegalStateException/i.test(msg);
  };

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    fixRef.current = fix;
  }, [fix]);

  // Keep screen awake while capturing so sleep doesn't kill the camera mid-clip.
  useEffect(() => {
    if (phase === 'recording' || phase === 'paused') {
      activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {});
      return () => {
        deactivateKeepAwake(KEEP_AWAKE_TAG);
      };
    }
    deactivateKeepAwake(KEEP_AWAKE_TAG);
    return undefined;
  }, [phase]);

  // Background / sleep: mark interrupt. If paused, stop the awaiting recordAsync
  // so we get a deterministic early URI that we can discard (not "minute N").
  useEffect(() => {
    const onChange = (next) => {
      if (next === 'active') return;
      const ph = phaseRef.current;
      if (ph !== 'recording' && ph !== 'paused') return;
      backgroundInterruptRef.current = true;
      if (ph === 'paused') {
        interruptedWhilePausedRef.current = true;
        if (nativeRecordingActiveRef.current && camRef.current) {
          runCameraOp(async () => {
            try {
              camRef.current?.stopRecording();
            } catch (_) {
              /* already stopped */
            }
          }).catch(() => {});
        }
      }
    };
    const sub = AppState.addEventListener('change', onChange);
    return () => sub.remove();
  }, [runCameraOp]);

  // Recording itself never needs network (camera + local GPS only) — this badge
  // and the upload gate below just stop the user from expecting a live upload
  // (or GPS-sealing ping) to work while there's no connection.
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const ok = await isOnline();
      if (!cancelled) setOnline(ok);
    };
    check();
    const id = setInterval(check, NET_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const newCaptureSessionId = () =>
    `cap_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

  const stopGpsTick = useCallback(() => {
    if (gpsTickRef.current) {
      clearInterval(gpsTickRef.current);
      gpsTickRef.current = null;
    }
  }, []);

  const pushGpsSample = useCallback((f, sec) => {
    if (!f) return;
    const next = [...gpsLogRef.current];
    const row = {
      VideoSecond: sec,
      Latitude: f.lat,
      Longitude: f.lon,
      AccuracyM: f.accuracy,
    };
    if (!next.length || next[next.length - 1].VideoSecond !== sec) {
      next.push(row);
    } else {
      next[next.length - 1] = row;
    }
    gpsLogRef.current = next;
    setGpsLog(next);
  }, []);

  /** Fill missing integer seconds so detection gets lat/lon every second. */
  const densifyGpsLog = useCallback((rows) => {
    if (!rows?.length) return [];
    const bySec = new Map();
    for (const r of rows) {
      const sec = Math.round(Number(r.VideoSecond));
      if (!Number.isFinite(sec) || sec < 0) continue;
      bySec.set(sec, { ...r, VideoSecond: sec });
    }
    const keys = [...bySec.keys()].sort((a, b) => a - b);
    if (!keys.length) return [];
    const out = [];
    for (let i = 0; i < keys.length; i++) {
      const a = keys[i];
      out.push(bySec.get(a));
      if (i + 1 >= keys.length) break;
      const b = keys[i + 1];
      const gap = b - a;
      if (gap <= 1 || gap > 8) continue;
      const sa = bySec.get(a);
      const sb = bySec.get(b);
      for (let s = a + 1; s < b; s++) {
        const t = (s - a) / gap;
        out.push({
          VideoSecond: s,
          Latitude: sa.Latitude + (sb.Latitude - sa.Latitude) * t,
          Longitude: sa.Longitude + (sb.Longitude - sa.Longitude) * t,
          AccuracyM: sa.AccuracyM,
          FrameIndex: s * 30,
        });
      }
    }
    return out.map((r) => ({
      ...r,
      FrameIndex: r.FrameIndex != null ? r.FrameIndex : (Number(r.VideoSecond) || 0) * 30,
    }));
  }, []);

  const gpsCsvBody = useCallback((rows) => {
    const densified = densifyGpsLog(rows);
    return densified
      .map((r) => {
        const sec = Number(r.VideoSecond) || 0;
        const frame = r.FrameIndex != null ? r.FrameIndex : sec * 30;
        return `${sec},${r.Latitude},${r.Longitude},${r.AccuracyM ?? ''},${frame}`;
      })
      .join('\n');
  }, [densifyGpsLog]);

  const writeFrameMeta = useCallback(async (dirOrCache, base, rows, geo = null) => {
    const densified = densifyGpsLog(rows);
    const frames = densified.map((r) => {
      const sec = Number(r.VideoSecond) || 0;
      return {
        frame: r.FrameIndex != null ? r.FrameIndex : sec * 30,
        video_second: sec,
        t_ms: sec * 1000,
        lat: r.Latitude,
        lon: r.Longitude,
        accuracy_m: r.AccuracyM,
      };
    });
    const payload = {
      assumed_fps: 30,
      country: 'India',
      country_code: 'IN',
      ...(geo || {}),
      frames,
    };
    const path = `${dirOrCache}${base}_log.json`;
    await FileSystem.writeAsStringAsync(
      path,
      JSON.stringify(payload, null, 2),
    );
    return path;
  }, [densifyGpsLog]);

  const resolveGeoContext = useCallback(async () => {
    try {
      const me = await api.me().catch(() => null);
      const summary = await api.assignmentSummary().catch(() => null);
      const sid = me?.state_id;
      const state =
        sid === 2 || sid === '2' ? 'Telangana'
          : sid === 1 || sid === '1' ? 'Andhra Pradesh'
            : undefined;
      const district =
        summary?.district_name
        || summary?.district
        || undefined;
      const district_id =
        summary?.district_id
        || me?.district_id
        || (me?.district_ids || [])[0]
        || undefined;
      return {
        country: 'India',
        country_code: 'IN',
        state,
        state_id: sid ?? undefined,
        district,
        district_id,
      };
    } catch {
      return { country: 'India', country_code: 'IN' };
    }
  }, []);

  const rollbackProvisionalCoverage = useCallback(async () => {
    const sid = captureSessionIdRef.current;
    const hadServer = chunkSessionReadyRef.current;
    captureSessionIdRef.current = null;
    chunkSessionReadyRef.current = false;
    localModeRef.current = false;
    if (!sid) return;
    if (hadServer) {
      await api.uploadSessionDiscard(sid).catch(() => null);
    }
    await offlineCapture.discardLocalSession(sid);
  }, []);

  const flushGpsToLocal = useCallback(async (sid) => {
    if (!sid || !gpsLogRef.current.length) return;
    const header = 'VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n';
    await offlineCapture.saveGpsCsv(sid, header + gpsCsvBody(gpsLogRef.current));
    const densified = densifyGpsLog(gpsLogRef.current);
    const frames = densified.map((r) => {
      const sec = Number(r.VideoSecond) || 0;
      return {
        frame: r.FrameIndex != null ? r.FrameIndex : sec * 30,
        video_second: sec,
        t_ms: sec * 1000,
        lat: r.Latitude,
        lon: r.Longitude,
        accuracy_m: r.AccuracyM,
      };
    });
    const geo = await resolveGeoContext();
    await offlineCapture.saveFrameMeta(
      sid,
      JSON.stringify({ assumed_fps: 30, ...geo, frames }, null, 2),
    );
  }, [gpsCsvBody, densifyGpsLog, resolveGeoContext]);

  const parkSessionOnDevice = useCallback(async (sid, hadServer) => {
    if (!sid) return;
    await flushGpsToLocal(sid);
    await offlineCapture.markSessionPending(sid);
    if (hadServer) {
      await api.uploadSessionDiscard(sid).catch(() => null);
      await offlineCapture.clearUploadedMarks(sid);
    }
  }, [flushGpsToLocal]);

  const uploadChunkOnce = useCallback(async (uri, index, sid) => {
    if (discardedRef.current) return { localOnly: true };
    const sessionId = sid || captureSessionIdRef.current;
    if (!sessionId || !uri) return;
    // Always persist on device first (survives kill + offline).
    const localUri = await offlineCapture.saveLocalChunk(sessionId, index, uri);
    if (discardedRef.current) return { localOnly: true };
    await flushGpsToLocal(sessionId);

    if (localModeRef.current || !chunkSessionReadyRef.current || !(await isOnline())) {
      localModeRef.current = true;
      chunkSessionReadyRef.current = false;
      return { localOnly: true };
    }
    const f = fixRef.current;
    chunkUploadingRef.current = true;
    try {
      await api.uploadSessionChunk({
        captureSessionId: sessionId,
        mediaUri: localUri,
        mediaName: `chunk_${index}.mp4`,
        chunkIndex: index,
        lat: f?.lat,
        lon: f?.lon,
        accuracy: f?.accuracy,
      });
      await offlineCapture.markChunkUploaded(sessionId, index);
    } catch (e) {
      // Server session may be gone — fall back to device; next Upload will re-init.
      localModeRef.current = true;
      chunkSessionReadyRef.current = false;
      throw e;
    } finally {
      chunkUploadingRef.current = false;
    }
    return { localOnly: false };
  }, [flushGpsToLocal]);

  /** Retries a chunk a few times (transient network blips) before giving up on it. */
  const uploadChunkWithRetry = useCallback(async (uri, index, sid) => {
    let lastErr;
    for (let attempt = 0; attempt <= CHUNK_RETRY_DELAYS_MS.length; attempt++) {
      try {
        const result = await uploadChunkOnce(uri, index, sid);
        // Local-only save is success for recording continuity; upload deferred.
        return { ok: true, localOnly: Boolean(result?.localOnly) };
      } catch (e) {
        lastErr = e;
        // After save to disk succeeded once, prefer local mode over endless retry.
        if (attempt >= 1) {
          localModeRef.current = true;
          chunkSessionReadyRef.current = false;
          try {
            await uploadChunkOnce(uri, index, sid);
            return { ok: true, localOnly: true };
          } catch (_) {
            /* fall through */
          }
        }
        if (attempt < CHUNK_RETRY_DELAYS_MS.length) {
          await new Promise((r) => setTimeout(r, CHUNK_RETRY_DELAYS_MS[attempt]));
        }
      }
    }
    setError(`Minute ${index + 1} failed to save/upload after retries: ${lastErr?.message || lastErr}. Will retry automatically.`);
    return { ok: false, localOnly: false };
  }, [uploadChunkOnce]);

  /** Re-attempts any chunks that previously exhausted retries — call before each new
   * chunk and once more after recording stops, so a temporary outage self-heals. */
  const drainPendingChunks = useCallback(async () => {
    const pending = pendingRetryChunksRef.current;
    pendingRetryChunksRef.current = [];
    for (const { uri, index, sid } of pending) {
      const { ok, localOnly } = await uploadChunkWithRetry(uri, index, sid);
      if (ok) {
        chunksUploadedRef.current = Math.max(chunksUploadedRef.current, index + 1);
        if (localOnly) localModeRef.current = true;
      } else {
        pendingRetryChunksRef.current.push({ uri, index, sid });
      }
    }
  }, [uploadChunkWithRetry]);

  const enqueueChunkUpload = useCallback((uri, index) => {
    const sid = captureSessionIdRef.current;
    chunkUploadChainRef.current = chunkUploadChainRef.current
      .then(async () => {
        if (discardedRef.current) return;
        await drainPendingChunks();
        if (discardedRef.current || !uri) return;
        const { ok, localOnly } = await uploadChunkWithRetry(uri, index, sid);
        if (discardedRef.current) return;
        if (ok) {
          chunksUploadedRef.current = Math.max(chunksUploadedRef.current, index + 1);
          if (localOnly) {
            localModeRef.current = true;
            chunkSessionReadyRef.current = false;
          }
          if (phaseRef.current === 'recording') {
            setStatus(
              localModeRef.current
                ? `Recording… (${chunksUploadedRef.current} min saved on device)`
                : `Recording… (server has ${chunksUploadedRef.current} min clip(s))`,
            );
          }
        } else {
          pendingRetryChunksRef.current.push({ uri, index, sid });
        }
      })
      .catch((e) => {
        if (!discardedRef.current) {
          setError(`Chunk ${index} upload failed: ${e?.message || e}`);
        }
      });
    return chunkUploadChainRef.current;
  }, [drainPendingChunks, uploadChunkWithRetry]);

  const elapsedSec = () => {
    if (!startedAtRef.current) return 0;
    const pauseExtra =
      phaseRef.current === 'paused' && pauseStartedRef.current
        ? Date.now() - pauseStartedRef.current
        : 0;
    return Math.max(
      0,
      Math.floor((Date.now() - startedAtRef.current - pausedAccumRef.current - pauseExtra) / 1000),
    );
  };

  const maybeTrackingPing = useCallback((payload, minIntervalMs = 2000) => {
    // Never spam Flask while an upload is in flight — that was leaving dozens of
    // CLOSE_WAIT sockets and starving the upload handshake (60s timeouts).
    // Each ~60s chunk upload already piggybacks the current GPS fix as a ping
    // server-side, so this is now just a light keepalive between chunks.
    if (uploadingRef.current || chunkUploadingRef.current) return;
    const now = Date.now();
    if (now - lastPingAtRef.current < minIntervalMs) return;
    lastPingAtRef.current = now;
    api.trackingPing(payload);
  }, []);

  const startGpsTick = useCallback(() => {
    stopGpsTick();
    gpsTickRef.current = setInterval(() => {
      if (!recordingRef.current || phaseRef.current !== 'recording') return;
      const f = fixRef.current;
      if (!f) return;
      const sec = elapsedSec();
      pushGpsSample(f, sec);
      maybeTrackingPing({
        lat: f.lat,
        lon: f.lon,
        accuracy: f.accuracy,
        recording: true,
        capture_session_id: captureSessionIdRef.current,
      }, 20000);
    }, 1000);
  }, [pushGpsSample, stopGpsTick, maybeTrackingPing]);

  const startGpsWatch = useCallback(() => {
    watchRef.current?.remove?.();
    watchRef.current = Location.watchPositionAsync(
      { accuracy: Location.Accuracy.BestForNavigation, distanceInterval: 0, timeInterval: 1000 },
      (pos) => {
        const f = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        fixRef.current = f;
        setFix(f);
        if (!recordingRef.current) {
          maybeTrackingPing({
            lat: f.lat,
            lon: f.lon,
            accuracy: f.accuracy,
            recording: false,
          }, 5000);
          return;
        }
        const sec = elapsedSec();
        pushGpsSample(f, sec);
        maybeTrackingPing({
          lat: f.lat,
          lon: f.lon,
          accuracy: f.accuracy,
          recording: true,
          capture_session_id: captureSessionIdRef.current,
        }, 20000);
      },
    ).then((sub) => {
      watchRef.current = sub;
    });
  }, [pushGpsSample, maybeTrackingPing]);

  useEffect(() => {
    (async () => {
      if (!camPerm?.granted) await requestCamPerm();
      if (!micPerm?.granted) await requestMicPerm();
      const { status: locStatus } = await Location.requestForegroundPermissionsAsync();
      setLocOk(locStatus === 'granted');
      if (locStatus === 'granted') {
        const pos = await Location.getCurrentPositionAsync({});
        const f = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        fixRef.current = f;
        setFix(f);
        api.trackingPing({
          lat: f.lat,
          lon: f.lon,
          accuracy: f.accuracy,
          recording: false,
        });
        startGpsWatch();
      }
    })();
    return () => {
      watchRef.current?.remove?.();
      stopGpsTick();
    };
  }, [stopGpsTick, startGpsWatch]);

  const waitForVideoUri = async (timeoutMs = 2500) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (videoUriRef.current) return videoUriRef.current;
      await new Promise((r) => setTimeout(r, 80));
    }
    return videoUriRef.current;
  };

  const start = async () => {
    // Never start a second record loop while one is still spinning.
    if (wantRecordRef.current || recordingRef.current || nativeRecordingActiveRef.current) {
      wantRecordRef.current = false;
      await stopRecordingOnly();
      await chunkUploadChainRef.current.catch(() => {});
      await new Promise((r) => setTimeout(r, 250));
    }

    const priorSid = captureSessionIdRef.current;
    if (priorSid && phaseRef.current === 'ended') {
      const hadServer = chunkSessionReadyRef.current;
      const hadChunks = chunksUploadedRef.current > 0 || nextChunkIndexRef.current > 0;
      await flushGpsToLocal(priorSid);
      if (hadChunks) {
        await parkSessionOnDevice(priorSid, hadServer);
      } else {
        await rollbackProvisionalCoverage();
      }
      captureSessionIdRef.current = null;
      chunkSessionReadyRef.current = false;
      localModeRef.current = false;
    }

    setError('');
    setStatus('');
    setVideoUri(null);
    videoUriRef.current = null;
    gpsLogRef.current = [];
    setGpsLog([]);
    setEverPaused(false);
    pausedAccumRef.current = 0;
    pauseStartedRef.current = 0;
    startedAtRef.current = Date.now();
    nextChunkIndexRef.current = 0;
    chunksUploadedRef.current = 0;
    chunkSessionReadyRef.current = false;
    localModeRef.current = false;
    discardedRef.current = false;
    backgroundInterruptRef.current = false;
    interruptedWhilePausedRef.current = false;
    pendingRetryChunksRef.current = [];
    const sid = newCaptureSessionId();
    captureSessionIdRef.current = sid;
    if (!camRef.current) {
      setError('Camera not ready');
      return;
    }
    if (!locOk) {
      setError('Location required');
      return;
    }
    if (!fixRef.current) {
      setError('Waiting for GPS fix…');
      return;
    }

    const netOk = await isOnline();
    await offlineCapture.initLocalSession(sid, { offline: !netOk });

    if (netOk) {
      try {
        setStatus('Starting chunk session…');
        await api.uploadSessionInit(sid);
        chunkSessionReadyRef.current = true;
        localModeRef.current = false;
      } catch (e) {
        localModeRef.current = true;
        chunkSessionReadyRef.current = false;
        setStatus(`Offline mode — ${e?.message || 'server unavailable'}. Clips saved on device.`);
      }
    } else {
      localModeRef.current = true;
      setStatus('Offline — recording to device; upload when online.');
    }

    const feats = camRef.current.getSupportedFeatures?.() || {};
    setCanTogglePause(Boolean(feats.toggleRecordingAsyncAvailable));

    wantRecordRef.current = true;
    recordingRef.current = true;
    setPhase('recording');
    if (!localModeRef.current) {
      setStatus(`Recording… (uploads every ${CHUNK_SECONDS}s)`);
    } else if (netOk) {
      /* status already set above for server-init failure */
    } else {
      setStatus(`Recording offline… (saves every ${CHUNK_SECONDS}s on device)`);
    }
    startGpsWatch();
    startGpsTick();
    if (fixRef.current) pushGpsSample(fixRef.current, 0);

    (async () => {
      try {
        while (wantRecordRef.current && camRef.current) {
          if (phaseRef.current === 'paused') {
            await new Promise((r) => setTimeout(r, 200));
            continue;
          }
          if (phaseRef.current === 'ended' || phaseRef.current === 'idle') break;

          recordingRef.current = true;
          let result;
          nativeRecordingActiveRef.current = true;
          try {
            result = await camRef.current.recordAsync({ maxDuration: CHUNK_SECONDS });
          } catch (e) {
            nativeRecordingActiveRef.current = false;
            if (!wantRecordRef.current) break;
            if (interruptedWhilePausedRef.current || phaseRef.current === 'paused') {
              interruptedWhilePausedRef.current = false;
              setStatus('Paused — tap Resume to continue');
              continue;
            }
            throw e;
          }
          nativeRecordingActiveRef.current = false;

          const intentionalStop =
            !wantRecordRef.current
            || phaseRef.current === 'ended'
            || phaseRef.current === 'idle';
          const interruptedPaused =
            !intentionalStop
            && (phaseRef.current === 'paused' || interruptedWhilePausedRef.current);

          if (interruptedPaused) {
            interruptedWhilePausedRef.current = false;
            backgroundInterruptRef.current = false;
            if (result?.uri) {
              await FileSystem.deleteAsync(result.uri, { idempotent: true }).catch(() => null);
            }
            setStatus('Paused — tap Resume to continue');
            continue;
          }

          if (result?.uri) {
            videoUriRef.current = result.uri;
            setVideoUri(result.uri);
            const idx = nextChunkIndexRef.current;
            nextChunkIndexRef.current += 1;
            const label = intentionalStop || backgroundInterruptRef.current
              ? `clip ${idx + 1}`
              : `minute ${idx + 1}`;
            backgroundInterruptRef.current = false;
            setStatus(
              localModeRef.current
                ? `Saving ${label} on device…`
                : `Sending ${label} to server…`,
            );
            enqueueChunkUpload(result.uri, idx);
          }

          if (!wantRecordRef.current) break;
          if (phaseRef.current === 'ended' || phaseRef.current === 'idle') break;
        }
      } catch (e) {
        if (wantRecordRef.current && phaseRef.current !== 'idle' && phaseRef.current !== 'paused') {
          setError(e?.message || 'Recording stopped');
        }
      } finally {
        recordingRef.current = false;
        nativeRecordingActiveRef.current = false;
        stopGpsTick();
        startGpsWatch();
      }
    })();
  };

  /** Start new after ended — always stop prior loop; park only if real clips exist. */
  const startNew = async () => {
    if (uploading || uploadingRef.current) return;
    setError('');
    wantRecordRef.current = false;
    await stopRecordingOnly();
    await chunkUploadChainRef.current.catch(() => {});
    setModalOpen(false);
    await start();
  };

  const pause = async () => {
    if (!camRef.current || phase !== 'recording') return;
    if (!nativeRecordingActiveRef.current) {
      // This minute's clip already rotated/finished under us —
      // stay in session; next Resume/loop segment continues.
      setStatus('This clip just finished — recording continues on the next segment.');
      return;
    }
    const feats = camRef.current.getSupportedFeatures?.() || {};
    if (!feats.toggleRecordingAsyncAvailable) {
      setError('Pause/resume needs a device that supports continuous pause (Android / iOS 18+). Use End when finished.');
      return;
    }
    try {
      await runCameraOp(() => camRef.current.toggleRecordingAsync());
    } catch (e) {
      if (isRecordingAlreadyStoppedError(e)) {
        setStatus('This clip just finished — tap Resume to continue on the next segment.');
        setPhase('paused');
        recordingRef.current = false;
        stopGpsTick();
        setEverPaused(true);
        return;
      }
      setError(e?.message || 'Pause failed');
      return;
    }
    pauseStartedRef.current = Date.now();
    recordingRef.current = false;
    stopGpsTick();
    setEverPaused(true);
    setPhase('paused');
    setStatus('Paused — Resume continues this minute clip');
  };

  const resume = async () => {
    if (phase !== 'paused' || !camRef.current) return;
    setError('');
    if (pauseStartedRef.current) {
      pausedAccumRef.current += Date.now() - pauseStartedRef.current;
      pauseStartedRef.current = 0;
    }
    interruptedWhilePausedRef.current = false;
    backgroundInterruptRef.current = false;

    // Clip was killed while paused (sleep/background) — continue same session
    // with a fresh recordAsync segment instead of jumping to End/Upload.
    if (!nativeRecordingActiveRef.current) {
      wantRecordRef.current = true;
      recordingRef.current = true;
      setPhase('recording');
      setStatus('Recording… (continued after interrupt)');
      startGpsTick();
      return;
    }
    try {
      await runCameraOp(() => camRef.current.toggleRecordingAsync());
    } catch (e) {
      if (isRecordingAlreadyStoppedError(e)) {
        wantRecordRef.current = true;
        recordingRef.current = true;
        setPhase('recording');
        setStatus('Recording… (continued after interrupt)');
        startGpsTick();
        return;
      }
      setError(e?.message || 'Resume failed');
      return;
    }
    recordingRef.current = true;
    setPhase('recording');
    setStatus('Recording…');
    startGpsTick();
  };

  // Only stops the physical recording — deliberately does NOT wait for the
  // chunk-upload chain (that can now take minutes across retries on a bad
  // connection). End/Discard/Save must stay fast; uploads keep running in the
  // background via chunkUploadChainRef and are awaited later, only where a
  // wait is actually required (finalize, in uploadToServer()).
  const stopRecordingOnly = async () => {
    wantRecordRef.current = false;
    try {
      await runCameraOp(async () => {
        camRef.current?.stopRecording();
      });
    } catch (_) {}
    recordingRef.current = false;
    stopGpsTick();
    startGpsWatch();
    await waitForVideoUri();
  };

  const end = async () => {
    setStatus(localModeRef.current ? 'Saving last clip on device…' : 'Sending last clip…');
    await stopRecordingOnly();
    // Wait for last minute to land on disk (and optionally server) before Upload/Discard.
    await chunkUploadChainRef.current.catch(() => {});
    const sid = captureSessionIdRef.current;
    if (sid) await flushGpsToLocal(sid);
    setPhase('ended');
    setStatus(
      chunksUploadedRef.current > 0
        ? localModeRef.current
          ? `Ready — Upload when online. ${chunksUploadedRef.current} clip(s) on device.`
          : `Ready — Upload or Discard. ${chunksUploadedRef.current} clip(s) on server.`
        : 'Ready — choose Upload',
    );
    setModalOpen(true);
  };

  const discard = async () => {
    discardedRef.current = true;
    pendingRetryChunksRef.current = [];
    await stopRecordingOnly();
    // Drain queue so a late saveLocalChunk cannot resurrect the session.
    await chunkUploadChainRef.current.catch(() => {});
    setVideoUri(null);
    videoUriRef.current = null;
    gpsLogRef.current = [];
    setGpsLog([]);
    setPhase('idle');
    setEverPaused(false);
    setModalOpen(false);
    setStatus('Capture discarded.');
    setError('');
    chunksUploadedRef.current = 0;
    nextChunkIndexRef.current = 0;
    await rollbackProvisionalCoverage();
  };

  const saveLocal = async () => {
    if (phase === 'recording' || phase === 'paused') {
      await stopRecordingOnly();
      setPhase('ended');
    }
    setModalOpen(false);
    const sid = captureSessionIdRef.current;
    const hadServer = chunkSessionReadyRef.current;
    try {
      await chunkUploadChainRef.current.catch(() => {});
      if (sid) {
        await flushGpsToLocal(sid);
        await offlineCapture.markSessionPending(sid);
        if (hadServer) {
          await api.uploadSessionDiscard(sid).catch(() => null);
          await offlineCapture.clearUploadedMarks(sid);
        }
      }
      captureSessionIdRef.current = null;
      chunkSessionReadyRef.current = false;
      localModeRef.current = false;
      setVideoUri(null);
      videoUriRef.current = null;
      gpsLogRef.current = [];
      setGpsLog([]);
      setPhase('idle');
      setEverPaused(false);
      chunksUploadedRef.current = 0;
      nextChunkIndexRef.current = 0;
      pendingRetryChunksRef.current = [];
      setStatus(
        sid
          ? 'Saved on device. Use Upload saved when online.'
          : 'Nothing to save — End after recording.',
      );
    } catch (e) {
      setError(`Save failed: ${e.message}`);
    }
  };

  const leaveCapture = useCallback(async ({ discard = false } = {}) => {
    if (uploadingRef.current) return;
    const sid = captureSessionIdRef.current;
    const phaseNow = phaseRef.current;

    if (!sid && phaseNow === 'idle') {
      onBack?.(false);
      return;
    }

    discardedRef.current = discard;
    wantRecordRef.current = false;
    pendingRetryChunksRef.current = [];

    if (phaseNow === 'recording' || phaseNow === 'paused') {
      await stopRecordingOnly();
      await chunkUploadChainRef.current.catch(() => {});
    } else {
      await chunkUploadChainRef.current.catch(() => {});
    }

    setModalOpen(false);

    if (discard && sid) {
      await rollbackProvisionalCoverage();
    } else if (sid) {
      const hadServer = chunkSessionReadyRef.current;
      await parkSessionOnDevice(sid, hadServer);
      captureSessionIdRef.current = null;
      chunkSessionReadyRef.current = false;
      localModeRef.current = false;
    }

    setVideoUri(null);
    videoUriRef.current = null;
    gpsLogRef.current = [];
    setGpsLog([]);
    setPhase('idle');
    setEverPaused(false);
    chunksUploadedRef.current = 0;
    nextChunkIndexRef.current = 0;
    onBack?.(false);
  }, [onBack, parkSessionOnDevice, rollbackProvisionalCoverage, stopRecordingOnly]);

  const onBackPress = useCallback(() => {
    if (uploadingRef.current) return true;
    const sid = captureSessionIdRef.current;
    const phaseNow = phaseRef.current;
    if (!sid && phaseNow === 'idle') {
      onBack?.(false);
      return true;
    }
    Alert.alert(
      'Leave capture?',
      'Save keeps clips on this phone for later upload. Discard deletes them.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Discard', style: 'destructive', onPress: () => leaveCapture({ discard: true }) },
        { text: 'Save on device', onPress: () => leaveCapture({ discard: false }) },
      ],
    );
    return true;
  }, [leaveCapture, onBack]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', onBackPress);
    return () => sub.remove();
  }, [onBackPress]);

  /** Ensure server session exists, push every local chunk in order, then finalize. */
  const uploadLocalSessionToServer = useCallback(async (sid) => {
    if (!(await isOnline())) {
      throw new Error('Offline — connect to Wi‑Fi or mobile data, then try Upload again.');
    }

    const postAllChunks = async (list) => {
      const pending = list.filter((c) => !c.uploaded);
      const already = list.length - pending.length;
      const total = list.length || 1;
      let done = already;
      for (const { index, uri, uploaded } of list) {
        if (uploaded) continue;
        const left = total - done;
        setUploadPct(Math.round((done / total) * 100));
        setStatus(
          `Uploading ${Math.round((done / total) * 100)}% — clip ${index + 1}/${total} (${left} left)`,
        );
        const f = fixRef.current;
        await api.uploadSessionChunk({
          captureSessionId: sid,
          mediaUri: uri,
          mediaName: `chunk_${index}.mp4`,
          chunkIndex: index,
          lat: f?.lat,
          lon: f?.lon,
          accuracy: f?.accuracy,
          onProgress: ({ fraction }) => {
            const overall = (done + fraction) / total;
            const pct = Math.min(99, Math.round(overall * 100));
            setUploadPct(pct);
            setStatus(
              `Uploading ${pct}% — clip ${index + 1}/${total} (${total - done} left)`,
            );
          },
        });
        await offlineCapture.markChunkUploaded(sid, index);
        done += 1;
        chunksUploadedRef.current = Math.max(chunksUploadedRef.current, index + 1);
        setUploadPct(Math.round((done / total) * 100));
      }
    };

    const reinitAndUploadAll = async () => {
      setStatus('Connecting upload session…');
      await api.uploadSessionInit(sid);
      await offlineCapture.clearUploadedMarks(sid);
      chunkSessionReadyRef.current = true;
      captureSessionIdRef.current = sid;
      localModeRef.current = false;
      const refreshed = await offlineCapture.listLocalChunks(sid);
      if (!refreshed.length) {
        throw new Error('No video clips on device for this session.');
      }
      await postAllChunks(refreshed);
    };

    let chunks = await offlineCapture.listLocalChunks(sid);
    if (!chunks.length) {
      throw new Error('No video clips on device for this session.');
    }
    // Server init wipes the session dir — local "uploaded" marks become stale.
    const needInit = !chunkSessionReadyRef.current || captureSessionIdRef.current !== sid;
    if (needInit) {
      await reinitAndUploadAll();
    } else {
      try {
        await postAllChunks(chunks);
      } catch (e) {
        const msg = String(e?.message || e || '');
        // Only wipe+re-init when the server session is gone — not on 503/stall.
        if (/unknown chunk session|no chunk session|no video chunks uploaded yet/i.test(msg)) {
          await reinitAndUploadAll();
        } else {
          throw e;
        }
      }
    }

    const stamp = Date.now();
    const header = 'VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n';
    let gpsBody = gpsCsvBody(gpsLogRef.current);
    if (!gpsLogRef.current.length) {
      const gpsPathExisting = `${offlineCapture.sessionDir(sid)}gps.csv`;
      const info = await FileSystem.getInfoAsync(gpsPathExisting);
      if (info.exists) {
        const raw = await FileSystem.readAsStringAsync(gpsPathExisting);
        const lines = raw.split(/\r?\n/).filter(Boolean);
        gpsBody = lines[0]?.startsWith('VideoSecond')
          ? lines.slice(1).join('\n')
          : lines.join('\n');
      }
    }
    if (!gpsBody?.trim()) {
      throw new Error('No GPS points — enable location and record before upload.');
    }
    const gpsPath = `${FileSystem.cacheDirectory}gps_${stamp}.csv`;
    await FileSystem.writeAsStringAsync(gpsPath, header + gpsBody);
    let framesPath;
    if (gpsLogRef.current.length) {
      const geo = await resolveGeoContext();
      framesPath = await writeFrameMeta(
        FileSystem.cacheDirectory,
        `capture_${stamp}`,
        gpsLogRef.current,
        geo,
      );
    } else {
      const localFrames = `${offlineCapture.sessionDir(sid)}frames_log.json`;
      const fi = await FileSystem.getInfoAsync(localFrames);
      framesPath = fi.exists ? localFrames : undefined;
    }
    setStatus('Finishing upload…');
    setUploadPct(100);
    let routeFields = {};
    try {
      const summary = await api.assignmentSummary();
      routeFields = routeUploadFields(summary);
    } catch {
      routeFields = {};
    }
    const names = fieldSiblingNames('capture');
    let data;
    try {
      data = await api.uploadSessionFinalize({
        captureSessionId: sid,
        gpsUri: gpsPath,
        gpsName: names.gpsName,
        frameMetaUri: framesPath,
        frameMetaName: framesPath ? names.frameMetaName : undefined,
        startLabel: routeFields.start_label,
        endLabel: routeFields.end_label,
        routeLabel: routeFields.route_label,
      });
    } catch (e) {
      // All clips marked uploaded locally but server empty (kill / restart).
      const msg = String(e?.message || e || '');
      if (/no video chunks/i.test(msg)) {
        await reinitAndUploadAll();
        data = await api.uploadSessionFinalize({
          captureSessionId: sid,
          gpsUri: gpsPath,
          gpsName: names.gpsName,
          frameMetaUri: framesPath,
          frameMetaName: framesPath ? names.frameMetaName : undefined,
          startLabel: routeFields.start_label,
          endLabel: routeFields.end_label,
          routeLabel: routeFields.route_label,
        });
      } else {
        throw e;
      }
    }
    await offlineCapture.markSessionUploaded(sid);
    return data;
  }, [gpsCsvBody, writeFrameMeta]);

  const uploadToServer = async () => {
    if (phase === 'recording' || phase === 'paused') {
      await stopRecordingOnly();
      setPhase('ended');
    }
    setModalOpen(false);
    const sid = captureSessionIdRef.current;
    if (!sid) {
      setError('No capture session — start recording again.');
      return;
    }
    setUploading(true);
    uploadingRef.current = true;
    setUploadPct(0);
    watchRef.current?.remove?.();
    watchRef.current = null;
    stopGpsTick();
    setError('');
    setStatus('Starting upload…');
    try {
      await chunkUploadChainRef.current.catch(() => {});
      await flushGpsToLocal(sid);
      await drainPendingChunks();

      const data = await uploadLocalSessionToServer(sid);

      const gapMsg = data?.missing_indices?.length
        ? ` Warning: minute(s) ${data.missing_indices.map((m) => m + 1).join(', ')} never uploaded — this video may have gaps.`
        : '';
      const doneMsg =
        data?.status?.message
        || `Upload complete — ${chunksUploadedRef.current} clip(s) on server.`;
      captureSessionIdRef.current = null;
      chunkSessionReadyRef.current = false;
      localModeRef.current = false;
      chunksUploadedRef.current = 0;
      nextChunkIndexRef.current = 0;
      pendingRetryChunksRef.current = [];
      setVideoUri(null);
      videoUriRef.current = null;
      gpsLogRef.current = [];
      setGpsLog([]);
      setPhase('idle');
      setEverPaused(false);
      setStatus(`${doneMsg}${gapMsg} Returning to map…`);
      setTimeout(() => onBack?.(true), 900);
    } catch (e) {
      setError(`Upload failed: ${e.message}`);
      setPhase('ended');
    } finally {
      uploadingRef.current = false;
      setUploading(false);
      setUploadPct(null);
      startGpsWatch();
    }
  };

  const uploadSaved = async () => {
    if (!(await isOnline())) {
      setError('Offline — connect to Wi‑Fi or mobile data, then try "Upload saved" again.');
      return;
    }
    try {
      setUploading(true);
      uploadingRef.current = true;
      setUploadPct(0);
      watchRef.current?.remove?.();
      watchRef.current = null;
      stopGpsTick();
      setError('');

      const pending = await offlineCapture.listPendingSessions();
      if (pending.length) {
        for (let i = 0; i < pending.length; i += 1) {
          const session = pending[i];
          setStatus(
            pending.length > 1
              ? `Uploading saved ${i + 1}/${pending.length} (${session.chunkCount} clip(s))…`
              : `Uploading saved session (${session.chunkCount} clip(s))…`,
          );
          localModeRef.current = true;
          chunkSessionReadyRef.current = false;
          captureSessionIdRef.current = session.sessionId;
          const gpsPath = `${offlineCapture.sessionDir(session.sessionId)}gps.csv`;
          const gi = await FileSystem.getInfoAsync(gpsPath);
          if (gi.exists) {
            const raw = await FileSystem.readAsStringAsync(gpsPath);
            const lines = raw.split(/\r?\n/).filter(Boolean);
            const dataLines = lines[0]?.startsWith('VideoSecond') ? lines.slice(1) : lines;
            gpsLogRef.current = dataLines.map((line) => {
              const [VideoSecond, Latitude, Longitude, AccuracyM, FrameIndex] = line.split(',');
              return {
                VideoSecond: Number(VideoSecond),
                Latitude: Number(Latitude),
                Longitude: Number(Longitude),
                AccuracyM: AccuracyM === '' ? undefined : Number(AccuracyM),
                FrameIndex: FrameIndex === '' || FrameIndex == null ? undefined : Number(FrameIndex),
              };
            }).filter((r) => Number.isFinite(r.Latitude) && Number.isFinite(r.Longitude));
            setGpsLog(gpsLogRef.current);
          } else {
            gpsLogRef.current = [];
            setGpsLog([]);
          }
          const data = await uploadLocalSessionToServer(session.sessionId);
          const sealMsg = data?.status?.message || data?.roads_completed?.message || 'Upload complete';
          if (i === pending.length - 1) {
            captureSessionIdRef.current = null;
            chunkSessionReadyRef.current = false;
            localModeRef.current = false;
            setStatus(`${sealMsg} Returning to map…`);
            setTimeout(() => onBack?.(true), 900);
          }
        }
        return;
      }

      const dest = `${FileSystem.documentDirectory}captures/`;
      const listing = await FileSystem.readDirectoryAsync(dest).catch(() => []);
      const videos = listing
        .filter((n) => n.toLowerCase().endsWith('.mp4') && !n.startsWith('chunk_'))
        .sort()
        .reverse();
      // Ignore session folders (no flat mp4)
      const flatVideos = [];
      for (const n of videos) {
        const info = await FileSystem.getInfoAsync(`${dest}${n}`);
        if (info.exists && !info.isDirectory) flatVideos.push(n);
      }
      if (!flatVideos.length) {
        setError('No saved captures yet.');
        return;
      }
      const name = flatVideos[0];
      const base = name.replace(/\.mp4$/i, '');
      const csvName = `${base}.csv`;
      if (!listing.includes(csvName)) {
        setError(`Latest saved video has no GPS CSV (${csvName}).`);
        return;
      }
      setStatus('Uploading saved capture…');
      const videoUriSaved = `${dest}${name}`;
      const gpsUriSaved = `${dest}${csvName}`;
      const framesName = `${base}_log.json`;
      let frameMetaUri;
      if (listing.includes(framesName)) {
        frameMetaUri = `${dest}${framesName}`;
      }
      const info = await FileSystem.getInfoAsync(videoUriSaved);
      const data = await api.uploadField({
        mediaUri: videoUriSaved,
        mediaName: name,
        gpsUri: gpsUriSaved,
        gpsName: csvName,
        frameMetaUri,
        frameMetaName: framesName,
        sizeBytes: info?.size || 0,
      });
      const sealMsg = data?.roads_completed?.message || 'Upload complete';
      setStatus(`${sealMsg} Returning to map…`);
      setTimeout(() => onBack?.(true), 900);
    } catch (e) {
      setError(`Saved upload failed: ${e.message}`);
    } finally {
      uploadingRef.current = false;
      setUploading(false);
      setUploadPct(null);
      startGpsWatch();
    }
  };

  if (!camPerm?.granted || !micPerm?.granted) {
    return (
      <View style={styles.root}>
        <View style={styles.top}>
          <Pressable onPress={() => onBack?.(false)}><Text style={styles.back}>← Back</Text></Pressable>
          <Text style={styles.title}>Capture</Text>
          <View style={{ width: 48 }} />
        </View>
        <View style={styles.body}>
          <Text style={styles.meta}>Camera and microphone permission required.</Text>
          <Pressable style={styles.btn} onPress={() => { requestCamPerm(); requestMicPerm(); }}>
            <Text style={styles.btnText}>Grant permissions</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <View style={styles.top}>
        <Pressable
          onPress={onBackPress}
          style={styles.iconBtn}
          hitSlop={8}
        >
          <MaterialIcons name="arrow-back" size={22} color="#fff" />
        </Pressable>
        <Text style={styles.title}>Capture</Text>
        <Text style={[styles.netBadge, online ? styles.netOn : styles.netOff]}>
          {online ? 'Online' : 'Offline'}
        </Text>
      </View>

      <View style={styles.cameraWrap}>
        <CameraView
          ref={camRef}
          style={styles.camera}
          facing="back"
          mode="video"
        />
        {(phase === 'recording' || phase === 'paused') ? (
          <View style={[styles.recBadge, phase === 'paused' && styles.recPaused]}>
            <View style={[styles.recDot, phase === 'paused' && styles.recDotPaused]} />
            <Text style={styles.recText}>{phase === 'paused' ? 'PAUSED' : 'REC'}</Text>
          </View>
        ) : null}
      </View>

      {fix ? (
        <Text style={[styles.gps, phase === 'recording' && styles.gpsRec]}>
          GPS {fix.lat.toFixed(5)}, {fix.lon.toFixed(5)} · {gpsLog.length} pts
          {phase === 'recording' ? ' · REC' : ''}
          {phase === 'paused' ? ' · PAUSED' : ''}
        </Text>
      ) : null}
      {!canTogglePause && phase === 'recording' ? (
        <Text style={styles.metaWarn}>This device may not support Pause — use End when finished.</Text>
      ) : null}
      {status ? <Text style={styles.status}>{status}</Text> : null}
      {uploadPct != null ? (
        <View style={styles.progressWrap}>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${uploadPct}%` }]} />
          </View>
          <Text style={styles.progressLabel}>
            {uploadPct}% done · {Math.max(0, 100 - uploadPct)}% left
          </Text>
        </View>
      ) : null}
      {error ? <Text style={styles.err}>{error}</Text> : null}
      {uploading ? <ActivityIndicator color={colors.navy} style={{ marginVertical: 8 }} /> : null}

      <View style={styles.actions}>
        {phase === 'idle' && (
          <>
            <Pressable style={buttonStyles.primary} onPress={start} disabled={!locOk || uploading}>
              <MaterialIcons name="fiber-manual-record" size={18} color="#fff" />
              <Text style={buttonStyles.primaryText}>Start</Text>
            </Pressable>
            <Pressable
              style={[buttonStyles.outline, { marginTop: 8 }]}
              onPress={uploadSaved}
              disabled={uploading}
            >
              <MaterialIcons name="cloud-upload" size={18} color={colors.navy} />
              <Text style={buttonStyles.outlineText}>Upload saved</Text>
            </Pressable>
          </>
        )}
        {phase === 'recording' && (
          <>
            <Pressable style={buttonStyles.tonal} onPress={pause}>
              <MaterialIcons name="pause" size={18} color={colors.navy} />
              <Text style={buttonStyles.tonalText}>Pause / Stop</Text>
            </Pressable>
            {!everPaused ? (
              <Pressable style={buttonStyles.outline} onPress={end}>
                <MaterialIcons name="stop" size={18} color={colors.navy} />
                <Text style={buttonStyles.outlineText}>End</Text>
              </Pressable>
            ) : (
              <Pressable style={buttonStyles.primary} onPress={() => setModalOpen(true)}>
                <MaterialIcons name="cloud-upload" size={18} color="#fff" />
                <Text style={buttonStyles.primaryText}>Upload</Text>
              </Pressable>
            )}
          </>
        )}
        {phase === 'paused' && (
          <>
            <Pressable style={buttonStyles.primary} onPress={resume}>
              <MaterialIcons name="play-arrow" size={20} color="#fff" />
              <Text style={buttonStyles.primaryText}>Resume</Text>
            </Pressable>
            <Pressable style={buttonStyles.tonal} onPress={() => setModalOpen(true)}>
              <MaterialIcons name="cloud-upload" size={18} color={colors.navy} />
              <Text style={buttonStyles.tonalText}>Upload</Text>
            </Pressable>
          </>
        )}
        {phase === 'ended' && (
          <>
            <Pressable style={buttonStyles.primary} onPress={() => setModalOpen(true)} disabled={uploading}>
              <MaterialIcons name="cloud-upload" size={18} color="#fff" />
              <Text style={buttonStyles.primaryText}>Upload</Text>
            </Pressable>
            <Pressable style={buttonStyles.outline} onPress={discard} disabled={uploading}>
              <MaterialIcons name="delete-outline" size={18} color={colors.navy} />
              <Text style={buttonStyles.outlineText}>Discard</Text>
            </Pressable>
            <Pressable onPress={startNew} disabled={uploading} style={{ padding: 8 }}>
              <Text style={styles.link}>Start new</Text>
            </Pressable>
          </>
        )}
      </View>

      <Modal visible={modalOpen} transparent animationType="fade" onRequestClose={() => setModalOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setModalOpen(false)}>
          <Pressable style={styles.modal} onPress={(e) => e.stopPropagation?.()}>
            <Text style={styles.modalTitle}>Upload capture?</Text>
            <Text style={styles.modalBody}>
              Upload sends any clips still on this phone (works after offline recording). Save keeps the session on device for later. Discard deletes local and server clips.
            </Text>
            <Pressable style={buttonStyles.primary} onPress={uploadToServer} disabled={uploading}>
              <Text style={buttonStyles.primaryText}>Upload</Text>
            </Pressable>
            <Pressable
              style={[buttonStyles.outline, { marginTop: 8 }]}
              onPress={saveLocal}
              disabled={uploading}
            >
              <Text style={buttonStyles.outlineText}>Save</Text>
            </Pressable>
            <Pressable
              style={[buttonStyles.tonalDanger, { marginTop: 8 }]}
              onPress={discard}
              disabled={uploading}
            >
              <Text style={buttonStyles.tonalDangerText}>Discard</Text>
            </Pressable>
            <Pressable onPress={() => setModalOpen(false)} disabled={uploading}>
              <Text style={styles.dismiss}>Dismiss</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.pageBg },
  top: {
    paddingTop: 48,
    paddingHorizontal: 14,
    paddingBottom: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.navy,
  },
  iconBtn: { padding: 4, minWidth: 36 },
  title: { fontWeight: '700', color: '#fff', fontSize: 16 },
  netBadge: { fontSize: 12, fontWeight: '700', minWidth: 56, textAlign: 'right' },
  netOn: { color: '#90EE90' },
  netOff: { color: '#FFB74D' },
  camera: { flex: 1, backgroundColor: '#111' },
  cameraWrap: { flex: 1, backgroundColor: '#111' },
  recBadge: {
    position: 'absolute',
    top: 12,
    left: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(0,0,0,0.75)',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
  },
  recPaused: { opacity: 0.9 },
  recDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.gold,
  },
  recDotPaused: { backgroundColor: '#94a3b8' },
  recText: {
    color: '#fff',
    fontWeight: '700',
    letterSpacing: 1.2,
    fontSize: 12,
  },
  gps: { textAlign: 'center', fontSize: 12, color: colors.muted, padding: 6 },
  gpsRec: { color: '#B8860B', fontWeight: '600' },
  metaWarn: { textAlign: 'center', fontSize: 12, color: '#b45309', paddingHorizontal: 12 },
  status: { textAlign: 'center', paddingHorizontal: 12, color: colors.navy },
  progressWrap: { paddingHorizontal: 16, marginTop: 6, marginBottom: 4 },
  progressTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: '#d7dee8',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: colors.navy,
  },
  progressLabel: {
    marginTop: 4,
    textAlign: 'center',
    fontSize: 12,
    color: colors.navy,
    opacity: 0.85,
  },
  err: { textAlign: 'center', color: colors.danger, paddingHorizontal: 12 },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'center',
    padding: 14,
    paddingBottom: 28,
    backgroundColor: colors.white,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  link: { color: colors.navy, fontWeight: '600', padding: 8 },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
  },
  modal: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 20,
    paddingBottom: 32,
  },
  modalTitle: { fontSize: 18, fontWeight: '700', color: colors.navy, marginBottom: 6 },
  modalBody: { color: '#6b7c8a', marginBottom: 16 },
  dismiss: {
    marginTop: 14,
    textAlign: 'center',
    fontSize: 13,
    color: '#6b7c8a',
    textDecorationLine: 'underline',
  },
});
