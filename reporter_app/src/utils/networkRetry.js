/**
 * networkRetry.js — exponential back-off retry for API calls.
 *
 * Features:
 *  - Configurable max attempts, base delay, and max delay.
 *  - Jitter to avoid thundering-herd.
 *  - Respects a signal/cancelled flag so unmounted components can abort.
 *  - Non-retriable status codes (4xx except 429) are not retried.
 *  - Provides a useRetry React hook for components that need retry UI state.
 */
import { useCallback, useRef, useState } from 'react';

// ---------------------------------------------------------------------------
// Core retry logic (no React dependency)
// ---------------------------------------------------------------------------

const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_BASE_DELAY_MS = 1000;
const DEFAULT_MAX_DELAY_MS = 15000;

/** Status codes that should NOT be retried. */
const NON_RETRIABLE_STATUSES = new Set([400, 401, 403, 404, 409, 422]);

function isRetriable(error) {
  if (error && error.status != null) {
    return !NON_RETRIABLE_STATUSES.has(error.status);
  }
  // Network errors, timeouts — retriable
  return true;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function backoffDelay(attempt, baseMs, maxMs) {
  const exp = baseMs * Math.pow(2, attempt - 1);
  const jitter = Math.random() * baseMs;
  return Math.min(exp + jitter, maxMs);
}

/**
 * Retries `fn` up to `maxAttempts` times with exponential back-off.
 *
 * @param {() => Promise<any>} fn            The async function to call.
 * @param {object}             [opts]
 * @param {number}             [opts.maxAttempts=3]
 * @param {number}             [opts.baseDelayMs=1000]
 * @param {number}             [opts.maxDelayMs=15000]
 * @param {{ cancelled: boolean }} [opts.cancelRef]  Set .cancelled = true to abort.
 * @param {(attempt: number, error: Error) => void} [opts.onRetry]
 * @returns {Promise<any>}
 */
export async function withRetry(fn, {
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  baseDelayMs = DEFAULT_BASE_DELAY_MS,
  maxDelayMs = DEFAULT_MAX_DELAY_MS,
  cancelRef = null,
  onRetry = null,
} = {}) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (cancelRef && cancelRef.cancelled) {
      throw Object.assign(new Error('Cancelled'), { cancelled: true });
    }
    try {
      return await fn();
    } catch (e) {
      lastError = e;
      if (!isRetriable(e)) throw e;
      if (attempt === maxAttempts) throw e;
      if (onRetry) onRetry(attempt, e);
      const wait = backoffDelay(attempt, baseDelayMs, maxDelayMs);
      await delay(wait);
    }
  }
  throw lastError;
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

/**
 * useRetry — wraps an async operation with retry state for UI feedback.
 *
 * @param {object} [opts]
 * @param {number} [opts.maxAttempts=3]
 * @param {number} [opts.baseDelayMs=1000]
 * @param {number} [opts.maxDelayMs=15000]
 *
 * @returns {{
 *   run: (fn: () => Promise<any>) => Promise<any>,
 *   attempt: number,
 *   maxAttempts: number,
 *   retrying: boolean,
 *   error: string,
 *   clearError: () => void,
 * }}
 */
export function useRetry({
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  baseDelayMs = DEFAULT_BASE_DELAY_MS,
  maxDelayMs = DEFAULT_MAX_DELAY_MS,
} = {}) {
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState('');
  const cancelRef = useRef({ cancelled: false });

  const clearError = useCallback(() => setError(''), []);

  const run = useCallback(
    async (fn) => {
      cancelRef.current = { cancelled: false };
      setAttempt(0);
      setRetrying(false);
      setError('');

      try {
        const result = await withRetry(fn, {
          maxAttempts,
          baseDelayMs,
          maxDelayMs,
          cancelRef: cancelRef.current,
          onRetry: (a) => {
            setAttempt(a);
            setRetrying(true);
          },
        });
        setRetrying(false);
        setAttempt(0);
        return result;
      } catch (e) {
        setRetrying(false);
        if (!e.cancelled) {
          setError(e.message || 'Request failed');
        }
        throw e;
      }
    },
    [maxAttempts, baseDelayMs, maxDelayMs],
  );

  // Cancel on unmount
  const cancel = useCallback(() => {
    cancelRef.current.cancelled = true;
  }, []);

  return { run, attempt, maxAttempts, retrying, error, clearError, cancel };
}
