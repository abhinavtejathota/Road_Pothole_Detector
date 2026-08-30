import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Alert,
} from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { api } from '../api';
import AppMap from '../components/AppMap';
import DistrictPickerModal from '../components/DistrictPickerModal';
import { colors, buttonStyles } from '../theme';

function stateKeyFromUser(user) {
  const sid = user?.state_id;
  return sid === 2 || sid === '2' ? 'telangana' : 'andhra';
}

/** Default map framing by VG state — avoid always opening on Hyderabad. */
function defaultRegionForState(stateKey) {
  if (stateKey === 'telangana') {
    return { latitude: 17.9, longitude: 79.5, latitudeDelta: 2.2, longitudeDelta: 2.2 };
  }
  return { latitude: 15.95, longitude: 79.95, latitudeDelta: 3.2, longitudeDelta: 3.2 };
}

function districtIdsFromUser(user) {
  const ids = (user?.district_ids || []).map(String);
  if (ids.length) return ids;
  if (user?.district_id != null) return [String(user.district_id)];
  return [];
}

function dedupeGeocodeHits(hits) {
  const seen = new Set();
  return (hits || []).filter((h) => {
    const name = (h.display_name || '').split(',')[0].trim().toLowerCase();
    const dist = String(h.district_name || h.district_id || '').toLowerCase();
    const key = `${name}|${dist}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function geocodeHitLabel(h) {
  const name = (h.display_name || '').trim();
  const dist = (h.district_name || '').trim();
  if (!dist || dist.toLowerCase() === name.toLowerCase()) return name;
  return `${name} · ${dist}`;
}

function polylineFromRoute(route) {
  /**
   * Never concatenate multiple LineStrings into one path — that draws
   * straight chords between unrelated segment endpoints (through buildings).
   * Prefer the continuous OSRM `polyline`; otherwise return the longest
   * single LineString (caller draws one preview line per route card).
   */
  const poly = route?.polyline;
  if (Array.isArray(poly) && poly.length) {
    return poly
      .filter((p) => Array.isArray(p) && p.length >= 2)
      .map((p) => ({ latitude: Number(p[0]), longitude: Number(p[1]) }));
  }
  const feats = route?.geojson?.features || [];
  let best = [];
  for (const f of feats) {
    const coords = f?.geometry?.coordinates;
    if (f?.geometry?.type !== 'LineString' || !Array.isArray(coords)) continue;
    const pts = [];
    for (const c of coords) {
      if (Array.isArray(c) && c.length >= 2) {
        pts.push({ latitude: Number(c[1]), longitude: Number(c[0]) });
      }
    }
    if (pts.length > best.length) best = pts;
  }
  return best;
}

/** All road LineStrings for a route (separate polylines — no cross-segment chords). */
function polylinesFromRoute(route, selected) {
  const continuous = polylineFromRoute(route);
  if (Array.isArray(route?.polyline) && route.polyline.length >= 2 && continuous.length >= 2) {
    return [
      {
        id: route.id,
        coordinates: continuous,
        strokeColor: selected ? '#1A73E8' : '#9AA0A6',
        strokeWidth: selected ? 6 : 4,
      },
    ];
  }
  const feats = route?.geojson?.features || [];
  const out = [];
  feats.forEach((f, i) => {
    const coords = f?.geometry?.coordinates;
    if (f?.geometry?.type !== 'LineString' || !Array.isArray(coords)) return;
    const pts = [];
    for (const c of coords) {
      if (Array.isArray(c) && c.length >= 2) {
        pts.push({ latitude: Number(c[1]), longitude: Number(c[0]) });
      }
    }
    if (pts.length < 2) return;
    out.push({
      id: `${route.id}::${i}`,
      coordinates: pts,
      strokeColor: selected ? '#1A73E8' : '#9AA0A6',
      strokeWidth: selected ? 6 : 4,
    });
  });
  return out;
}

export default function SurveyRouteScreen({ user, onBack, onAssigned, onUserUpdated }) {
  const stateKey = stateKeyFromUser(user);
  const initialDistrictIds = districtIdsFromUser(user);
  const [activeDistrictIds, setActiveDistrictIds] = useState(initialDistrictIds);
  const [districtId, setDistrictId] = useState(initialDistrictIds[0] || '');
  const [districtOpen, setDistrictOpen] = useState(false);

  const [startQ, setStartQ] = useState('');
  const [endQ, setEndQ] = useState('');
  const [startHit, setStartHit] = useState(null);
  const [endHit, setEndHit] = useState(null);
  const [startHits, setStartHits] = useState([]);
  const [endHits, setEndHits] = useState([]);

  const [routes, setRoutes] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [suggestBusy, setSuggestBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [msgError, setMsgError] = useState(false);
  const suggestSeq = useRef(0);
  const startDebounceRef = useRef(null);
  const endDebounceRef = useRef(null);

  const selected = useMemo(
    () => routes.find((r) => r.id === selectedId) || null,
    [routes, selectedId],
  );

  useEffect(() => {
    const ids = districtIdsFromUser(user);
    setActiveDistrictIds(ids);
    setDistrictId((prev) => (ids.includes(String(prev)) ? prev : (ids[0] || '')));
  }, [user?.district_ids, user?.district_id]);

  const geocode = async (which, { soft = false } = {}) => {
    const q = which === 'start' ? startQ.trim() : endQ.trim();
    if (q.length < 2) {
      if (!soft) {
        setMsg('Enter at least 2 characters');
        setMsgError(true);
      }
      return;
    }
    const seq = ++suggestSeq.current;
    if (!soft) {
      setBusy(true);
      setMsg('');
    } else {
      setSuggestBusy(true);
    }
    try {
      const res = await api.geocode(q, { stateKey, districtIds: activeDistrictIds });
      if (seq !== suggestSeq.current) return;
      const hits = dedupeGeocodeHits(res.results || []);
      if (which === 'start') {
        setStartHits(hits);
        if (!soft) setStartHit(null);
      } else {
        setEndHits(hits);
        if (!soft) setEndHit(null);
      }
      if (!hits.length && !soft) {
        setMsg(`No places found for "${q}"`);
        setMsgError(true);
      }
    } catch (e) {
      if (seq !== suggestSeq.current) return;
      if (!soft) {
        setMsg(e.message);
        setMsgError(true);
      }
    } finally {
      if (seq === suggestSeq.current) {
        setBusy(false);
        setSuggestBusy(false);
      }
    }
  };

  // Live recommendations while typing (local road index is fast after backend fix)
  useEffect(() => {
    if (startDebounceRef.current) clearTimeout(startDebounceRef.current);
    const q = startQ.trim();
    if (q.length < 3 || startHit) return undefined;
    startDebounceRef.current = setTimeout(() => {
      geocode('start', { soft: true });
    }, 280);
    return () => {
      if (startDebounceRef.current) clearTimeout(startDebounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startQ]);

  useEffect(() => {
    if (endDebounceRef.current) clearTimeout(endDebounceRef.current);
    const q = endQ.trim();
    if (q.length < 3 || endHit) return undefined;
    endDebounceRef.current = setTimeout(() => {
      geocode('end', { soft: true });
    }, 280);
    return () => {
      if (endDebounceRef.current) clearTimeout(endDebounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endQ]);

  const pick = async (which, hit) => {
    if (hit?.access_ok === false) {
      setMsg(hit.access_message || 'You do not have access to that place.');
      setMsgError(true);
      return;
    }
    setBusy(true);
    try {
      let enriched = { ...hit };
      let chk = null;
      // Road-index / coords / already-located hits — skip a second locate round-trip.
      if (
        hit?.district_id
        && hit?.access_ok !== false
        && (hit?.source === 'road_index' || hit?.source === 'gps' || hit?.source === 'coords')
      ) {
        enriched = {
          ...hit,
          display_name: hit.display_name,
        };
      } else {
        chk = await api.locate(hit.lat, hit.lon, { stateKey, reverse: false });
        if (chk && chk.in_scope === false) {
          setMsg(chk.message || 'You do not have access to that place.');
          setMsgError(true);
          return;
        }
        enriched = {
          ...hit,
          display_name: hit.display_name,
          district_id: hit.district_id || chk?.located?.district_id,
          district_name: hit.district_name || chk?.located?.district_name,
          state_key: hit.state_key || chk?.located?.state_key,
        };
      }
      if (which === 'start') {
        setStartHit(enriched);
        setStartHits([]);
        setStartQ(enriched.display_name || '');
      } else {
        setEndHit(enriched);
        setEndHits([]);
        setEndQ(enriched.display_name || '');
      }
      setRoutes([]);
      setSelectedId(null);
      const did = enriched.district_id;
      if (did && activeDistrictIds.includes(String(did))) {
        setDistrictId(String(did));
      }
      if (enriched.district_name || chk?.located?.district_name) {
        setMsg(`Located in ${enriched.district_name || chk.located.district_name}.`);
        setMsgError(false);
      } else {
        setMsg('');
        setMsgError(false);
      }
    } catch (e) {
      setMsg(e.message);
      setMsgError(true);
    } finally {
      setBusy(false);
    }
  };

  const myLocation = async (which) => {
    setBusy(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') throw new Error('Location permission denied');
      const pos = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      // Same path as pasting lat,lon — reverse place/road name + district access.
      const res = await api.geocode(`${lat},${lon}`, {
        stateKey,
        districtIds: activeDistrictIds,
      });
      const hit = (res.results || [])[0];
      if (!hit) throw new Error('Could not resolve your GPS fix');
      if (hit.access_ok === false) {
        setMsg(hit.access_message || 'You do not have access to that place.');
        setMsgError(true);
        return;
      }
      if (which === 'start') {
        setStartHit(hit);
        setStartHits([]);
        setStartQ(hit.display_name || '');
      } else {
        setEndHit(hit);
        setEndHits([]);
        setEndQ(hit.display_name || '');
      }
      setRoutes([]);
      setSelectedId(null);
      const did = hit.district_id;
      if (did && activeDistrictIds.includes(String(did))) {
        setDistrictId(String(did));
      }
      setMsg(hit.district_name ? `Located in ${hit.district_name}.` : 'Location set.');
      setMsgError(false);
    } catch (e) {
      setMsg(e.message || 'Could not get location');
      setMsgError(true);
    } finally {
      setBusy(false);
    }
  };

  const findRoutes = async () => {
    if (!startHit || !endHit) {
      setMsg('Set start and end first');
      setMsgError(true);
      return;
    }
    setBusy(true);
    setMsg('Finding routes…');
    setMsgError(false);
    setRoutes([]);
    setSelectedId(null);
    try {
      const preferredDistrict =
        (startHit.district_id && activeDistrictIds.includes(String(startHit.district_id))
          ? String(startHit.district_id)
          : null)
        || (endHit.district_id && activeDistrictIds.includes(String(endHit.district_id))
          ? String(endHit.district_id)
          : null)
        || districtId;
      const res = await api.previewRoutes({
        start_lat: startHit.lat,
        start_lon: startHit.lon,
        end_lat: endHit.lat,
        end_lon: endHit.lon,
        start_label: startHit.display_name || startQ,
        end_label: endHit.display_name || endQ,
        district_id: preferredDistrict,
        state_key: startHit.state_key || endHit.state_key || stateKey,
        max_options: 3,
      });
      const list = res.routes || [];
      setRoutes(list);
      setSelectedId(list[0]?.id || null);
      if (res.district_id) setDistrictId(String(res.district_id));
      setMsg(list.length ? `Found ${list.length} route option(s)` : 'No routes found');
      setMsgError(!list.length);
    } catch (e) {
      setMsg(e.message);
      setMsgError(true);
    } finally {
      setBusy(false);
    }
  };

  const assignCustom = async () => {
    if (!startHit || !endHit) {
      setMsg('Set start and end first');
      setMsgError(true);
      return;
    }
    // Confirm
    Alert.alert(
      'Assign custom route?',
      'Start and end only — drive any path, then Capture. Complete when you reach the end pin (cover km can be under or over assigned). You cannot assign again until this is completed.',
      [
        { text: 'No', style: 'cancel' },
        {
          text: 'Yes, assign',
          onPress: async () => {
            setBusy(true);
            try {
              await api.assignCustomRoute({
                start_lat: startHit.lat,
                start_lon: startHit.lon,
                end_lat: endHit.lat,
                end_lon: endHit.lon,
                start_label: startHit.display_name || startQ,
                end_label: endHit.display_name || endQ,
                district_id: districtId,
              });
              setMsg('Custom route assigned — map shows start & end; tap Capture');
              setMsgError(false);
              onAssigned?.({ goCapture: false });
            } catch (e) {
              setMsg(e.message);
              setMsgError(true);
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  };

  const assign = async () => {
    const ids = selected?.segment_ids || [];
    if (!startHit || !endHit || !ids.length) {
      setMsg('Select a route first');
      setMsgError(true);
      return;
    }
    setBusy(true);
    try {
      const res = await api.assignCorridor({
        start_lat: startHit.lat,
        start_lon: startHit.lon,
        end_lat: endHit.lat,
        end_lon: endHit.lon,
        start_label: startHit.display_name || startQ,
        end_label: endHit.display_name || endQ,
        district_id: districtId,
        segment_ids: ids,
        replace: true,
        polyline: selected?.polyline || null,
      });
      const warn = res?.overlap_warning?.message || res?.message;
      setMsg(warn ? `Route updated. ${warn}` : 'Route assigned — tap Capture on the dashboard next');
      setMsgError(false);
      onAssigned?.({ goCapture: false });
    } catch (e) {
      setMsg(e.message);
      setMsgError(true);
    } finally {
      setBusy(false);
    }
  };

  const region = useMemo(() => {
    const pts = [];
    if (startHit) pts.push({ latitude: startHit.lat, longitude: startHit.lon });
    if (endHit) pts.push({ latitude: endHit.lat, longitude: endHit.lon });
    for (const r of routes) pts.push(...polylineFromRoute(r));
    if (!pts.length) {
      return defaultRegionForState(stateKey);
    }
    const lats = pts.map((p) => p.latitude);
    const lons = pts.map((p) => p.longitude);
    const latitude = (Math.min(...lats) + Math.max(...lats)) / 2;
    const longitude = (Math.min(...lons) + Math.max(...lons)) / 2;
    const latitudeDelta = Math.max(0.04, (Math.max(...lats) - Math.min(...lats)) * 1.4);
    const longitudeDelta = Math.max(0.04, (Math.max(...lons) - Math.min(...lons)) * 1.4);
    return { latitude, longitude, latitudeDelta, longitudeDelta };
  }, [startHit, endHit, routes]);

  const turnMarkers = useMemo(() => {
    const steps = selected?.steps || [];
    return steps.filter((s) => {
      if (s?.lat == null) return false;
      const t = String(s.type || '');
      if (t === 'depart' || t === 'arrive') return false;
      const m = String(s.modifier || '');
      return (
        t === 'turn' ||
        t.includes('ramp') ||
        m.includes('uturn') ||
        t === 'roundabout' ||
        t === 'fork' ||
        t === 'end of road'
      );
    });
  }, [selected]);

  const HitList = ({ which, hits }) => {
    if (!hits?.length) return null;
    return (
      <View style={styles.hitsWrap}>
        <ScrollView
          nestedScrollEnabled
          keyboardShouldPersistTaps="handled"
          style={styles.hitsScroll}
          contentContainerStyle={styles.hitsContent}
        >
          {hits.map((h) => (
            <Pressable
              key={`${h.lat},${h.lon},${h.display_name}`}
              style={styles.hit}
              onPress={() => pick(which, h)}
            >
              <Text style={[styles.hitText, h.access_ok === false && { color: '#b3261e' }]} numberOfLines={3}>
                {h.access_ok === false ? '[no access] ' : h.source === 'road_index' ? '[road] ' : ''}
                {h.source === 'road_index' ? h.display_name : geocodeHitLabel(h)}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>
    );
  };

  return (
    <View style={styles.root}>
      <View style={styles.top}>
        <Pressable onPress={onBack} style={styles.iconBtn} hitSlop={8}>
          <MaterialIcons name="arrow-back" size={22} color="#fff" />
        </Pressable>
        <Text style={styles.title}>Select route</Text>
        <Pressable onPress={() => setDistrictOpen(true)} style={styles.iconBtn} hitSlop={8}>
          <MaterialIcons name="map" size={22} color="#fff" />
          {activeDistrictIds.length > 0 ? (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>
                {activeDistrictIds.length > 99 ? '99+' : activeDistrictIds.length}
              </Text>
            </View>
          ) : null}
        </Pressable>
        {busy ? <ActivityIndicator color="#fff" /> : <View style={{ width: 8 }} />}
      </View>

      <AppMap
        style={styles.map}
        region={region}
        polylines={routes.flatMap((r) => polylinesFromRoute(r, r.id === selectedId))}
        markers={[
          ...(startHit
            ? [{ id: 'start', latitude: startHit.lat, longitude: startHit.lon, color: '#2563eb', title: 'Start' }]
            : []),
          ...(endHit
            ? [{ id: 'end', latitude: endHit.lat, longitude: endHit.lon, color: '#b3261e', title: 'End' }]
            : []),
          ...turnMarkers.map((s, i) => ({
            id: `t-${i}`,
            latitude: s.lat,
            longitude: s.lon,
            color: '#f59e0b',
            title: s.instruction || 'Turn',
          })),
        ]}
        onPolylinePress={(id) => {
          const routeId = String(id).includes('::') ? String(id).split('::')[0] : String(id);
          setSelectedId(routeId);
        }}
      />

      <ScrollView
        style={styles.panel}
        contentContainerStyle={styles.panelContent}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <Text style={styles.muted}>
          {activeDistrictIds.length
            ? `${activeDistrictIds.length} district${activeDistrictIds.length === 1 ? '' : 's'} active — tap the map icon to change.`
            : 'Choose districts from the map icon in the top bar before searching.'}
        </Text>

        <Text style={styles.label}>Start</Text>
        <View style={styles.row}>
          <TextInput
            style={[styles.input, { flex: 1 }]}
            value={startQ}
            onChangeText={(t) => {
              setStartQ(t);
              if (startHit) setStartHit(null);
            }}
            placeholder="e.g. cyber towers / NH44"
            onSubmitEditing={() => geocode('start')}
          />
          <Pressable style={styles.smallBtn} onPress={() => geocode('start')} disabled={busy}>
            <Text style={styles.smallBtnText}>{suggestBusy && startHits.length ? '…' : 'Find'}</Text>
          </Pressable>
        </View>
        <Pressable onPress={() => myLocation('start')} disabled={busy}>
          <Text style={styles.link}>My location</Text>
        </Pressable>
        <HitList which="start" hits={startHits} />

        <Text style={styles.label}>End</Text>
        <View style={styles.row}>
          <TextInput
            style={[styles.input, { flex: 1 }]}
            value={endQ}
            onChangeText={(t) => {
              setEndQ(t);
              if (endHit) setEndHit(null);
            }}
            placeholder="e.g. JNTUH / Hitec City"
            onSubmitEditing={() => geocode('end')}
          />
          <Pressable style={styles.smallBtn} onPress={() => geocode('end')} disabled={busy}>
            <Text style={styles.smallBtnText}>{suggestBusy && endHits.length ? '…' : 'Find'}</Text>
          </Pressable>
        </View>
        <Pressable onPress={() => myLocation('end')} disabled={busy}>
          <Text style={styles.link}>My location</Text>
        </Pressable>
        <HitList which="end" hits={endHits} />

        {msg ? (
          <Text style={{ color: msgError ? '#b3261e' : '#166534', marginTop: 8 }}>{msg}</Text>
        ) : null}

        {routes.map((r) => (
          <Pressable
            key={r.id}
            style={[styles.routeCard, r.id === selectedId && styles.routeCardOn]}
            onPress={() => setSelectedId(r.id)}
          >
            <Text style={styles.routeTitle}>{r.label || `${r.km} km`}</Text>
            <Text style={styles.muted}>
              {[
                r.duration_min_est != null ? `~${r.duration_min_est} min` : null,
                r.district_ids?.length ? `${r.district_ids.length} districts` : null,
                r.steps?.length ? `${r.steps.length} steps` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </Text>
          </Pressable>
        ))}

        {(selected?.steps || []).length > 0 ? (
          <View style={{ marginTop: 8 }}>
            <Text style={styles.label}>Directions</Text>
            {selected.steps.map((s, i) => {
              const dm = s.distance_m || 0;
              const dist =
                dm >= 1000 ? `${(dm / 1000).toFixed(1)} km` : dm > 0 ? `${dm} m` : '';
              return (
                <View key={`${i}-${s.instruction}`} style={styles.stepRow}>
                  <Text style={{ flex: 1, fontSize: 13 }}>{s.instruction}</Text>
                  {dist ? <Text style={styles.muted}>{dist}</Text> : null}
                </View>
              );
            })}
          </View>
        ) : null}
      </ScrollView>

      <View style={styles.actionBar}>
        <Pressable
          style={[
            buttonStyles.primary,
            styles.barBtn,
            (busy || !startHit || !endHit) && buttonStyles.disabled,
          ]}
          onPress={findRoutes}
          disabled={busy || !startHit || !endHit}
        >
          <Text style={[buttonStyles.primaryText, styles.barBtnText]}>Find routes</Text>
        </Pressable>
        <Pressable
          style={[
            buttonStyles.outline,
            styles.barBtn,
            (busy || !startHit || !endHit) && buttonStyles.disabled,
          ]}
          onPress={assignCustom}
          disabled={busy || !startHit || !endHit}
        >
          <Text style={[buttonStyles.outlineText, styles.barBtnText]}>Custom</Text>
        </Pressable>
        <Pressable
          style={[
            buttonStyles.tonal,
            styles.barBtn,
            (busy || !(selected?.segment_ids || []).length) && buttonStyles.disabled,
          ]}
          onPress={assign}
          disabled={busy || !(selected?.segment_ids || []).length}
        >
          <Text style={[buttonStyles.tonalText, styles.barBtnText]}>Assign</Text>
        </Pressable>
      </View>

      <DistrictPickerModal
        open={districtOpen}
        user={user}
        onClose={() => setDistrictOpen(false)}
        onSaved={(payload) => {
          const ids = (payload?.district_ids || []).map(String);
          setActiveDistrictIds(ids);
          setDistrictId(ids[0] || '');
          setMsg(ids.length ? `Saved ${ids.length} district${ids.length === 1 ? '' : 's'}.` : '');
          setMsgError(false);
          onUserUpdated?.(payload);
        }}
      />
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
  iconBtn: { padding: 4, minWidth: 36, position: 'relative' },
  badge: {
    position: 'absolute',
    top: 0,
    right: 0,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    backgroundColor: colors.gold,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  badgeText: { fontSize: 9, fontWeight: '800', color: colors.navy },
  title: { fontWeight: '700', color: '#fff', fontSize: 16 },
  map: { height: '40%' },
  panel: { flex: 1, backgroundColor: colors.white },
  panelContent: { paddingHorizontal: 14, paddingTop: 10, paddingBottom: 16 },
  label: { fontWeight: '600', marginTop: 8, marginBottom: 4, color: colors.navy },
  row: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  input: {
    borderWidth: 1,
    borderColor: colors.inputBorder,
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 10,
    backgroundColor: colors.inputBg,
  },
  smallBtn: {
    backgroundColor: colors.navy,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 12,
  },
  smallBtnText: { color: '#fff', fontWeight: '700' },
  link: { color: colors.navy, marginVertical: 4, fontWeight: '600' },
  hitsWrap: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    marginBottom: 8,
    backgroundColor: colors.white,
    overflow: 'hidden',
    maxHeight: 140,
    zIndex: 5,
    elevation: 3,
  },
  hitsScroll: { maxHeight: 140 },
  hitsContent: { flexGrow: 0 },
  hit: { padding: 10, borderBottomWidth: 1, borderBottomColor: '#eef2f5' },
  hitText: { fontSize: 13, color: colors.navy },
  actionBar: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: 28,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.white,
    zIndex: 10,
    elevation: 8,
  },
  barBtn: {
    flex: 1,
    paddingVertical: 12,
    paddingHorizontal: 8,
  },
  barBtnText: { fontSize: 13 },
  routeCard: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    padding: 12,
    marginTop: 8,
    backgroundColor: colors.white,
  },
  routeCardOn: {
    borderColor: colors.navy,
    backgroundColor: colors.tonalBg,
  },
  routeTitle: { fontWeight: '700', marginBottom: 2, color: colors.navy },
  muted: { color: colors.muted, fontSize: 12 },
  stepRow: {
    flexDirection: 'row',
    gap: 8,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: '#eef2f5',
  },
});
