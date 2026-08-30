import { useCallback, useEffect, useState } from 'react';
import { View, Text, Pressable, StyleSheet, ActivityIndicator, Image } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { api, getToken, setToken } from './src/api';
import LoginScreen from './src/screens/LoginScreen';
import FileComplaintScreen from './src/screens/FileComplaintScreen';
import TrackComplaintScreen from './src/screens/TrackComplaintScreen';

const TABS = [
  { id: 'file', label: 'File' },
  { id: 'track', label: 'Track' },
];

export default function App() {
  const [reporter, setReporter] = useState(null);
  const [booting, setBooting] = useState(true);
  const [tab, setTab] = useState('file');

  const boot = useCallback(async () => {
    try {
      const tok = await getToken();
      if (!tok) {
        setReporter(null);
        return;
      }
      const me = await api.me();
      setReporter(me);
    } catch {
      await setToken('');
      setReporter(null);
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  const logout = async () => {
    await api.logout();
    setReporter(null);
    setTab('file');
  };

  if (booting) {
    return (
      <View style={styles.boot}>
        <Image source={require('./assets/icon.png')} style={styles.logo} />
        <Text style={styles.brand}>SmartRoad Citizen</Text>
        <ActivityIndicator color="#7dd3fc" style={{ marginTop: 16 }} />
        <StatusBar style="light" />
      </View>
    );
  }

  if (!reporter) {
    return (
      <>
        <LoginScreen
          onLoggedIn={async () => {
            const me = await api.me();
            setReporter(me);
          }}
        />
        <StatusBar style="light" />
      </>
    );
  }

  return (
    <View style={styles.shell}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Citizen Reporting</Text>
        <View style={styles.headerRight}>
          <Text style={styles.mobile}>{reporter.mobile_masked || ''}</Text>
          <Pressable onPress={logout}>
            <Text style={styles.signOut}>Sign out</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.body}>
        {tab === 'file' && <FileComplaintScreen />}
        {tab === 'track' && <TrackComplaintScreen />}
      </View>

      <View style={styles.tabs}>
        {TABS.map((t) => (
          <Pressable
            key={t.id}
            style={[styles.tab, tab === t.id && styles.tabOn]}
            onPress={() => setTab(t.id)}
          >
            <Text style={[styles.tabText, tab === t.id && styles.tabTextOn]}>{t.label}</Text>
          </Pressable>
        ))}
      </View>
      <StatusBar style="light" />
    </View>
  );
}

const styles = StyleSheet.create({
  boot: {
    flex: 1,
    backgroundColor: '#0f172a',
    alignItems: 'center',
    justifyContent: 'center',
  },
  logo: { width: 72, height: 72, borderRadius: 16 },
  brand: { color: '#f8fafc', fontSize: 18, fontWeight: '600', marginTop: 12 },
  shell: { flex: 1, backgroundColor: '#f4f7fb' },
  header: {
    backgroundColor: '#0f172a',
    paddingTop: 48,
    paddingHorizontal: 16,
    paddingBottom: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  headerTitle: { color: '#f8fafc', fontWeight: '700', fontSize: 16 },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  mobile: { color: '#94a3b8', fontSize: 12 },
  signOut: { color: '#e2e8f0', fontWeight: '600' },
  body: { flex: 1 },
  tabs: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: '#e2e8f0',
    backgroundColor: '#fff',
  },
  tab: { flex: 1, paddingVertical: 14, alignItems: 'center' },
  tabOn: { borderTopWidth: 2, borderTopColor: '#0369a1' },
  tabText: { color: '#64748b', fontWeight: '500' },
  tabTextOn: { color: '#0369a1', fontWeight: '700' },
});
