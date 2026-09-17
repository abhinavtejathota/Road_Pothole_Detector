/**
 * ComplaintDetailScreen — full-detail view for a single complaint.
 *
 * Shows:
 *  - Status badge + tracking number.
 *  - All submitted metadata: defect type, description, GPS, timestamps.
 *  - Media preview (photo or video thumbnail) via s3_url.
 *  - Maps deep-link to open the GPS location in Google Maps / Apple Maps.
 *  - Timeline: created → updated → resolved dates.
 *
 * Props:
 *   complaint   object   — the complaint object from the API
 *   onBack      fn       — go back to the list
 */
import {
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import StatusBadge from '../components/StatusBadge';
import MediaPreview from '../components/MediaPreview';
import {
  formatCoords,
  formatDateTime,
  formatDefectType,
  formatRelativeTime,
  formatTrackingNumber,
  formatAccuracy,
} from '../utils/formatters';

function Row({ label, value, mono = false }) {
  if (!value && value !== 0) return null;
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={[styles.rowValue, mono && styles.mono]}>{String(value)}</Text>
    </View>
  );
}

function Section({ title, children }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

export default function ComplaintDetailScreen({ complaint, onBack }) {
  if (!complaint) {
    return (
      <View style={styles.center}>
        <Text style={styles.notFound}>Complaint not found.</Text>
        <Pressable onPress={onBack}>
          <Text style={styles.backLink}>← Go back</Text>
        </Pressable>
      </View>
    );
  }

  const hasGps =
    complaint.latitude != null && complaint.longitude != null;

  const openMaps = () => {
    if (!hasGps) return;
    const lat = complaint.latitude;
    const lon = complaint.longitude;
    const label = encodeURIComponent(complaint.tracking_number || 'Pothole');
    const url = Platform.OS === 'ios'
      ? `maps:0,0?q=${label}@${lat},${lon}`
      : `geo:${lat},${lon}?q=${lat},${lon}(${label})`;
    Linking.openURL(url).catch(() =>
      Linking.openURL(
        `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`,
      ),
    );
  };

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {/* Header */}
      <Pressable onPress={onBack} style={styles.backBtn}>
        <Text style={styles.backText}>← Back</Text>
      </Pressable>

      <View style={styles.headerRow}>
        <Text style={styles.tn} numberOfLines={2}>
          {formatTrackingNumber(complaint.tracking_number)}
        </Text>
        <StatusBadge status={complaint.status} />
      </View>

      {/* Media */}
      {!!complaint.s3_url && (
        <MediaPreview remoteUrl={complaint.s3_url} />
      )}

      {/* Details */}
      <Section title="Complaint Details">
        <Row label="Category" value={formatDefectType(complaint.defect_type)} />
        <Row label="Description" value={complaint.description} />
        <Row label="Filed on" value={formatDateTime(complaint.created_at)} />
        {complaint.updated_at && complaint.updated_at !== complaint.created_at && (
          <Row label="Last updated" value={formatRelativeTime(complaint.updated_at)} />
        )}
        {complaint.resolved_at && (
          <Row label="Resolved on" value={formatDateTime(complaint.resolved_at)} />
        )}
        {complaint.admin_note && (
          <Row label="Admin note" value={complaint.admin_note} />
        )}
      </Section>

      {/* Location */}
      {hasGps && (
        <Section title="Location">
          <Row
            label="Coordinates"
            value={formatCoords(complaint.latitude, complaint.longitude)}
            mono
          />
          {complaint.gps_accuracy_m != null && (
            <Row label="GPS accuracy" value={formatAccuracy(complaint.gps_accuracy_m)} />
          )}
          <Pressable style={styles.mapsBtn} onPress={openMaps}>
            <Text style={styles.mapsBtnText}>Open in Maps →</Text>
          </Pressable>
        </Section>
      )}

      {/* Technical */}
      <Section title="Reference">
        <Row label="Tracking #" value={complaint.tracking_number} mono />
        {complaint.id && <Row label="Internal ID" value={String(complaint.id)} mono />}
        {complaint.media_kind && <Row label="Media type" value={complaint.media_kind} />}
        {complaint.source && <Row label="Source" value={complaint.source} />}
      </Section>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  content: { padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  notFound: { fontSize: 16, color: '#475569', marginBottom: 16 },
  backLink: { color: '#0369a1', fontWeight: '600' },

  backBtn: { marginBottom: 12 },
  backText: { color: '#0369a1', fontSize: 14, fontWeight: '600' },

  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 4,
    gap: 8,
  },
  tn: { flex: 1, fontSize: 18, fontWeight: '800', color: '#0f172a' },

  section: {
    marginTop: 20,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: '#94a3b8',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  sectionBody: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    overflow: 'hidden',
  },

  row: {
    flexDirection: 'row',
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  rowLabel: {
    width: 110,
    fontSize: 13,
    color: '#64748b',
    flexShrink: 0,
  },
  rowValue: {
    flex: 1,
    fontSize: 13,
    color: '#1e293b',
    lineHeight: 18,
  },
  mono: { fontFamily: 'monospace', fontSize: 12 },

  mapsBtn: {
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  mapsBtnText: { color: '#0369a1', fontWeight: '600', fontSize: 13 },
});
