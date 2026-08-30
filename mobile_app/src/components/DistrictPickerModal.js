import { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  Modal,
  Pressable,
  StyleSheet,
  TextInput,
  FlatList,
  ActivityIndicator,
} from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import { api } from '../api';
import { colors } from '../theme';

const PAGE_SIZE = 8;

function idsFromUser(user) {
  const ids = (user?.district_ids || []).map(String);
  if (ids.length) return ids;
  if (user?.district_id != null) return [String(user.district_id)];
  return [];
}

function stateKeyFromUser(user) {
  const sid = user?.state_id;
  return sid === 2 || sid === '2' ? 'telangana' : 'andhra';
}

/**
 * District multi-select modal: search, select all / clear all, paged list.
 * Saves via PATCH /api/survey/my-districts (at least one district required).
 */
export default function DistrictPickerModal({
  open,
  user,
  onClose,
  onSaved,
}) {
  const stateKey = stateKeyFromUser(user);
  const [allDistricts, setAllDistricts] = useState([]);
  const [selected, setSelected] = useState(() => new Set(idsFromUser(user)));
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [loadingList, setLoadingList] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return undefined;
    setSelected(new Set(idsFromUser(user)));
    setSearch('');
    setPage(0);
    setError('');
    let cancelled = false;
    (async () => {
      setLoadingList(true);
      try {
        const rows = await api.surveyDistricts(stateKey);
        if (!cancelled) setAllDistricts(Array.isArray(rows) ? rows : []);
      } catch (e) {
        if (!cancelled) {
          setAllDistricts([]);
          setError(e.message || 'Could not load districts');
        }
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, user, stateKey]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = allDistricts || [];
    if (!q) return rows;
    return rows.filter((d) => {
      const name = String(d.name || '').toLowerCase();
      const id = String(d.district_id || '');
      return name.includes(q) || id.includes(q);
    });
  }, [allDistricts, search]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [search]);

  const toggle = (did) => {
    const id = String(did);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setError('');
  };

  const selectAllFiltered = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      filtered.forEach((d) => next.add(String(d.district_id)));
      return next;
    });
    setError('');
  };

  const clearAll = () => {
    setSelected(new Set());
    setError('');
  };

  const save = async () => {
    const ids = [...selected].map(String);
    if (!ids.length) {
      setError('Keep at least one district selected');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const res = await api.updateMyDistricts(ids.map(Number));
      const userRow = res?.user || null;
      const savedIds = (res?.district_ids || ids).map(String);
      onSaved?.({
        district_ids: savedIds,
        district_id: savedIds[0] ? Number(savedIds[0]) : null,
        user: userRow,
      });
      onClose?.();
    } catch (e) {
      setError(e.message || 'Could not update districts');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal visible={open} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Districts</Text>
            <Pressable onPress={onClose} hitSlop={10} disabled={saving}>
              <MaterialIcons name="close" size={22} color={colors.navy} />
            </Pressable>
          </View>

          <TextInput
            style={styles.search}
            value={search}
            onChangeText={setSearch}
            placeholder="Search districts…"
            placeholderTextColor={colors.muted}
            editable={!saving}
          />

          <View style={styles.toolbar}>
            <Pressable onPress={selectAllFiltered} disabled={saving || !filtered.length} style={styles.toolBtn}>
              <Text style={styles.toolBtnText}>Select all</Text>
            </Pressable>
            <Pressable onPress={clearAll} disabled={saving} style={styles.toolBtn}>
              <Text style={styles.toolBtnText}>Clear all</Text>
            </Pressable>
            <Text style={styles.count}>{selected.size} selected</Text>
          </View>

          {loadingList ? (
            <View style={styles.center}>
              <ActivityIndicator color={colors.navy} />
              <Text style={styles.muted}>Loading districts…</Text>
            </View>
          ) : (
            <FlatList
              data={pageRows}
              keyExtractor={(d) => String(d.district_id)}
              style={styles.list}
              renderItem={({ item }) => {
                const id = String(item.district_id);
                const on = selected.has(id);
                return (
                  <Pressable
                    style={[styles.row, on && styles.rowOn]}
                    onPress={() => toggle(id)}
                    disabled={saving}
                  >
                    <MaterialIcons
                      name={on ? 'check-box' : 'check-box-outline-blank'}
                      size={22}
                      color={on ? colors.navy : colors.muted}
                    />
                    <Text style={[styles.rowText, on && styles.rowTextOn]} numberOfLines={2}>
                      {item.name || id}
                    </Text>
                  </Pressable>
                );
              }}
              ListEmptyComponent={
                <Text style={[styles.muted, { textAlign: 'center', padding: 16 }]}>
                  {search.trim() ? 'No districts match your search.' : 'No districts available.'}
                </Text>
              }
            />
          )}

          {filtered.length > PAGE_SIZE ? (
            <View style={styles.pager}>
              <Pressable
                style={[styles.pageBtn, safePage <= 0 && styles.pageBtnDisabled]}
                disabled={safePage <= 0 || saving}
                onPress={() => setPage((p) => Math.max(0, p - 1))}
              >
                <MaterialIcons name="chevron-left" size={22} color={colors.navy} />
                <Text style={styles.pageBtnText}>Prev</Text>
              </Pressable>
              <Text style={styles.pageLabel}>
                {safePage + 1} / {pageCount}
              </Text>
              <Pressable
                style={[styles.pageBtn, safePage >= pageCount - 1 && styles.pageBtnDisabled]}
                disabled={safePage >= pageCount - 1 || saving}
                onPress={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              >
                <Text style={styles.pageBtnText}>Next</Text>
                <MaterialIcons name="chevron-right" size={22} color={colors.navy} />
              </Pressable>
            </View>
          ) : null}

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <View style={styles.footer}>
            <Pressable style={styles.cancelBtn} onPress={onClose} disabled={saving}>
              <Text style={styles.cancelText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.saveBtn, saving && { opacity: 0.6 }]}
              onPress={save}
              disabled={saving || loadingList}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.saveText}>Save</Text>
              )}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.white,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    maxHeight: '88%',
    paddingBottom: 20,
    paddingTop: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    marginBottom: 8,
  },
  title: { fontSize: 18, fontWeight: '800', color: colors.navy },
  search: {
    marginHorizontal: 16,
    borderWidth: 1,
    borderColor: colors.inputBorder,
    backgroundColor: colors.inputBg,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: colors.navy,
  },
  toolbar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  toolBtn: { paddingVertical: 4 },
  toolBtnText: { color: colors.navy, fontWeight: '700', fontSize: 13 },
  count: { marginLeft: 'auto', color: colors.muted, fontSize: 12 },
  list: { flexGrow: 0, maxHeight: 360 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  rowOn: { backgroundColor: colors.tonalBg },
  rowText: { flex: 1, color: colors.navy, fontSize: 15 },
  rowTextOn: { fontWeight: '700' },
  center: { padding: 28, alignItems: 'center', gap: 10 },
  muted: { color: colors.muted, fontSize: 13 },
  pager: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 10,
  },
  pageBtn: { flexDirection: 'row', alignItems: 'center', gap: 2, padding: 6 },
  pageBtnDisabled: { opacity: 0.35 },
  pageBtnText: { color: colors.navy, fontWeight: '600' },
  pageLabel: { color: colors.muted, fontSize: 13 },
  error: { color: colors.danger, paddingHorizontal: 16, paddingTop: 8, fontSize: 13 },
  footer: {
    flexDirection: 'row',
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 14,
  },
  cancelBtn: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: colors.navy,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
  },
  cancelText: { color: colors.navy, fontWeight: '700' },
  saveBtn: {
    flex: 1,
    backgroundColor: colors.navy,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
  },
  saveText: { color: '#fff', fontWeight: '700' },
});
