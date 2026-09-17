/**
 * TrackComplaintScreen — list and manage filed complaints.
 *
 * Improvements over the original:
 *  - Pull-to-refresh.
 *  - Status filter chips (All / Open / In Progress / Resolved / Rejected).
 *  - Live search bar filtering by tracking number, category, or description.
 *  - Tapping a card opens ComplaintDetailScreen.
 *  - Delete action (swipe-to-reveal via long-press → ConfirmModal) for Open complaints.
 *  - Empty + error states use shared components.
 *  - Status badge uses StatusBadge component.
 *  - Relative timestamps via formatters.
 */
import { useCallback, useState } from 'react';
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { api } from '../api';
import { useComplaints, KNOWN_STATUSES, STATUS_ALL } from '../hooks/useComplaints';
import StatusBadge from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';
import ConfirmModal from '../components/ConfirmModal';
import ComplaintDetailScreen from './ComplaintDetailScreen';
import { formatDefectType, formatRelativeTime, formatTrackingNumber } from '../utils/formatters';

const STATUS_LABELS = {
  [STATUS_ALL]: 'All',
  open: 'Open',
  in_progress: 'In Progress',
  resolved: 'Resolved',
  rejected: 'Rejected',
};

export default function TrackComplaintScreen() {
  const [statusFilter, setStatusFilter] = useState(STATUS_ALL);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedComplaint, setSelectedComplaint] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteErr, setDeleteErr] = useState('');

  const {
    complaints,
    loading,
    refreshing,
    error,
    refresh,
    removeById,
  } = useComplaints({ statusFilter, searchQuery });

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteErr('');
    try {
      await api.deleteComplaint(deleteTarget.id);
      removeById(deleteTarget.id);
      setDeleteTarget(null);
    } catch (e) {
      setDeleteErr(e.message || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, removeById]);

  // Detail view navigation
  if (selectedComplaint) {
    return (
      <ComplaintDetailScreen
        complaint={selectedComplaint}
        onBack={() => setSelectedComplaint(null)}
      />
    );
  }

  const renderItem = ({ item }) => (
    <Pressable
      style={styles.card}
      onPress={() => setSelectedComplaint(item)}
      onLongPress={() => {
        const status = String(item.status || '').toLowerCase();
        if (status === 'open' || status === 'pending') {
          setDeleteTarget(item);
        }
      }}
      accessibilityHint="Tap to view details. Long press to delete if open."
    >
      <View style={styles.cardHeader}>
        <Text style={styles.tn} numberOfLines={1}>
          {formatTrackingNumber(item.tracking_number)}
        </Text>
        <StatusBadge status={item.status} small />
      </View>
      <Text style={styles.meta} numberOfLines={1}>
        {formatDefectType(item.defect_type)}
        {item.created_at ? `  ·  ${formatRelativeTime(item.created_at)}` : ''}
      </Text>
      {item.description ? (
        <Text style={styles.desc} numberOfLines={2}>
          {item.description}
        </Text>
      ) : null}
    </Pressable>
  );

  return (
    <View style={styles.root}>
      {/* Search bar */}
      <View style={styles.searchRow}>
        <TextInput
          style={styles.searchInput}
          value={searchQuery}
          onChangeText={setSearchQuery}
          placeholder="Search by tracking #, category…"
          clearButtonMode="while-editing"
          returnKeyType="search"
          autoCapitalize="none"
          autoCorrect={false}
        />
        {searchQuery.length > 0 && (
          <Pressable onPress={() => setSearchQuery('')} style={styles.clearBtn}>
            <Text style={styles.clearText}>✕</Text>
          </Pressable>
        )}
      </View>

      {/* Status filter chips */}
      <FlatList
        horizontal
        data={KNOWN_STATUSES}
        keyExtractor={(s) => s}
        style={styles.chipScroll}
        contentContainerStyle={styles.chipContainer}
        showsHorizontalScrollIndicator={false}
        renderItem={({ item: s }) => (
          <Pressable
            style={[styles.chip, statusFilter === s && styles.chipOn]}
            onPress={() => setStatusFilter(s)}
          >
            <Text style={[styles.chipText, statusFilter === s && styles.chipTextOn]}>
              {STATUS_LABELS[s] || s}
            </Text>
          </Pressable>
        )}
      />

      {/* Error */}
      {!!error && (
        <ErrorBanner message={error} type="error" style={styles.banner} />
      )}
      {!!deleteErr && (
        <ErrorBanner
          message={deleteErr}
          type="error"
          onDismiss={() => setDeleteErr('')}
          style={styles.banner}
        />
      )}

      {/* List */}
      <FlatList
        data={complaints}
        keyExtractor={(i) => String(i.id)}
        contentContainerStyle={styles.listContent}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={refresh}
            colors={['#0369a1']}
            tintColor="#0369a1"
          />
        }
        ListHeaderComponent={
          !loading && complaints.length > 0 ? (
            <Text style={styles.countLabel}>
              {complaints.length} complaint{complaints.length !== 1 ? 's' : ''}
            </Text>
          ) : null
        }
        ListEmptyComponent={
          loading ? null : (
            <EmptyState
              icon={searchQuery || statusFilter !== STATUS_ALL ? '🔍' : '📋'}
              title={
                searchQuery || statusFilter !== STATUS_ALL
                  ? 'No matching complaints'
                  : 'No complaints yet'
              }
              subtitle={
                searchQuery || statusFilter !== STATUS_ALL
                  ? 'Try a different filter or search term.'
                  : 'File a complaint from the File tab and it will appear here.'
              }
            />
          )
        }
        renderItem={renderItem}
      />

      {/* Delete confirmation */}
      <ConfirmModal
        visible={!!deleteTarget}
        title="Delete complaint?"
        message={`This will permanently delete complaint ${deleteTarget?.tracking_number || ''}. This action cannot be undone.`}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        destructive
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => {
          setDeleteTarget(null);
          setDeleteErr('');
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },

  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    backgroundColor: '#f1f5f9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    color: '#0f172a',
  },
  clearBtn: { marginLeft: 8, padding: 4 },
  clearText: { color: '#94a3b8', fontSize: 14 },

  chipScroll: { maxHeight: 50, flexGrow: 0, backgroundColor: '#fff', borderBottomWidth: 1, borderBottomColor: '#e2e8f0' },
  chipContainer: { paddingHorizontal: 12, paddingVertical: 8, gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  chipOn: { backgroundColor: '#0369a1', borderColor: '#0369a1' },
  chipText: { fontSize: 12, color: '#475569', fontWeight: '500' },
  chipTextOn: { color: '#fff', fontWeight: '700' },

  banner: { marginHorizontal: 12, marginTop: 8 },

  listContent: { padding: 12, gap: 10, paddingBottom: 24 },
  countLabel: { fontSize: 12, color: '#94a3b8', marginBottom: 6, textAlign: 'right' },

  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
    gap: 8,
  },
  tn: { fontWeight: '700', fontSize: 14, flex: 1, color: '#0f172a' },
  meta: { color: '#64748b', fontSize: 12, marginBottom: 4 },
  desc: { color: '#475569', fontSize: 12, lineHeight: 17, marginTop: 2 },
});
