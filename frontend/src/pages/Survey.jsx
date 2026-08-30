import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../App";
import { useAsync } from "../hooks/useAsync";
import PageShell from "../components/PageShell";
import CardHeader from "../components/CardHeader";
import RoadSurveyMap from "../components/RoadSurveyMap";
import { MotionCard } from "../components/PageTransition";
import { todayIST, STATE_META } from "../data/surveyConstants";
import { roadSegmentDisplay } from "../utils/roadSegmentLabel";
import RoadNetworkLegend from "../components/RoadNetworkLegend";
import { fetchSegmentsCached } from "../utils/segmentCache";
import Modal from "../components/Modal";
import "./Survey.css";
function dedupeGeocodeHits(hits) {
  const seen = new Set();
  return (hits || []).filter((h) => {
    const name = (h.display_name || "").split(",")[0].trim().toLowerCase();
    const dist = String(h.district_name || h.district_id || "").toLowerCase();
    const key = `${name}|${dist}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function geocodeHitLabel(h) {
  const name = (h.display_name || "").trim();
  const dist = (h.district_name || "").trim();
  if (!dist || dist.toLowerCase() === name.toLowerCase()) return name;
  return `${name} · ${dist}`;
}

function AssignmentSummaryBanner({ summary, variant }) {
  if (!summary) return null;
  const isCustom = summary.mode === "auto_track";
  if (!summary.segment_count && !isCustom) return null;
  const isExisting = variant === "existing" || variant === "loaded";
  const districtNote = (summary.district_names || []).length > 1
    ? summary.district_names.join(" · ")
    : null;
  return (
    <div
      className={`survey-assignment-banner${isExisting ? " survey-assignment-banner-existing" : ""}${summary.over_target ? " survey-assignment-banner-over" : ""}`}
      role="status"
    >
      <div className="survey-assignment-banner-row">
        <span className="survey-assignment-swatch" aria-hidden />
        <div>
          <strong className="survey-assignment-banner-title">
            {isCustom
              ? (isExisting ? "Custom route for today" : "Custom route assigned")
              : (isExisting ? "Your assignment for today" : "Assignment generated")}
          </strong>
          <p className="survey-assignment-banner-stats">
            {isCustom
              ? `Free-drive · ~${summary.total_km || 0} km straight-line`
              : `${summary.segment_count} segments · ${summary.total_km} km`}
            <span className="survey-assignment-target"> (target {summary.target_km} km)</span>
            {summary.covered_km != null && (
              <span> · covered {summary.covered_km} km</span>
            )}
          </p>
          {districtNote && (
            <p className="survey-assignment-banner-note">Districts today: {districtNote}</p>
          )}
          {summary.start?.label && summary.end?.label && (
            <p className="survey-assignment-banner-note">
              {isCustom ? "Custom" : "Corridor"}: {summary.start.label} → {summary.end.label}
            </p>
          )}
          {isCustom && (
            <p className="survey-assignment-banner-note">
              Drive any path between start and end, then Capture + upload. Video GPS must match your trail.
            </p>
          )}
          {summary.open_assignment && !summary.can_assign && (
            <p className="survey-assignment-banner-note survey-assignment-over-note">
              {summary.open_assignment.message}
            </p>
          )}
          {summary.is_carryover && summary.assignment_date && (
            <p className="survey-assignment-banner-note survey-assignment-over-note">
              Showing incomplete assignment from {summary.assignment_date} — finish this quota before a new day route.
            </p>
          )}
          {summary.quota_incomplete && !isCustom && (
            <p className="survey-assignment-banner-note survey-assignment-over-note">
              Quota incomplete — travel at least {summary.target_km} km today (no penalty).
            </p>
          )}
          {isExisting && !isCustom && (
            <p className="survey-assignment-banner-note">
              Already generated for today — indigo on the map = your assignment.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Survey() {
  const { user, refresh } = useAuth();
  const stateId = user?.state_id || null;
  const assignedDistrictIds = useMemo(() => {
    const ids = user?.district_ids?.length
      ? user.district_ids
      : (user?.district_id != null ? [user.district_id] : []);
    return ids.map(String);
  }, [user?.district_ids, user?.district_id]);
  const homeStateKey = stateId === 2 ? "telangana" : stateId === 1 ? "andhra" : null;
  const userStateKeys = useMemo(() => {
    if (user?.state_keys?.length) return user.state_keys;
    return homeStateKey ? [homeStateKey] : [];
  }, [user?.state_keys, homeStateKey]);
  const multiState = userStateKeys.length > 1;
  const stateMeta = homeStateKey ? STATE_META[homeStateKey] : null;

  const [districtId, setDistrictId] = useState(assignedDistrictIds[0] || "");
  const [mapStateKey, setMapStateKey] = useState(homeStateKey);
  const [districtsModalOpen, setDistrictsModalOpen] = useState(false);
  const [districtDraft, setDistrictDraft] = useState(() => new Set(assignedDistrictIds));
  const [districtSearch, setDistrictSearch] = useState("");
  const [districtPage, setDistrictPage] = useState(0);
  const [districtSaving, setDistrictSaving] = useState(false);
  const [clearingSurvey, setClearingSurvey] = useState(false);
  const [clearModalOpen, setClearModalOpen] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [msg, setMsg] = useState("");
  const [msgKind, setMsgKind] = useState(null);
  const [assignVariant, setAssignVariant] = useState("loaded");
  const [startQuery, setStartQuery] = useState("");
  const [endQuery, setEndQuery] = useState("");
  const [startHit, setStartHit] = useState(null);
  const [endHit, setEndHit] = useState(null);
  const [startHits, setStartHits] = useState([]);
  const [endHits, setEndHits] = useState([]);
  const [geoBusy, setGeoBusy] = useState(null);
  const [routeOptions, setRouteOptions] = useState([]);
  const [selectedRouteId, setSelectedRouteId] = useState(null);
  /** When true, show start/end route editor even if today already has an assignment. */
  const [editingRoute, setEditingRoute] = useState(false);
  /** Sticky: fetch MDR/local once zoom ≥ 10; keep thereafter (Leaflet hides by zoom). */
  const [localRoadsUnlocked, setLocalRoadsUnlocked] = useState(false);
  const [customModalOpen, setCustomModalOpen] = useState(false);

  useEffect(() => {
    if (!assignedDistrictIds.length) return;
    if (!assignedDistrictIds.includes(String(districtId))) {
      setDistrictId(assignedDistrictIds[0]);
      setLocalRoadsUnlocked(false);
    }
  }, [assignedDistrictIds, districtId]);

  const clearRouteOptions = useCallback(() => {
    setRouteOptions([]);
    setSelectedRouteId(null);
  }, []);

  useEffect(() => {
    clearRouteOptions();
  }, [startHit, endHit, clearRouteOptions]);

  const { data: settings } = useAsync(() => api.surveySettings(), []);
  const { data: apDistricts } = useAsync(() => api.surveyDistricts("andhra"), []);
  const { data: tgDistricts } = useAsync(() => api.surveyDistricts("telangana"), []);
  const districts = useMemo(
    () => [...(apDistricts || []), ...(tgDistricts || [])],
    [apDistricts, tgDistricts],
  );
  const myDistricts = useMemo(
    () => districts.filter((d) => assignedDistrictIds.includes(String(d.district_id))),
    [districts, assignedDistrictIds],
  );
  const pickerDistricts = useMemo(() => {
    // VG can pick any district in their home state(s); multi-state accounts see both catalogs.
    const keys = userStateKeys.length ? userStateKeys : (homeStateKey ? [homeStateKey] : ["andhra", "telangana"]);
    return districts.filter((d) => keys.includes(d.state_key));
  }, [districts, userStateKeys, homeStateKey]);
  const filteredPickerDistricts = useMemo(() => {
    const q = districtSearch.trim().toLowerCase();
    if (!q) return pickerDistricts;
    return pickerDistricts.filter((d) => {
      const name = String(d.name || "").toLowerCase();
      const id = String(d.district_id || "");
      return name.includes(q) || id.includes(q);
    });
  }, [pickerDistricts, districtSearch]);
  const DISTRICT_PAGE_SIZE = 8;
  const districtPageCount = Math.max(1, Math.ceil(filteredPickerDistricts.length / DISTRICT_PAGE_SIZE));
  const districtSafePage = Math.min(districtPage, districtPageCount - 1);
  const districtPageRows = filteredPickerDistricts.slice(
    districtSafePage * DISTRICT_PAGE_SIZE,
    districtSafePage * DISTRICT_PAGE_SIZE + DISTRICT_PAGE_SIZE,
  );

  useEffect(() => {
    if (!districtsModalOpen) return;
    setDistrictDraft(new Set(assignedDistrictIds));
    setDistrictSearch("");
    setDistrictPage(0);
  }, [districtsModalOpen, assignedDistrictIds]);

  useEffect(() => {
    setDistrictPage(0);
  }, [districtSearch]);

  const toggleDistrictDraft = (did) => {
    const id = String(did);
    setDistrictDraft((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const saveDistricts = async () => {
    const ids = [...districtDraft].map(String);
    if (!ids.length) {
      setMsg("Keep at least one district selected.");
      setMsgKind("error");
      return;
    }
    setDistrictSaving(true);
    setMsg("");
    try {
      await api.updateMyDistricts(ids.map(Number));
      await refresh?.();
      setDistrictsModalOpen(false);
      setMsg(`Saved ${ids.length} district${ids.length === 1 ? "" : "s"}.`);
      setMsgKind("ok");
    } catch (e) {
      setMsg(e.message || "Could not update districts");
      setMsgKind("error");
    } finally {
      setDistrictSaving(false);
    }
  };

  const clearSurvey = async () => {
    setClearingSurvey(true);
    setMsg("");
    try {
      const res = await api.clearMySurveyAssignment();
      if (res?.cleared === false) {
        throw new Error(res?.message || "No survey assignment to clear");
      }
      setClearModalOpen(false);
      setEditingRoute(false);
      clearRouteOptions();
      setStartHit(null);
      setEndHit(null);
      setStartQuery("");
      setEndQuery("");
      await reload();
      await reloadAssignGeo();
      setMsg(res?.message || "Survey assignment cleared.");
      setMsgKind("ok");
    } catch (e) {
      setMsg(e.message || "Could not clear survey");
      setMsgKind("error");
    } finally {
      setClearingSurvey(false);
    }
  };

  const districtName = useMemo(
    () => (myDistricts.find((d) => String(d.district_id) === String(districtId))?.name)
      || districts.find((d) => String(d.district_id) === String(districtId))?.name,
    [myDistricts, districts, districtId],
  );
  const stateKey = mapStateKey || homeStateKey;

  useEffect(() => {
    if (homeStateKey && !mapStateKey) setMapStateKey(homeStateKey);
  }, [homeStateKey, mapStateKey]);

  // When active district changes, sync map state from that district's GIS state
  useEffect(() => {
    const d = myDistricts.find((x) => String(x.district_id) === String(districtId));
    if (d?.state_key) setMapStateKey(d.state_key);
  }, [districtId, myDistricts]);

  const { data: assignmentData, loading, error, reload } = useAsync(
    () => api.surveyAssignments(),
    [],
  );

  // This VG's indigo corridor + grey GPS cover (DB trail) — not district-wide sealed history
  const { data: assignGeo, reload: reloadAssignGeo } = useAsync(
    () => api.surveyAssignmentGeoJson(),
    [
      assignmentData?.summary?.assignment_date,
      assignmentData?.summary?.date,
      assignmentData?.summary?.covered_km,
      assignmentData?.summary?.segment_count,
      assignmentData?.summary?.mode,
      assignmentData?.summary?.completed,
      assignmentData?.summary?.assignment_complete,
    ],
  );

  const onMapZoom = useCallback((z) => {
    if (z >= 10) setLocalRoadsUnlocked(true);
  }, []);

  // District NH/SH GeoJSON is only needed when picking/editing a route — not on
  // every Survey open (that endpoint was dominating page load + server CPU).
  const hasAssignedRoute = Boolean(
    (assignmentData?.summary?.segment_count ?? 0) > 0
      || assignmentData?.summary?.mode === "auto_track",
  );
  const wantDistrictGis = Boolean(
    stateKey && districtId && (editingRoute || !hasAssignedRoute || localRoadsUnlocked),
  );
  const loadLocalRoads = Boolean(wantDistrictGis && localRoadsUnlocked);

  const { data: primarySeg, loading: loadingPrimary, error: primaryErr, reload: reloadPrimary } = useAsync(
    () => (wantDistrictGis ? fetchSegmentsCached(api, stateKey, districtId, "nh,sh") : Promise.resolve(null)),
    [stateKey, districtId, wantDistrictGis],
  );
  const { data: secondarySeg, loading: loadingSecondary, error: secondaryErr, reload: reloadSecondary } = useAsync(
    () => (loadLocalRoads ? fetchSegmentsCached(api, stateKey, districtId, "mdr,other") : Promise.resolve(null)),
    [stateKey, districtId, loadLocalRoads],
  );

  // Map uses assignGeo only; district GIS is optional background for editors.
  const loadingSeg = wantDistrictGis
    && ((loadingPrimary && !primarySeg) || (loadingSecondary && !secondarySeg && !primarySeg));
  const segError = primaryErr || secondaryErr;
  const reloadSeg = useCallback(() => {
    reloadPrimary();
    reloadSecondary();
    reloadAssignGeo();
  }, [reloadPrimary, reloadSecondary, reloadAssignGeo]);

  const assignedIds = useMemo(
    () => new Set((assignmentData?.assignments || []).map((a) => a.id)),
    [assignmentData],
  );

  /**
   * VG Survey map: only THIS videographer's route.
   * Indigo = assigned corridor (from assignments/geojson, Postgres).
   * Grey = GPS they drove start→end (tracking_trail_points) — never other VGs' sealed OSM history.
   */
  const displayFeatures = useMemo(() => {
    const own = assignGeo?.features || [];
    const hasOwnRoute = own.some((f) => {
      const k = f?.properties?.route_kind;
      return k === "continuous" || k === "auto_track" || k === "covered" || k === "segment" || k === "endpoint";
    });
    if (hasOwnRoute) return own;
    // Picking / no assignment yet: keep basemap clear (route cards use overlays)
    if (routeOptions.length > 0) return [];
    return [];
  }, [assignGeo, routeOptions.length]);

  const summary = assignmentData?.summary;
  const targetKm = settings?.daily_km ?? summary?.target_km ?? 100;
  const focusClass = settings?.focus_road_class || "all";
  const focusLabel = focusClass === "all"
    ? "All (NH → SH → MDR → Local)"
    : focusClass.split(",").map((c) => ({
      nh: "NH", sh: "SH", mdr: "MDR", other: "Local",
    }[c] || c)).join(" + ");
  const hasAssignment = (summary?.segment_count ?? 0) > 0 || summary?.mode === "auto_track";
  const canAssignNew = summary?.can_assign !== false;
  const openBlockMsg = summary?.open_assignment?.message;

  // Only sync district from summary when user hasn't multi-district choice active
  useEffect(() => {
    if (assignedDistrictIds.length <= 1 && summary?.district_id) {
      setDistrictId(String(summary.district_id));
    }
  }, [summary?.district_id, assignedDistrictIds.length]);

  const applyAccessCheck = useCallback(async (lat, lon) => {
    if (!homeStateKey && !userStateKeys.length) return { ok: true };
    try {
      const chk = await api.surveyLocate(lat, lon, { stateKey: mapStateKey || homeStateKey });
      if (chk?.located?.state_key) {
        setMapStateKey(chk.located.state_key);
      }
      if (chk?.located?.district_id && assignedDistrictIds.includes(String(chk.located.district_id))) {
        setDistrictId(String(chk.located.district_id));
      }
      if (!chk?.in_scope && chk?.message) {
        setMsg(chk.message);
        setMsgKind("error");
      } else if (chk?.located?.district_name) {
        setMsg(`Located in ${chk.located.district_name}.`);
        setMsgKind("success");
      }
      return chk;
    } catch {
      return { ok: true };
    }
  }, [homeStateKey, mapStateKey, userStateKeys.length, assignedDistrictIds]);

  const acceptHit = useCallback(async (which, h) => {
    if (h && h.access_ok === false) {
      setMsg(h.access_message || "You do not have access to that place.");
      setMsgKind("error");
      return false;
    }
    let loc = {
      district_id: h?.district_id,
      district_name: h?.district_name,
      state_key: h?.state_key,
    };
    // Coords / My location already annotated by /geocode — skip a second locate round-trip.
    const prechecked = h?.access_ok === true && h?.district_id;
    if (!prechecked) {
      const chk = await applyAccessCheck(h.lat, h.lon);
      if (chk && chk.in_scope === false) {
        if (which === "start") {
          setStartHit(null);
        } else {
          setEndHit(null);
        }
        return false;
      }
      if (chk?.located) loc = chk.located;
    } else if (h?.district_name) {
      setMsg(`Located in ${h.district_name}.`);
      setMsgKind("success");
    }
    if (which === "start") {
      setStartHit(h);
      setStartQuery(h.display_name);
      setStartHits([]);
    } else {
      setEndHit(h);
      setEndQuery(h.display_name);
      setEndHits([]);
    }
    if (loc.district_id && assignedDistrictIds.includes(String(loc.district_id))) {
      setDistrictId(String(loc.district_id));
    }
    if (loc.state_key) setMapStateKey(loc.state_key);
    return true;
  }, [applyAccessCheck, assignedDistrictIds]);

  const lookupPlace = useCallback(async (which) => {
    const q = which === "start" ? startQuery : endQuery;
    if (!q.trim()) return;
    setGeoBusy(which);
    setMsg("");
    setMsgKind(null);
    try {
      const res = await api.surveyGeocode(q.trim(), {
        stateKey: mapStateKey || homeStateKey,
        districtIds: assignedDistrictIds,
      });
      const hits = dedupeGeocodeHits(res.results || []);
      if (!hits.length) {
        setMsg(`No place or road found for “${q}”. Try a clearer landmark, road name, or lat,lon.`);
        setMsgKind("error");
        if (which === "start") setStartHits([]);
        else setEndHits([]);
        return;
      }
      if (hits.length === 1) {
        await acceptHit(which, hits[0]);
        return;
      }
      if (which === "start") {
        setStartHits(hits);
        setStartHit(null);
      } else {
        setEndHits(hits);
        setEndHit(null);
      }
    } catch (e) {
      setMsg(e.message);
      setMsgKind("error");
    } finally {
      setGeoBusy(null);
    }
  }, [startQuery, endQuery, mapStateKey, homeStateKey, assignedDistrictIds, acceptHit]);

  const pickHit = useCallback(async (which, h) => {
    await acceptHit(which, h);
  }, [acceptHit]);

  const useMyLocation = useCallback((which = "start") => {
    if (!navigator.geolocation) {
      setMsg("Geolocation not available in this browser. Use HTTPS or localhost, or paste lat,lon.");
      setMsgKind("error");
      return;
    }
    setGeoBusy(which);
    setMsg("Getting your location…");
    setMsgKind(null);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        try {
          // Same path as pasting lat,lon — reverse place/road name + district access.
          const res = await api.surveyGeocode(`${lat},${lon}`, {
            stateKey: mapStateKey || homeStateKey,
            districtIds: assignedDistrictIds,
          });
          const hit = (res.results || [])[0];
          if (!hit) {
            setMsg("Could not resolve your GPS fix.");
            setMsgKind("error");
            setGeoBusy(null);
            return;
          }
          await acceptHit(which, hit);
        } catch (e) {
          setMsg(e.message || "Could not resolve your location");
          setMsgKind("error");
        } finally {
          setGeoBusy(null);
        }
      },
      (err) => {
        const code = err?.code;
        let hint = err.message || "Could not get your location";
        if (code === 1) hint = "Location permission denied — allow location for this site, or paste lat,lon.";
        if (code === 2) hint = "Location unavailable — try again outdoors / with GPS on, or paste lat,lon.";
        if (code === 3) hint = "Location timed out — try again, or paste lat,lon.";
        setMsg(hint);
        setMsgKind("error");
        setGeoBusy(null);
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    );
  }, [acceptHit, assignedDistrictIds, homeStateKey, mapStateKey]);

  const handleAssignResult = useCallback((res) => {
    if (res.already_complete) {
      setMsg(res.message);
      setMsgKind("existing");
      setAssignVariant("existing");
    } else if (res.already_exists && !res.replaced) {
      setMsg(res.message);
      setMsgKind("existing");
      setAssignVariant("existing");
    } else {
      const rem = res.remaining_km != null ? ` · ${res.remaining_km} km left to target` : "";
      const warn = res.overlap_warning?.message
        ? ` Note: ${res.overlap_warning.message}`
        : "";
      const head = res.replaced
        ? `Route updated — ${res.assigned_count} segments (${res.total_km} km)`
        : res.leg_added
          ? `Added leg: ${res.assigned_count} segments (${res.leg_km ?? res.total_km} km). Total today ${res.total_km} km`
          : `Assigned ${res.assigned_count} segments (${res.total_km} km)`;
      setMsg(head + rem + (res.connector_km > 0 ? ` · ${res.connector_km} km connectors to bridge gaps.` : ".") + warn);
      setMsgKind(res.overlap_warning ? "existing" : "success");
      setAssignVariant("created");
      setEditingRoute(false);
      setStartHit(null);
      setEndHit(null);
      setStartQuery("");
      setEndQuery("");
      clearRouteOptions();
    }
    reload();
    reloadSeg();
  }, [reload, reloadSeg, clearRouteOptions]);

  const findRoutes = useCallback(async () => {
    if (!startHit || !endHit) {
      setMsg("Set start and end places first (Find, My location, or lat,lon).");
      setMsgKind("error");
      return;
    }
    setPreviewing(true);
    setMsg("");
    setMsgKind(null);
    clearRouteOptions();
    try {
      const res = await api.previewSurveyRoutes({
        start_lat: startHit.lat,
        start_lon: startHit.lon,
        end_lat: endHit.lat,
        end_lon: endHit.lon,
        start_label: startHit.display_name || startQuery,
        end_label: endHit.display_name || endQuery,
        district_id: districtId,
      });
      const routes = (res.routes || []).slice(0, 4);
      if (!routes.length) {
        setMsg("No road routes found between those points in your districts.");
        setMsgKind("error");
        return;
      }
      setRouteOptions(routes);
      setSelectedRouteId(routes[0].id);
      setMsg(`Found ${routes.length} route option${routes.length === 1 ? "" : "s"} — pick one to assign.`);
      setMsgKind("success");
    } catch (e) {
      setMsg(e?.data?.error || e?.data?.message || e?.message);
      setMsgKind("error");
    } finally {
      setPreviewing(false);
    }
  }, [startHit, endHit, startQuery, endQuery, districtId, clearRouteOptions]);

  const assignCustomRoute = useCallback(async () => {
    if (!startHit || !endHit) {
      setMsg("Set start and end places first.");
      setMsgKind("error");
      setCustomModalOpen(false);
      return;
    }
    setAssigning(true);
    setMsg("");
    setMsgKind(null);
    setCustomModalOpen(false);
    try {
      const res = await api.generateMySurveyAssignment({
        mode: "auto_track",
        start_lat: startHit.lat,
        start_lon: startHit.lon,
        end_lat: endHit.lat,
        end_lon: endHit.lon,
        start_label: startHit.display_name || startQuery,
        end_label: endHit.display_name || endQuery,
        district_id: districtId,
      });
      handleAssignResult(res);
      setMsg(
        (res.message || "Custom route assigned.") + " Go to Capture to record your drive.",
      );
      setMsgKind("success");
    } catch (e) {
      setMsg(e?.data?.error || e?.data?.message || e?.message);
      setMsgKind("error");
    } finally {
      setAssigning(false);
    }
  }, [startHit, endHit, startQuery, endQuery, districtId, handleAssignResult]);

  const assignSelectedRoute = useCallback(async () => {
    const route = routeOptions.find((r) => r.id === selectedRouteId);
    if (!startHit || !endHit) {
      setMsg("Set start and end places first.");
      setMsgKind("error");
      return;
    }
    if (!route?.segment_ids?.length) {
      setMsg("Select a route option first.");
      setMsgKind("error");
      return;
    }
    setAssigning(true);
    setMsg("");
    setMsgKind(null);
    try {
      const res = await api.generateMySurveyAssignment({
        mode: "corridor",
        start_lat: startHit.lat,
        start_lon: startHit.lon,
        end_lat: endHit.lat,
        end_lon: endHit.lon,
        start_label: startHit.display_name || startQuery,
        end_label: endHit.display_name || endQuery,
        district_id: districtId,
        segment_ids: route.segment_ids,
        replace: true,
        polyline: route.polyline || null,
      });
      handleAssignResult(res);
    } catch (e) {
      setMsg(e?.data?.error || e?.data?.message || e?.message);
      setMsgKind("error");
    } finally {
      setAssigning(false);
    }
  }, [routeOptions, selectedRouteId, startHit, endHit, startQuery, endQuery, districtId, handleAssignResult]);

  const runNearest = useCallback(async () => {
    setAssigning(true);
    setMsg("");
    setMsgKind(null);
    try {
      const body = { mode: "nearest", district_id: districtId };
      if (startHit?.lat != null) {
        body.lat = startHit.lat;
        body.lon = startHit.lon;
      }
      const res = await api.generateMySurveyAssignment(body);
      handleAssignResult(res);
    } catch (e) {
      setMsg(e?.data?.error || e?.data?.message || e?.message);
      setMsgKind("error");
    } finally {
      setAssigning(false);
    }
  }, [districtId, startHit, handleAssignResult]);

  const markers = useMemo(() => {
    const out = [];
    if (startHit?.lat != null) out.push({ lat: startHit.lat, lon: startHit.lon, label: "Start", kind: "start" });
    if (endHit?.lat != null) out.push({ lat: endHit.lat, lon: endHit.lon, label: "End", kind: "end" });
    const route = routeOptions.find((r) => r.id === selectedRouteId);
    const steps = route?.steps || [];
    for (const s of steps) {
      if (s?.lat == null || s?.lon == null) continue;
      const t = String(s.type || "");
      if (t === "depart" || t === "arrive") continue;
      const m = String(s.modifier || "");
      const isTurn = t === "turn" || t.includes("ramp") || m.includes("uturn") || m.includes("u turn")
        || t === "roundabout" || t === "fork" || t === "end of road";
      if (!isTurn) continue;
      out.push({
        lat: s.lat,
        lon: s.lon,
        label: s.instruction || "Maneuver",
        kind: "turn",
      });
    }
    return out;
  }, [startHit, endHit, routeOptions, selectedRouteId]);

  const routeOverlays = useMemo(() => {
    if (!routeOptions.length) return null;
    return routeOptions.map((r) => ({
      id: r.id,
      color: "#9aa0a6",
      activeColor: "#1a73e8",
      selected: r.id === selectedRouteId,
      features: r.geojson?.features || [],
      geojson: r.geojson,
    }));
  }, [routeOptions, selectedRouteId]);

  const selectedRoute = useMemo(
    () => routeOptions.find((r) => r.id === selectedRouteId) || null,
    [routeOptions, selectedRouteId],
  );

  const busy = assigning || previewing;
  const showRouteEditor = !hasAssignment || editingRoute;

  if (user?.is_admin || user?.is_dev_admin) return <Navigate to="/survey/admin" replace />;

  return (
    <PageShell
      title="Field survey"
      subtitle={summary?.date ? `${summary.date} · your daily route` : todayIST()}
      loading={(loading || loadingSeg) && !!districtId && !assignmentData}
      error={error || segError}
    >
      {!districtId && (
        <div className="alert alert-warn">
          No district is linked to your videographer account. Ask an admin to set your state + district(s) on Users.
        </div>
      )}

      {districtId && (
        <>
          <MotionCard className="card survey-toolbar" delay={0}>
            <CardHeader title="Today's corridor" />
            <div className="card-body">
              <div className="dashboard-geo-row" style={{ marginBottom: "0.75rem" }}>
                <label className="dashboard-geo-field">
                  <span className="label">State</span>
                  <select className="select select-sm" value={stateKey || ""} disabled>
                    <option value={stateKey || ""}>
                      {multiState
                        ? "Andhra + Telangana"
                        : (STATE_META[stateKey]?.name || stateMeta?.name || "—")}
                    </option>
                  </select>
                </label>
                <div className="dashboard-geo-field" style={{ flex: 1, display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.86rem" }}>
                    {assignedDistrictIds.length} district{assignedDistrictIds.length === 1 ? "" : "s"} active
                    {districtName ? ` · ${districtName}` : ""}
                  </p>
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ marginTop: "0.25rem" }}
                    onClick={() => setDistrictsModalOpen(true)}
                  >
                    Select districts
                  </button>
                </div>
              </div>
              {assignedDistrictIds.length > 1 && (
                <p className="muted" style={{ fontSize: "0.78rem", marginBottom: "0.65rem" }}>
                  Multi-district account — start/end can be in different districts you have access to.
                  Search may show places outside your access; choosing one shows a clear warning.
                </p>
              )}

              {hasAssignment && !showRouteEditor ? (
                <div className="survey-ready-capture">
                  <p style={{ margin: "0 0 0.75rem" }}>
                    Route ready for today: <strong>{summary?.total_km ?? 0}</strong>
                    {" / "}
                    {summary?.target_km ?? targetKm} km
                    {summary?.segment_count ? ` · ${summary.segment_count} segments` : ""}.
                    Next step is Capture — film the assigned roads.
                  </p>
                  <div className="survey-toolbar-actions">
                    <Link to="/capture" className="btn btn-primary btn-sm">
                      Go to Capture
                    </Link>
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={summary?.can_change_route === false}
                      title={
                        summary?.can_change_route === false
                          ? (summary?.change_route_message || "Complete the current assignment first")
                          : "Replace or remove today’s route"
                      }
                      onClick={() => {
                        if (summary?.can_change_route === false) {
                          setMsg(summary?.change_route_message || "Complete the current assignment before changing route.");
                          setMsgKind("error");
                          return;
                        }
                        setEditingRoute(true);
                      }}
                    >
                      Change route
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      style={{ color: "var(--danger, #b3261e)", borderColor: "var(--danger, #b3261e)" }}
                      disabled={clearingSurvey}
                      onClick={() => setClearModalOpen(true)}
                    >
                      Clear survey
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => { reload(); reloadAssignGeo(); reloadSeg(); }}>
                      Refresh
                    </button>
                  </div>
                  {summary?.can_change_route === false && summary?.change_route_message && (
                    <p className="muted" style={{ marginTop: "0.65rem", fontSize: "0.82rem", color: "var(--danger, #b3261e)" }}>
                      {summary.change_route_message}
                    </p>
                  )}
                </div>
              ) : (
                <>
                  <p className="muted" style={{ marginBottom: "0.75rem" }}>
                    {hasAssignment
                      ? "Change today’s path: set start and end, find routes, then assign."
                      : <>Daily target: <strong>{targetKm} km</strong> · Focus: <strong>{focusLabel}</strong>. Set start and end, find routes, then assign — Capture comes after.</>}
                  </p>

              <div className="survey-corridor-form">
                <div className="survey-corridor-row">
                  <input
                    className="input"
                    placeholder="Start — road/place name, then Find (or My location / lat,lon)"
                    value={startQuery}
                    onChange={(e) => { setStartQuery(e.target.value); setStartHit(null); setStartHits([]); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); lookupPlace("start"); } }}
                  />
                  <button type="button" className="btn btn-sm" onClick={() => lookupPlace("start")} disabled={geoBusy === "start"}>
                    {geoBusy === "start" ? "…" : "Find"}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => useMyLocation("start")} disabled={!!geoBusy}>
                    My location
                  </button>
                </div>
                {startHits.length > 0 && !startHit && (
                  <ul className="survey-place-hits">
                    {startHits.map((h) => (
                      <li key={`${h.lat},${h.lon},${h.display_name}`}>
                        <button type="button" className="survey-place-hit" onClick={() => pickHit("start", h)}>
                          {h.access_ok === false ? "[no access] " : h.source === "road_index" ? "[road] " : ""}
                          {geocodeHitLabel(h)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {startHit && <p className="muted survey-corridor-hit">Start: {startHit.display_name}</p>}
                <div className="survey-corridor-row">
                  <input
                    className="input"
                    placeholder="End — landmark, junction, road, or lat,lon"
                    value={endQuery}
                    onChange={(e) => { setEndQuery(e.target.value); setEndHit(null); setEndHits([]); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); lookupPlace("end"); } }}
                  />
                  <button type="button" className="btn btn-sm" onClick={() => lookupPlace("end")} disabled={geoBusy === "end"}>
                    {geoBusy === "end" ? "…" : "Find"}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => useMyLocation("end")} disabled={!!geoBusy}>
                    My location
                  </button>
                </div>
                {endHits.length > 0 && !endHit && (
                  <ul className="survey-place-hits">
                    {endHits.map((h) => (
                      <li key={`${h.lat},${h.lon},${h.display_name}`}>
                        <button type="button" className="survey-place-hit" onClick={() => pickHit("end", h)}>
                          {h.access_ok === false ? "[no access] " : h.source === "road_index" ? "[road] " : ""}
                          {geocodeHitLabel(h)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {endHit && <p className="muted survey-corridor-hit">End: {endHit.display_name}</p>}

                {routeOptions.length > 0 && (
                  <div className="survey-route-options" role="listbox" aria-label="Route options">
                    {routeOptions.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        role="option"
                        aria-selected={r.id === selectedRouteId}
                        className={`survey-route-option${r.id === selectedRouteId ? " is-selected" : ""}`}
                        onClick={() => setSelectedRouteId(r.id)}
                      >
                        <strong>{r.label || `Route · ${r.km} km`}</strong>
                        <span className="muted">
                          {r.duration_min_est != null ? `~${r.duration_min_est} min` : ""}
                          {r.district_ids?.length ? ` · ${r.district_ids.length} district${r.district_ids.length > 1 ? "s" : ""}` : ""}
                          {r.steps?.length ? ` · ${r.steps.length} steps` : ""}
                          {Number(r.already_covered_pct) > 0
                            ? ` · ${Number(r.already_covered_pct).toFixed(0)}% already covered — consider another route`
                            : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                {selectedRoute?.steps?.length > 0 && (
                  <ol className="survey-route-steps">
                    {selectedRoute.steps.map((s, i) => (
                      <li key={`${i}-${s.instruction}`}>
                        <span>{s.instruction}</span>
                        {s.distance_m > 0 && (
                          <span className="muted">
                            {s.distance_m >= 1000
                              ? `${(s.distance_m / 1000).toFixed(1)} km`
                              : `${s.distance_m} m`}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </div>

              <div className="survey-toolbar-actions">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={findRoutes}
                  disabled={busy || !startHit || !endHit || !canAssignNew}
                >
                  {previewing ? "Finding routes…" : "Find routes"}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setCustomModalOpen(true)}
                  disabled={busy || !startHit || !endHit || !canAssignNew}
                  title="Assign start→end only; drive any path; verify GPS vs video on upload"
                >
                  Custom route
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={assignSelectedRoute}
                  disabled={busy || !selectedRoute?.segment_ids?.length || !canAssignNew}
                >
                  {assigning ? "Assigning…" : "Assign selected route"}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={runNearest}
                  disabled={busy || !canAssignNew}
                  title="Finds nearest not-completed road and grows a continuous assignment"
                >
                  {assigning ? "…" : "Nearest available"}
                </button>
                {hasAssignment && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      setEditingRoute(false);
                      clearRouteOptions();
                    }}
                  >
                    Cancel
                  </button>
                )}
                {hasAssignment && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ color: "var(--danger, #b3261e)", borderColor: "var(--danger, #b3261e)" }}
                    disabled={clearingSurvey}
                    onClick={() => setClearModalOpen(true)}
                  >
                    Clear survey
                  </button>
                )}
                <button type="button" className="btn btn-sm" onClick={() => { reload(); reloadAssignGeo(); reloadSeg(); }}>Refresh</button>
              </div>
              {!canAssignNew && openBlockMsg && (
                <div className="alert alert-error" style={{ marginTop: "0.75rem", marginBottom: 0 }} role="alert">
                  {openBlockMsg}
                </div>
              )}
                </>
              )}
              {msg && msgKind === "existing" && (
                <div className="alert alert-survey-existing" style={{ marginTop: "0.75rem", marginBottom: 0 }} role="alert">
                  <strong>{msg}</strong> — your existing route is shown below.
                </div>
              )}
              {msg && msgKind === "success" && (
                <div className="alert alert-ok" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
                  {msg}{" "}
                  <Link to="/capture">Go to Capture →</Link>
                </div>
              )}
              {msg && msgKind === "error" && <div className="alert alert-error" style={{ marginTop: "0.75rem", marginBottom: 0 }}>{msg}</div>}
            </div>
          </MotionCard>

          <AssignmentSummaryBanner summary={summary} variant={assignVariant} />

          <MotionCard className="card" delay={0.1}>
            <CardHeader title={routeOptions.length ? "Route preview" : (hasAssignment ? "Today's assigned path" : "Map")} />
            <div className="card-body">
              <div className="survey-map-legend">
                <RoadNetworkLegend showAssignment showCompleted />
              </div>
              <RoadSurveyMap
                features={displayFeatures}
                center={(STATE_META[stateKey] || stateMeta)?.center}
                zoom={(STATE_META[stateKey] || stateMeta)?.zoom || 10}
                bounds={(STATE_META[stateKey] || stateMeta)?.bounds}
                fitToFeatures={displayFeatures.length > 0 || (routeOverlays?.length > 0)}
                fitKey={`survey-${stateKey || "x"}-${districtId || "x"}-r${routeOptions.length}-${selectedRouteId || "n"}-a${assignedIds.size}-g${displayFeatures.length}-c${summary?.covered_km ?? 0}`}
                detailMode
                height={480}
                assignmentSummary={summary}
                hideLegend
                onZoomChange={onMapZoom}
                markers={markers}
                routeOverlays={routeOverlays}
                onRouteSelect={setSelectedRouteId}
                emptyMessage="Set start & end, then Find routes — only your assigned path and covered GPS show here."
              />
            </div>
          </MotionCard>

          {hasAssignment ? (
            <MotionCard className="card" delay={0.15}>
              <CardHeader title="Assigned segments" />
              <div className="table-wrap table-template">
                <table>
                  <thead>
                    <tr>
                      <th>Road</th>
                      <th>District</th>
                      <th>Class</th>
                      <th>Km</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(assignmentData?.assignments || []).some((a) => a.name || a.ref || a.length_km > 0) ? (
                      (assignmentData?.assignments || []).map((a) => {
                        const { title } = roadSegmentDisplay(a);
                        return (
                          <tr key={a.id}>
                            <td>{title}</td>
                            <td>{a.district_name || a.district_id || "—"}</td>
                            <td>{a.road_class}</td>
                            <td>{a.length_km}</td>
                            <td>{a.status}</td>
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={5}>
                          Continuous corridor
                          {summary?.total_km ? ` · ${summary.total_km} km` : ""}
                          {summary?.segment_count ? ` · ${summary.segment_count} segments` : ""}
                          {" — see map above"}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </MotionCard>
          ) : null}
        </>
      )}

      <Modal
        open={districtsModalOpen}
        onClose={() => !districtSaving && setDistrictsModalOpen(false)}
        title="Select districts"
        className="modal-wide"
      >
        <div className="modal-body" style={{ paddingTop: 0 }}>
          <p className="muted" style={{ marginBottom: "0.75rem", fontSize: "0.86rem" }}>
            Choose the districts you survey in. Locating / My location only works inside these.
          </p>
          <input
            className="input"
            placeholder="Search districts…"
            value={districtSearch}
            onChange={(e) => setDistrictSearch(e.target.value)}
            disabled={districtSaving}
            style={{ marginBottom: "0.65rem" }}
          />
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", marginBottom: "0.65rem", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-sm"
              disabled={districtSaving || !filteredPickerDistricts.length}
              onClick={() => {
                setDistrictDraft((prev) => {
                  const next = new Set(prev);
                  filteredPickerDistricts.forEach((d) => next.add(String(d.district_id)));
                  return next;
                });
              }}
            >
              Select all
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={districtSaving}
              onClick={() => setDistrictDraft(new Set())}
            >
              Clear all
            </button>
            <span className="muted" style={{ fontSize: "0.82rem", marginLeft: "auto" }}>
              {districtDraft.size} selected
            </span>
          </div>
          <div className="table-wrap" style={{ maxHeight: 320, overflow: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 40 }} />
                  <th>District</th>
                  <th>State</th>
                  <th>LGD</th>
                </tr>
              </thead>
              <tbody>
                {districtPageRows.map((d) => {
                  const id = String(d.district_id);
                  const on = districtDraft.has(id);
                  return (
                    <tr
                      key={id}
                      style={{ cursor: "pointer", background: on ? "var(--accent-dim, #e8eef2)" : undefined }}
                      onClick={() => !districtSaving && toggleDistrictDraft(id)}
                    >
                      <td>
                        <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                      </td>
                      <td>{d.name}</td>
                      <td>{d.state_name || STATE_META[d.state_key]?.name || "—"}</td>
                      <td>{d.district_id}</td>
                    </tr>
                  );
                })}
                {!districtPageRows.length && (
                  <tr>
                    <td colSpan={4} className="muted" style={{ textAlign: "center" }}>
                      {districtSearch.trim() ? "No districts match your search." : "No districts available."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {filteredPickerDistricts.length > DISTRICT_PAGE_SIZE && (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.65rem" }}>
              <button
                type="button"
                className="btn btn-sm"
                disabled={districtSafePage <= 0 || districtSaving}
                onClick={() => setDistrictPage((p) => Math.max(0, p - 1))}
              >
                Prev
              </button>
              <span className="muted" style={{ fontSize: "0.82rem" }}>
                {districtSafePage + 1} / {districtPageCount}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={districtSafePage >= districtPageCount - 1 || districtSaving}
                onClick={() => setDistrictPage((p) => Math.min(districtPageCount - 1, p + 1))}
              >
                Next
              </button>
            </div>
          )}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={() => setDistrictsModalOpen(false)} disabled={districtSaving}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={saveDistricts} disabled={districtSaving}>
            {districtSaving ? "Saving…" : "Save"}
          </button>
        </div>
      </Modal>

      <Modal open={clearModalOpen} onClose={() => !clearingSurvey && setClearModalOpen(false)} title={null}>
        <div className="modal-warning-header">
          <i className="bi bi-exclamation-triangle-fill modal-warning-icon" aria-hidden />
          <h2 className="modal-title" style={{ margin: 0 }}>Clear survey?</h2>
        </div>
        <div className="modal-body">
          <p>Remove today’s assigned route so you can pick a new start and end.</p>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={() => setClearModalOpen(false)} disabled={clearingSurvey}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={clearSurvey} disabled={clearingSurvey}>
            {clearingSurvey ? "Clearing…" : "Clear survey"}
          </button>
        </div>
      </Modal>

      <Modal open={customModalOpen} onClose={() => setCustomModalOpen(false)} title={null}>
        <div className="modal-warning-header">
          <i className="bi bi-signpost-2 modal-warning-icon" aria-hidden />
          <h2 className="modal-title" style={{ margin: 0 }}>Assign custom route?</h2>
        </div>
        <div className="modal-body">
          <p>
            This stores your <strong>start</strong> and <strong>end</strong> only — you drive any path between them.
            After Capture upload, the GPS trail is verified against the video GPS log.
          </p>
          {startHit && endHit && (
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {(startHit.display_name || startQuery || "Start")} → {(endHit.display_name || endQuery || "End")}
            </p>
          )}
          <p className="muted" style={{ fontSize: "0.82rem" }}>
            You cannot assign another route until this one is completed (matched upload) or an admin clears it.
          </p>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={() => setCustomModalOpen(false)} disabled={assigning}>
            No
          </button>
          <button type="button" className="btn btn-primary" onClick={assignCustomRoute} disabled={assigning}>
            {assigning ? "Assigning…" : "Yes, assign & go Capture"}
          </button>
        </div>
      </Modal>
    </PageShell>
  );
}
