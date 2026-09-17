/**
 * useComplaints — manages the list of complaints filed by the current user.
 *
 * Features:
 *  - Loads complaints from the API on mount.
 *  - Exposes `refresh` for pull-to-refresh.
 *  - Tracks `loading`, `refreshing`, and `error` states independently.
 *  - Maintains local optimistic state: after filing a new complaint the hook
 *    prepends it to the list so the user sees it immediately.
 *  - `filter` lets callers restrict the visible set by status or search text.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';

/** Status values returned by the server, normalised to lowercase. */
export const STATUS_ALL = 'all';
export const STATUS_OPEN = 'open';
export const STATUS_IN_PROGRESS = 'in_progress';
export const STATUS_RESOLVED = 'resolved';
export const STATUS_REJECTED = 'rejected';

export const KNOWN_STATUSES = [STATUS_ALL, STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_REJECTED];

/**
 * @param {object} opts
 * @param {string} [opts.statusFilter]  One of KNOWN_STATUSES; default 'all'.
 * @param {string} [opts.searchQuery]   Free-text filter across tracking_number / defect_type.
 */
export function useComplaints({ statusFilter = STATUS_ALL, searchQuery = '', disabled = false } = {}) {
  const [complaints, setComplaints] = useState([]);
  const [loading, setLoading] = useState(!disabled);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  // Initialised false; set true after first render so async callbacks can
  // safely check whether the component is still mounted before updating state.
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError('');
    try {
      const out = await api.trackComplaints();
      if (mountedRef.current) {
        setComplaints(out.complaints || []);
      }
    } catch (e) {
      if (mountedRef.current) {
        setError(e.message || 'Failed to load complaints');
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = useCallback(() => {
    setRefreshing(true);
    load({ silent: true });
  }, [load]);

  /** Optimistically prepend a newly filed complaint to the top of the list. */
  const prependOptimistic = useCallback((complaint) => {
    setComplaints((prev) => [complaint, ...prev]);
  }, []);

  /** Remove a complaint by id (e.g. after a failed optimistic insert). */
  const removeById = useCallback((id) => {
    setComplaints((prev) => prev.filter((c) => c.id !== id));
  }, []);

  /** Update a single complaint in-place by id. */
  const updateById = useCallback((id, patch) => {
    setComplaints((prev) =>
      prev.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    );
  }, []);

  useEffect(() => {
    if (disabled) {
      setLoading(false);
      return;
    }
    load();
  }, [load, disabled]);

  const filtered = useMemo(() => {
    let list = complaints;

    if (statusFilter && statusFilter !== STATUS_ALL) {
      list = list.filter(
        (c) => String(c.status || '').toLowerCase() === statusFilter,
      );
    }

    const q = searchQuery.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (c) =>
          String(c.tracking_number || '').toLowerCase().includes(q) ||
          String(c.defect_type || '').toLowerCase().includes(q) ||
          String(c.description || '').toLowerCase().includes(q),
      );
    }

    return list;
  }, [complaints, statusFilter, searchQuery]);

  return {
    complaints: filtered,
    allComplaints: complaints,
    loading,
    refreshing,
    error,
    refresh,
    prependOptimistic,
    removeById,
    updateById,
  };
}
