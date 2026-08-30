import { useEffect, useRef } from "react";

/**
 * Call onLogout after `idleMs` with no pointer/keyboard/scroll activity.
 * Default 2 minutes — matches server SESSION_IDLE_TIMEOUT_S.
 */
export default function useIdleLogout(onLogout, idleMs = 120_000, enabled = true) {
  const lastRef = useRef(Date.now());
  const onLogoutRef = useRef(onLogout);
  onLogoutRef.current = onLogout;

  useEffect(() => {
    if (!enabled || idleMs <= 0) return undefined;

    const bump = () => {
      lastRef.current = Date.now();
    };
    const events = ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "wheel"];
    for (const ev of events) {
      window.addEventListener(ev, bump, { passive: true });
    }
    window.addEventListener("smartroad:activity", bump);
    // First paint counts as activity.
    bump();

    const tick = setInterval(() => {
      if (Date.now() - lastRef.current >= idleMs) {
        onLogoutRef.current?.();
      }
    }, 5_000);

    return () => {
      clearInterval(tick);
      for (const ev of events) {
        window.removeEventListener(ev, bump);
      }
      window.removeEventListener("smartroad:activity", bump);
    };
  }, [enabled, idleMs]);
}
