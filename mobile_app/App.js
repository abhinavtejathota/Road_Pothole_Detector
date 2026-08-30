import { useCallback, useEffect, useState } from 'react';
import { View, Text, Pressable, ActivityIndicator, StyleSheet, Image } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { api } from './src/api';
import ErrorBoundary from './src/components/ErrorBoundary';
import LoginScreen from './src/screens/LoginScreen';
import DashboardScreen, { clearDashboardCache } from './src/screens/DashboardScreen';
import SurveyRouteScreen from './src/screens/SurveyRouteScreen';
import CaptureScreen from './src/screens/CaptureScreen';

function AppBody() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  const [screen, setScreen] = useState('home');
  const [sealTick, setSealTick] = useState(0);

  const boot = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  const logout = async () => {
    await api.logout();
    clearDashboardCache();
    setUser(null);
    setScreen('home');
    setSealTick(0);
  };

  const onUserUpdated = useCallback((payload) => {
    setUser((prev) => {
      if (!prev) return prev;
      const fromUser = payload?.user && typeof payload.user === 'object' ? payload.user : null;
      const ids = (payload?.district_ids || fromUser?.district_ids || []).map(String);
      const districtId = payload?.district_id ?? fromUser?.district_id ?? (ids[0] ? Number(ids[0]) : null);
      return {
        ...prev,
        ...(fromUser || {}),
        district_ids: ids.length ? ids.map(Number) : (prev.district_ids || []),
        district_id: districtId != null ? Number(districtId) : prev.district_id,
      };
    });
  }, []);

  if (booting) {
    return (
      <View style={styles.boot}>
        <Image source={require('./assets/icon.png')} style={styles.bootLogo} />
        <Text style={styles.bootBrand}>SmartRoad Field</Text>
        <ActivityIndicator color="#E8B40A" size="large" style={{ marginTop: 20 }} />
        <StatusBar style="light" />
      </View>
    );
  }

  if (!user) {
    return (
      <>
        <LoginScreen onLoggedIn={setUser} />
        <StatusBar style="light" />
      </>
    );
  }

  if (!user.is_videographer) {
    return (
      <View style={styles.center}>
        <Image source={require('./assets/icon.png')} style={styles.smallLogo} />
        <Text style={styles.warn}>This app is for videographers only.</Text>
        <Text style={styles.meta}>
          {user.username} · {user.role}
        </Text>
        <Pressable style={styles.btn} onPress={logout}>
          <Text style={styles.btnText}>Logout</Text>
        </Pressable>
        <StatusBar style="dark" />
      </View>
    );
  }

  if (screen === 'route') {
    return (
      <>
        <SurveyRouteScreen
          user={user}
          onBack={() => setScreen('home')}
          onUserUpdated={onUserUpdated}
          onAssigned={(opts) => {
            // Custom → Capture; corridor → dashboard map first
            setScreen(opts?.goCapture ? 'capture' : 'home');
          }}
        />
        <StatusBar style="dark" />
      </>
    );
  }

  if (screen === 'capture') {
    return (
      <>
        <CaptureScreen
          onBack={(sealed) => {
            setScreen('home');
            // Only animate seal after a successful upload — discard/back keep indigo
            if (sealed === true) setSealTick((n) => n + 1);
            else setSealTick(0);
          }}
        />
        <StatusBar style="dark" />
      </>
    );
  }

  return (
    <>
      <DashboardScreen
        user={user}
        onLogout={logout}
        onSelectRoute={() => setScreen('route')}
        onCapture={() => setScreen('capture')}
        onUserUpdated={onUserUpdated}
        sealTick={sealTick}
      />
      <StatusBar style="light" />
    </>
  );
}

const styles = StyleSheet.create({
  boot: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#0B3D5C',
    padding: 24,
  },
  bootLogo: { width: 108, height: 108, borderRadius: 24 },
  bootBrand: {
    marginTop: 16,
    fontSize: 22,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: 0.3,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
    padding: 24,
  },
  smallLogo: { width: 64, height: 64, borderRadius: 14, marginBottom: 16 },
  warn: { fontSize: 16, fontWeight: '600', color: '#0B3D5C', marginBottom: 8 },
  meta: { color: '#6b7c8a', marginBottom: 16 },
  btn: {
    backgroundColor: '#0B3D5C',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 8,
  },
  btnText: { color: '#fff', fontWeight: '600' },
});

export default function App() {
  const [resetKey, setResetKey] = useState(0);
  return (
    <ErrorBoundary
      key={resetKey}
      onReset={() => setResetKey((k) => k + 1)}
    >
      <AppBody />
    </ErrorBoundary>
  );
}
