import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, ActivityIndicator, Linking, Pressable } from 'react-native';
import { api } from '../api';

export default function TrackComplaintScreen() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const out = await api.trackComplaints();
        setItems(out.complaints || []);
      } catch (e) {
        setError(e.message || 'Load failed');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#0369a1" />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Text style={styles.h}>Track complaints</Text>
      {!!error && <Text style={styles.err}>{error}</Text>}
      {!error && items.length === 0 && (
        <Text style={styles.empty}>No complaints yet. File one from the other tab.</Text>
      )}
      <FlatList
        data={items}
        keyExtractor={(i) => String(i.id)}
        contentContainerStyle={{ padding: 16, gap: 10 }}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <View style={styles.row}>
              <Text style={styles.tn}>{item.tracking_number}</Text>
              <Text style={styles.status}>{item.status}</Text>
            </View>
            <Text style={styles.meta}>
              {item.defect_type}
              {item.created_at ? ` · ${new Date(item.created_at).toLocaleString()}` : ''}
            </Text>
            {item.s3_url ? (
              <Pressable onPress={() => Linking.openURL(item.s3_url)}>
                <Text style={styles.link}>View media</Text>
              </Pressable>
            ) : null}
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  h: { fontSize: 20, fontWeight: '700', padding: 16, paddingBottom: 0 },
  empty: { padding: 16, color: '#64748b' },
  err: { padding: 16, color: '#b91c1c' },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  tn: { fontWeight: '700' },
  status: {
    fontSize: 11,
    backgroundColor: '#dbeafe',
    color: '#1d4ed8',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    overflow: 'hidden',
  },
  meta: { color: '#64748b', marginTop: 6, fontSize: 13 },
  link: { color: '#0369a1', marginTop: 8, fontSize: 13 },
});
