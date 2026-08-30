import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Image,
  Alert,
} from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import { api } from '../api';
import AppMap from '../components/AppMap';
import DistrictPickerModal from '../components/DistrictPickerModal';
import { colors, buttonStyles } from '../theme';

function linesFromGeo(geo) {
  const features = geo?.features || [];
  const lines = [];
  const markers = [];
  features.forEach((f, idx) => {
    const geom = f?.geometry;
    const props = f?.properties || {};
    const st = String(props.status || '');
    const kind = String(props.route_kind || '');
    const isCovered = st === 'completed' || st === 'verified' || kind === 'covered';

    if (geom?.type === 'Point' && Array.isArray(geom.coordinates) && geom.coordinates.length >= 2) {
      markers.push({
        id: props.id || `pt-${idx}`,
        latitude: Number(geom.coordinates[1]),
        longitude: Number(geom.coordinates[0]),
        title: props.label || props.endpoint || 'Point',
        color: props.marker_color || (props.endpoint === 'end' ? '#dc2626' : '#16a34a'),
      });
      return;
    }

    const push = (coords) => {
      if (!Array.isArray(coords)) return;
      const pts = coords
        .filter((c) => Array.isArray(c) && c.length >= 2)
        .map((c) => ({ latitude: Number(c[1]), longitude: Number(c[0]) }));
      if (pts.length >= 2) {
        lines.push({
          key: `${props.segment_id || idx}-${lines.length}`,
          pts,
          isCovered,
          continuous: kind === 'continuous' || kind === 'auto_track',
        });
      }
    };
    if (geom?.type === 'LineString') push(geom.coordinates);
    if (geom?.type === 'MultiLineString') (geom.coordinates || []).forEach(push);
  });
  return { lines, markers };
}

/** Survive Capture remounts so returning from upload never blanks the UI. */
let _dashCache = { summary: null, geo: null };

export function clearDashboardCache() {
  _dashCache = { summary: null, geo: null };
}

export default function DashboardScreen({
  user,
  onLogout,
  onSelectRoute,
  onCapture,
  onUserUpdated,
  sealTick = 0,
}) {
  const [summary, setSummary] = useState(_dashCache.summary);
  const [geo, setGeo] = useState(_dashCache.geo);
  const [loading, setLoading] = useState(!_dashCache.summary && !_dashCache.geo);
  const [error, setError] = useState('');
  const [sealStep, setSealStep] = useState(0);
  const [sealing, setSealing] = useState(false);
  const [districtOpen, setDistrictOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async ({ soft = false } = {}) => {
    const hasCache = !!( _dashCache.summary || _dashCache.geo);
    if (soft && hasCache) {
      if (_dashCache.summary) setSummary(_dashCache.summary);
      if (_dashCache.geo) setGeo(_dashCache.geo);
      setLoading(false);
    } else if (!hasCache) {
      setLoading(true);
    }
    setError('');
    try {
      // Summary first so the dashboard paints even if geojson is slow.
      const a = await api.assignmentSummary();
      const nextSummary = a.summary || null;
      setSummary(nextSummary);
      _dashCache = { ..._dashCache, summary: nextSummary };
      setLoading(false);
      const g = await api.assignmentGeoJson();
      setGeo(g);
      _dashCache = { summary: nextSummary, geo: g };
    } catch (e) {
      if (!hasCache) setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load({ soft: true });
  }, [load]);

  // Returning from Capture without a successful upload: clear any mid-seal UI
  useEffect(() => {
    if (!sealTick) {
      setSealing(false);
      setSealStep(0);
    }
  }, [sealTick]);

  useEffect(() => {
    if (!sealTick) return undefined;
    let cancelled = false;
    (async () => {
      try {
        await load({ soft: true });
        if (cancelled) return;
        const covered = linesFromGeo(_dashCache.geo || geo).lines.filter((l) => l.isCovered);
        const n = covered.length;
        if (!n) {
          setSealing(false);
          setSealStep(0);
          setTimeout(() => {
            if (!cancelled) load({ soft: true });
          }, 2500);
          return;
        }
        setSealing(true);
        setSealStep(0);
        for (let i = 1; i <= n; i += 1) {
          // eslint-disable-next-line no-await-in-loop
          await new Promise((r) => setTimeout(r, Math.max(40, Math.min(100, Math.floor(1800 / n)))));
          if (cancelled) return;
          setSealStep(i);
        }
        if (!cancelled) setSealing(false);
      } catch (e) {
        if (!cancelled) setError(e.message || 'Failed to load');
      }
    })();
    return () => { cancelled = true; };
  }, [sealTick, load]);

  const parsed = useMemo(() => linesFromGeo(geo), [geo]);
  // Indigo corridor under grey covered GPS (same layering as web Survey)
  const lines = useMemo(() => {
    const list = parsed.lines || [];
    return [...list].sort((a, b) => Number(a.isCovered) - Number(b.isCovered));
  }, [parsed.lines]);
  const markers = parsed.markers;
  const isCustom = summary?.mode === 'auto_track';
  const hasRoute = lines.length > 0 || markers.length >= 2 || isCustom;
  const canChangeRoute = summary?.can_change_route !== false;
  const changeRouteMsg = summary?.change_route_message || 'Complete the current assignment before changing route.';
  const coveredKm = Number(summary?.covered_km ?? 0);
  const totalKm = Number(summary?.total_km ?? summary?.route_km ?? 0);
  const isComplete = Boolean(
    summary?.assignment_complete || summary?.completed,
  );
  const region = useMemo(() => {
    const pts = [
      ...lines.flatMap((l) => l.pts || []),
      ...markers.map((m) => ({ latitude: m.latitude, longitude: m.longitude })),
    ];
    if (!pts.length && summary?.start?.lat != null && summary?.end?.lat != null) {
      pts.push(
        { latitude: Number(summary.start.lat), longitude: Number(summary.start.lon) },
        { latitude: Number(summary.end.lat), longitude: Number(summary.end.lon) },
      );
    }
    if (!pts.length) {
      return { latitude: 17.45, longitude: 78.39, latitudeDelta: 0.12, longitudeDelta: 0.12 };
    }
    const lats = pts.map((p) => p.latitude);
    const lons = pts.map((p) => p.longitude);
    return {
      latitude: (Math.min(...lats) + Math.max(...lats)) / 2,
      longitude: (Math.min(...lons) + Math.max(...lons)) / 2,
      latitudeDelta: Math.max(0.04, (Math.max(...lats) - Math.min(...lats)) * 1.6),
      longitudeDelta: Math.max(0.04, (Math.max(...lons) - Math.min(...lons)) * 1.6),
    };
  }, [lines, markers, summary]);

  const endpointMarkers = useMemo(() => {
    if (markers.length) return markers;
    if (!isCustom || summary?.start?.lat == null || summary?.end?.lat == null) return [];
    return [
      {
        id: 'start',
        latitude: Number(summary.start.lat),
        longitude: Number(summary.start.lon),
        title: summary.start.label || 'Start',
        color: '#16a34a',
      },
      {
        id: 'end',
        latitude: Number(summary.end.lat),
        longitude: Number(summary.end.lon),
        title: summary.end.label || 'End',
        color: '#dc2626',
      },
    ];
  }, [markers, isCustom, summary]);

  const name = user?.full_name || user?.username || '';
  const count = summary?.segment_count ?? 0;
  let coveredAnimIdx = 0;
  const districtCount = (user?.district_ids || []).length
    || (user?.district_id != null ? 1 : 0);

  const clearSurvey = () => {
    if (clearing) return;
    Alert.alert(
      'Clear survey',
      'Remove the assigned route so you can pick a new one?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Clear',
          style: 'destructive',
          onPress: async () => {
            setClearing(true);
            setError('');
            try {
              const workDate = summary?.assignment_date || summary?.date || null;
              const res = await api.clearMySurveyAssignment(workDate);
              if (!res?.cleared) {
                throw new Error(res?.message || 'No survey assignment to clear');
              }
              clearDashboardCache();
              setSummary(null);
              setGeo(null);
              await load({ soft: false });
            } catch (e) {
              setError(e.message || 'Could not clear survey');
            } finally {
              setClearing(false);
            }
          },
        },
      ],
    );
  };

  return (
    <View style={styles.root}>
      <View style={styles.top}>
        <Image source={require('../../assets/icon.png')} style={styles.logo} />
        <View style={{ flex: 1 }}>
          <Text style={styles.brand}>SmartRoad Field</Text>
          <Text style={styles.hi}>Hi {name}</Text>
        </View>
        <Pressable onPress={() => setDistrictOpen(true)} style={styles.iconBtn} hitSlop={8}>
          <MaterialIcons name="map" size={22} color="#fff" />
          {districtCount > 0 ? (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{districtCount > 99 ? '99+' : districtCount}</Text>
            </View>
          ) : null}
        </Pressable>
        <Pressable onPress={() => load({ soft: true })} style={styles.iconBtn} hitSlop={8}>
          <MaterialIcons name="refresh" size={22} color="#fff" />
        </Pressable>
        <Pressable onPress={onLogout} style={styles.iconBtn} hitSlop={8}>
          <MaterialIcons name="logout" size={22} color="#fff" />
        </Pressable>
      </View>

      <View style={styles.metaStrip}>
        <Text style={styles.metaStripText}>
          {isCustom
            ? `Custom · ${totalKm || 0} km · Capture any path start→end`
            : `Assigned: ${count} seg · ${totalKm || 0} km`}
          {coveredKm > 0.01 ? ` · covered ${coveredKm.toFixed(1)} km` : ''}
          {isComplete ? ' · complete' : ''}
          {sealing ? ' · sealing…' : ''}
        </Text>
      </View>

      {loading ? (
        <View style={styles.boot}>
          <Image source={require('../../assets/icon.png')} style={styles.bootLogo} />
          <ActivityIndicator color={colors.navy} style={{ marginTop: 16 }} />
        </View>
      ) : (
        <View style={styles.mapWrap}>
          {error ? <Text style={styles.err}>{error}</Text> : null}
          {!hasRoute ? (
            <ScrollView contentContainerStyle={styles.empty}>
              <Image source={require('../../assets/icon.png')} style={styles.emptyLogo} />
              <Text style={styles.emptyText}>
                No assigned path yet.{'\n'}Pick start & end, then Capture to film the roads.
              </Text>
              <Pressable style={buttonStyles.primary} onPress={onSelectRoute}>
                <MaterialIcons name="alt-route" size={20} color="#fff" />
                <Text style={buttonStyles.primaryText}>Select route</Text>
              </Pressable>
            </ScrollView>
          ) : (
            <AppMap
              style={StyleSheet.absoluteFill}
              initialRegion={region}
              markers={endpointMarkers}
              polylines={lines.map((line) => {
                let stroke = '#6366F1';
                if (line.isCovered) {
                  if (sealing) {
                    const reveal = coveredAnimIdx < sealStep;
                    coveredAnimIdx += 1;
                    stroke = reveal ? '#9CA3AF' : '#6366F1';
                  } else {
                    stroke = '#9CA3AF';
                  }
                }
                return {
                  id: line.key,
                  coordinates: line.pts,
                  strokeColor: stroke,
                  strokeWidth: line.isCovered ? 6 : (line.continuous ? 5.5 : 4.5),
                };
              })}
            />
          )}
        </View>
      )}

      <View style={styles.bottom}>
        {!hasRoute ? (
          <Pressable style={buttonStyles.primary} onPress={onSelectRoute}>
            <MaterialIcons name="alt-route" size={20} color="#fff" />
            <Text style={buttonStyles.primaryText}>Select route</Text>
          </Pressable>
        ) : (
          <>
            <Pressable style={buttonStyles.primary} onPress={onCapture}>
              <MaterialIcons name="videocam" size={20} color="#fff" />
              <Text style={buttonStyles.primaryText}>Capture</Text>
            </Pressable>
            <View style={styles.secondaryRow}>
              <Pressable
                style={[
                  buttonStyles.outline,
                  styles.secondaryBtn,
                  !canChangeRoute && buttonStyles.disabled,
                ]}
                onPress={() => {
                  if (!canChangeRoute) {
                    setError(changeRouteMsg);
                    return;
                  }
                  onSelectRoute();
                }}
              >
                <MaterialIcons name="alt-route" size={18} color={colors.navy} />
                <Text style={buttonStyles.outlineText}>
                  {canChangeRoute ? 'Change route' : 'Change blocked'}
                </Text>
              </Pressable>
              <Pressable
                style={[styles.clearBtn, clearing && buttonStyles.disabled]}
                onPress={clearSurvey}
                disabled={clearing}
              >
                {clearing ? (
                  <ActivityIndicator color={colors.dangerText} size="small" />
                ) : (
                  <>
                    <MaterialIcons name="layers-clear" size={18} color={colors.dangerText} />
                    <Text style={styles.clearBtnText}>Clear survey</Text>
                  </>
                )}
              </Pressable>
            </View>
            {!canChangeRoute && (
              <Text style={[styles.err, { marginTop: 8 }]}>{changeRouteMsg}</Text>
            )}
            {isComplete ? (
              <Text style={styles.carryNote}>
                Assignment complete — start→end reached. Grey on the map is your GPS path.
              </Text>
            ) : null}
            {summary?.is_carryover && summary?.assignment_date ? (
              <Text style={styles.carryNote}>
                Incomplete from {summary.assignment_date} — finish this route (start→end) before a new day assignment.
              </Text>
            ) : null}
          </>
        )}
      </View>

      <DistrictPickerModal
        open={districtOpen}
        user={user}
        onClose={() => setDistrictOpen(false)}
        onSaved={(payload) => {
          onUserUpdated?.(payload);
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.pageBg },
  top: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 52,
    paddingBottom: 10,
    gap: 10,
    backgroundColor: colors.navy,
  },
  logo: { width: 32, height: 32, borderRadius: 8 },
  brand: { fontSize: 16, fontWeight: '800', color: '#fff' },
  hi: { fontSize: 12, fontWeight: '500', color: 'rgba(255,255,255,0.8)', marginTop: 1 },
  iconBtn: { padding: 6, position: 'relative' },
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
  secondaryRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 8,
  },
  secondaryBtn: {
    flex: 1,
    paddingHorizontal: 10,
  },
  clearBtn: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: colors.dangerText,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 10,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 6,
    backgroundColor: '#FFF5F5',
  },
  clearBtnText: {
    color: colors.dangerText,
    fontWeight: '700',
    fontSize: 14,
  },
  metaStrip: {
    backgroundColor: colors.white,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  metaStripText: { color: colors.navy, fontWeight: '600', fontSize: 13 },
  mapWrap: { flex: 1 },
  boot: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  bootLogo: { width: 88, height: 88, borderRadius: 20 },
  empty: { padding: 28, alignItems: 'center', justifyContent: 'center', flexGrow: 1 },
  emptyLogo: { width: 72, height: 72, borderRadius: 16, marginBottom: 16, opacity: 0.95 },
  emptyText: { textAlign: 'center', color: colors.muted, marginBottom: 16, lineHeight: 22 },
  err: { color: colors.danger, padding: 12, textAlign: 'center' },
  carryNote: { marginTop: 8, color: colors.muted, fontSize: 12, textAlign: 'center' },
  bottom: {
    padding: 16,
    paddingBottom: 28,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.white,
  },
});
