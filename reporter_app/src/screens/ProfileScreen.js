/**
 * ProfileScreen — displays the logged-in reporter's profile and complaint statistics.
 *
 * Shows:
 *  - Masked mobile number and account creation date.
 *  - Summary counts: total, open, resolved, rejected complaints.
 *  - A list of recent (last 5) complaints with their status.
 *  - Sign-out action (with a confirm modal).
 *
 * Props:
 *   reporter   object   — from useAuth (has .mobile_masked, .created_at, .name, etc.)
 *   complaints array    — full complaints array from useComplaints
 *   onLogout   fn       — called after logout confirmed
 *   onTabChange fn      — fn(tabId: string) to switch tabs from inside this screen
 */
import { useCallback, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import StatusBadge from '../components/StatusBadge';
import ConfirmModal from '../components/ConfirmModal';
import EmptyState from '../components/EmptyState';
import { formatDateTime, formatMobile, formatRelativeTime, formatDefectType } from '../utils/formatters';

const STAT_KEYS = [
  { key: 'total', label: 'Total', color: '#0369a1' },
  { key: 'open', label: 'Open', color: '#1d4ed8' },
  { key: 'in_progress', label: 'In Progress', color: '#b45309' },
  { key: 'resolved', label: 'Resolved', color: '#15803d' },
  { key: 'rejected', label: 'Rejected', color: '#b91c1c' },
];

export default function ProfileScreen({ reporter, complaints = [], onLogout, onTabChange }) {
  const [showLogout, setShowLogout] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const stats = useMemo(() => {
    const counts = { total: complaints.length, open: 0, in_progress: 0, resolved: 0, rejected: 0 };
    for (const c of complaints) {
      const s = String(c.status || '').toLowerCase();
      if (s in counts) counts[s]++;
    }
    return counts;
  }, [complaints]);

  const recent = useMemo(
    () =>
      [...complaints]
        .sort((a, b) => (b.created_at > a.created_at ? 1 : -1))
        .slice(0, 5),
    [complaints],
  );

  const handleLogout = useCallback(async () => {
    setLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setLoggingOut(false);
      setShowLogout(false);
    }
  }, [onLogout]);

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {/* Profile card */}
      <View style={styles.profileCard}>
        <View style={styles.avatarCircle}>
          <Text style={styles.avatarText}>
            {(reporter?.mobile_masked || '?').slice(-2)}
          </Text>
        </View>
        <View style={styles.profileInfo}>
          <Text style={styles.mobileText}>
            {reporter?.name
              ? reporter.name
              : formatMobile(reporter?.mobile || reporter?.mobile_masked || '')}
          </Text>
          {reporter?.mobile_masked && reporter?.name && (
            <Text style={styles.mobileSubText}>{reporter.mobile_masked}</Text>
          )}
          {reporter?.created_at && (
            <Text style={styles.memberSince}>
              Member since {formatDateTime(reporter.created_at)}
            </Text>
          )}
        </View>
      </View>

      {/* Stats row */}
      <Text style={styles.sectionTitle}>Your Activity</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.statsRow}
      >
        {STAT_KEYS.map(({ key, label, color }) => (
          <View key={key} style={styles.statCard}>
            <Text style={[styles.statCount, { color }]}>{stats[key] ?? 0}</Text>
            <Text style={styles.statLabel}>{label}</Text>
          </View>
        ))}
      </ScrollView>

      {/* Recent complaints */}
      <Text style={styles.sectionTitle}>Recent Complaints</Text>
      {recent.length === 0 ? (
        <EmptyState
          icon="📋"
          title="No complaints yet"
          subtitle="File a complaint from the File tab."
          action="File a complaint"
          onAction={() => onTabChange && onTabChange('file')}
          style={styles.emptyInline}
        />
      ) : (
        recent.map((item) => (
          <View key={item.id} style={styles.recentCard}>
            <View style={styles.recentRow}>
              <Text style={styles.tn} numberOfLines={1}>{item.tracking_number}</Text>
              <StatusBadge status={item.status} small />
            </View>
            <Text style={styles.recentMeta} numberOfLines={1}>
              {formatDefectType(item.defect_type)}
              {item.created_at ? `  ·  ${formatRelativeTime(item.created_at)}` : ''}
            </Text>
          </View>
        ))
      )}

      {recent.length > 0 && (
        <Pressable
          style={styles.viewAllBtn}
          onPress={() => onTabChange && onTabChange('track')}
        >
          <Text style={styles.viewAllText}>View all complaints →</Text>
        </Pressable>
      )}

      {/* Sign out */}
      <View style={styles.dangerSection}>
        <Pressable style={styles.logoutBtn} onPress={() => setShowLogout(true)}>
          <Text style={styles.logoutText}>Sign out</Text>
        </Pressable>
      </View>

      <ConfirmModal
        visible={showLogout}
        title="Sign out"
        message="Are you sure you want to sign out? You will need your mobile number and an OTP to sign back in."
        confirmLabel="Sign out"
        cancelLabel="Cancel"
        destructive
        loading={loggingOut}
        onConfirm={handleLogout}
        onCancel={() => setShowLogout(false)}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  content: { padding: 16, paddingBottom: 40 },

  /* Profile card */
  profileCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    gap: 14,
  },
  avatarCircle: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: '#0369a1',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  avatarText: { color: '#fff', fontWeight: '700', fontSize: 18 },
  profileInfo: { flex: 1 },
  mobileText: { fontSize: 16, fontWeight: '700', color: '#0f172a' },
  mobileSubText: { fontSize: 13, color: '#64748b', marginTop: 2 },
  memberSince: { fontSize: 11, color: '#94a3b8', marginTop: 4 },

  /* Stats */
  sectionTitle: { fontSize: 13, fontWeight: '700', color: '#64748b', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 0.5 },
  statsRow: { gap: 10, paddingBottom: 16 },
  statCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    paddingHorizontal: 18,
    paddingVertical: 14,
    alignItems: 'center',
    minWidth: 80,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  statCount: { fontSize: 26, fontWeight: '800' },
  statLabel: { fontSize: 11, color: '#64748b', marginTop: 2 },

  /* Recent */
  recentCard: {
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  recentRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  tn: { fontWeight: '700', fontSize: 13, flex: 1, marginRight: 8 },
  recentMeta: { fontSize: 12, color: '#64748b' },

  viewAllBtn: { alignItems: 'center', marginTop: 4, marginBottom: 24 },
  viewAllText: { color: '#0369a1', fontWeight: '600', fontSize: 14 },

  emptyInline: { minHeight: 120 },

  /* Logout */
  dangerSection: { marginTop: 12 },
  logoutBtn: {
    borderWidth: 1.5,
    borderColor: '#fca5a5',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  logoutText: { color: '#dc2626', fontWeight: '700', fontSize: 15 },
});
