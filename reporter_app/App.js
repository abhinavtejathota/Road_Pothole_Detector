/**
 * App.js — root component for the SmartRoad Citizen Reporter app.
 *
 * Changes vs original:
 *  - Uses the centralised useAuth hook instead of inline boot logic.
 *  - Adds a Profile tab (useComplaints provides the data for it).
 *  - Adds a Settings screen accessible via a gear icon in the header.
 *  - Flushes any offline-queued drafts on boot / when the user logs in.
 *  - Pass onTabChange to ProfileScreen so it can navigate to other tabs.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
  Image,
  ActivityIndicator,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { useAuth } from './src/hooks/useAuth';
import { useComplaints } from './src/hooks/useComplaints';
import { flushDraftQueue } from './src/api';
import LoginScreen from './src/screens/LoginScreen';
import FileComplaintScreen from './src/screens/FileComplaintScreen';
import TrackComplaintScreen from './src/screens/TrackComplaintScreen';
import ProfileScreen from './src/screens/ProfileScreen';
import SettingsScreen from './src/screens/SettingsScreen';

const TABS = [
  { id: 'file', label: 'File', icon: '📝' },
  { id: 'track', label: 'Track', icon: '📍' },
  { id: 'profile', label: 'Profile', icon: '👤' },
];

export default function App() {
  const { reporter, booting, login, logout } = useAuth();
  const [tab, setTab] = useState('file');
  const [showSettings, setShowSettings] = useState(false);

  // Load complaints for ProfileScreen stats — only active once logged in.
  // Passing a flag so the hook skips the API call when reporter is null.
  const { allComplaints } = useComplaints({ disabled: !reporter });

  // Flush offline draft queue whenever a session becomes active
  useEffect(() => {
    if (!reporter) return;
    flushDraftQueue().catch(() => {
      /* silent — drafts will retry next time */
    });
  }, [reporter]);

  const handleLogin = useCallback(
    async (authPayload) => {
      await login(authPayload);
      setTab('file');
    },
    [login],
  );

  const handleLogout = useCallback(async () => {
    await logout();
    setTab('file');
  }, [logout]);

  // ── Boot splash ──────────────────────────────────────────────────────────
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

  // ── Login screen ─────────────────────────────────────────────────────────
  if (!reporter) {
    return (
      <>
        <LoginScreen onLoggedIn={handleLogin} />
        <StatusBar style="light" />
      </>
    );
  }

  // ── Main app shell ───────────────────────────────────────────────────────
  return (
    <View style={styles.shell}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Citizen Reporting</Text>
        <View style={styles.headerRight}>
          {reporter.mobile_masked ? (
            <Text style={styles.mobile} numberOfLines={1}>
              {reporter.mobile_masked}
            </Text>
          ) : null}
          <Pressable
            onPress={() => setShowSettings(true)}
            hitSlop={8}
            accessibilityLabel="Settings"
          >
            <Text style={styles.settingsIcon}>⚙</Text>
          </Pressable>
        </View>
      </View>

      {/* Screen body */}
      <View style={styles.body}>
        {tab === 'file' && <FileComplaintScreen />}
        {tab === 'track' && <TrackComplaintScreen />}
        {tab === 'profile' && (
          <ProfileScreen
            reporter={reporter}
            complaints={allComplaints}
            onLogout={handleLogout}
            onTabChange={(newTab) => setTab(newTab)}
          />
        )}
      </View>

      {/* Bottom tab bar */}
      <View style={styles.tabs}>
        {TABS.map((t) => (
          <Pressable
            key={t.id}
            style={[styles.tab, tab === t.id && styles.tabOn]}
            onPress={() => setTab(t.id)}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === t.id }}
          >
            <Text style={styles.tabIcon}>{t.icon}</Text>
            <Text style={[styles.tabText, tab === t.id && styles.tabTextOn]}>
              {t.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Settings modal */}
      <Modal
        visible={showSettings}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setShowSettings(false)}
      >
        <SettingsScreen onClose={() => setShowSettings(false)} />
      </Modal>

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
  mobile: { color: '#94a3b8', fontSize: 12, maxWidth: 120 },
  settingsIcon: { color: '#e2e8f0', fontSize: 20 },

  body: { flex: 1 },

  tabs: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: '#e2e8f0',
    backgroundColor: '#fff',
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
    gap: 2,
  },
  tabOn: { borderTopWidth: 2, borderTopColor: '#0369a1' },
  tabIcon: { fontSize: 18 },
  tabText: { color: '#64748b', fontWeight: '500', fontSize: 11 },
  tabTextOn: { color: '#0369a1', fontWeight: '700' },
});
