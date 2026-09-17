/**
 * useLocation — reusable GPS hook for the reporter app.
 *
 * Features:
 *  - Requests foreground permission on mount (idempotent).
 *  - Fetches an initial high-accuracy fix.
 *  - Optionally starts a background watcher that updates coords in real-time.
 *  - Exposes accuracy, error and a manual `refresh` trigger.
 *  - Cleans up the location subscription on unmount.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Location from 'expo-location';

const DEFAULT_ACCURACY = Location.Accuracy.High;
const WATCHER_DISTANCE_M = 5;        // minimum distance before update fires
const WATCHER_INTERVAL_MS = 10000;   // minimum time between updates

/**
 * @param {object} options
 * @param {boolean} [options.watch=false]   Continuously watch position.
 * @param {number}  [options.accuracy]      expo-location Accuracy enum value.
 */
export function useLocation({ watch = false, accuracy = DEFAULT_ACCURACY } = {}) {
  const [coords, setCoords] = useState(null);   // { latitude, longitude, accuracy, altitude }
  const [error, setError] = useState('');
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [acquiring, setAcquiring] = useState(true);
  const watchRef = useRef(null);

  const stopWatch = useCallback(() => {
    if (watchRef.current) {
      watchRef.current.remove();
      watchRef.current = null;
    }
  }, []);

  const applyPosition = useCallback((pos) => {
    setCoords({
      latitude: pos.coords.latitude,
      longitude: pos.coords.longitude,
      accuracy: pos.coords.accuracy,
      altitude: pos.coords.altitude,
      heading: pos.coords.heading,
      speed: pos.coords.speed,
      capturedAt: new Date().toISOString(),
    });
    setError('');
  }, []);

  const refresh = useCallback(async () => {
    setAcquiring(true);
    setError('');
    try {
      const pos = await Location.getCurrentPositionAsync({ accuracy });
      applyPosition(pos);
    } catch (e) {
      setError(e.message || 'Could not get location');
    } finally {
      setAcquiring(false);
    }
  }, [accuracy, applyPosition]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (cancelled) return;
        if (status !== 'granted') {
          setError('Location permission is required to file a complaint.');
          setAcquiring(false);
          return;
        }
        setPermissionGranted(true);

        // Initial fix
        const pos = await Location.getCurrentPositionAsync({ accuracy });
        if (!cancelled) {
          applyPosition(pos);
          setAcquiring(false);
        }

        // Optional continuous watch
        if (watch && !cancelled) {
          watchRef.current = await Location.watchPositionAsync(
            {
              accuracy,
              distanceInterval: WATCHER_DISTANCE_M,
              timeInterval: WATCHER_INTERVAL_MS,
            },
            (p) => {
              if (!cancelled) applyPosition(p);
            },
          );
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message || 'Location error');
          setAcquiring(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      stopWatch();
    };
  }, [accuracy, watch, applyPosition, stopWatch]);

  return {
    coords,
    error,
    permissionGranted,
    acquiring,
    refresh,
  };
}
