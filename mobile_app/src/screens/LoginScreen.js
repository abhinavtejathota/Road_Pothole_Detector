import { useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ActivityIndicator,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  Image,
  ScrollView,
} from 'react-native';
import { api, getBaseUrl, setBaseUrl, DEFAULT_BASE, IS_DEV_API, resetBaseUrlToDefault } from '../api';
import { colors, buttonStyles } from '../theme';

export default function LoginScreen({ onLoggedIn }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [serverUrl, setServerUrl] = useState(DEFAULT_BASE);
  const [showServer, setShowServer] = useState(IS_DEV_API);

  useEffect(() => {
    getBaseUrl().then(setServerUrl).catch(() => {});
  }, []);

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const clean = String(serverUrl || '').trim().replace(/\/+$/, '');
      if (clean) await setBaseUrl(clean);
      const user = await api.login(username.trim(), password);
      onLoggedIn(user);
    } catch (e) {
      setError(e.message || 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  const useDefault = async () => {
    const url = await resetBaseUrlToDefault();
    setServerUrl(url);
  };

  return (
    <View style={styles.root}>
      <Image
        source={require('../../assets/login-bg.png')}
        style={StyleSheet.absoluteFill}
        resizeMode="cover"
        accessibilityElementsHidden
        importantForAccessibility="no"
      />
      {/* Same navy scrim feel as web .login-bg-scrim */}
      <View style={styles.scrim} pointerEvents="none" />

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.hero}>
            <Image source={require('../../assets/icon.png')} style={styles.logo} />
            <Text style={styles.brand}>SmartRoad</Text>
            <Text style={styles.tagline}>Field survey for Andhra Pradesh roads</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Welcome back</Text>
            <Text style={styles.cardSub}>Sign in with your videographer account</Text>

            <Text style={styles.label}>Username</Text>
            <TextInput
              style={styles.input}
              autoCapitalize="none"
              autoCorrect={false}
              value={username}
              onChangeText={setUsername}
              placeholder="username"
              placeholderTextColor="#9aabb8"
              editable={!busy}
            />
            <Text style={styles.label}>Password</Text>
            <TextInput
              style={styles.input}
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              placeholder="••••••••"
              placeholderTextColor="#9aabb8"
              editable={!busy}
              onSubmitEditing={submit}
            />
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Pressable onPress={() => setShowServer((v) => !v)} hitSlop={8}>
              <Text style={styles.serverHint}>
                {showServer ? 'Hide server' : 'Server settings'} · {IS_DEV_API ? 'dev' : 'prod'}
              </Text>
            </Pressable>
            {showServer ? (
              <>
                <Text style={styles.label}>Server URL</Text>
                <TextInput
                  style={styles.input}
                  autoCapitalize="none"
                  autoCorrect={false}
                  value={serverUrl}
                  onChangeText={setServerUrl}
                  placeholder={DEFAULT_BASE}
                  placeholderTextColor="#9aabb8"
                  editable={!busy}
                />
                <Text style={styles.serverHint}>
                  Default: {DEFAULT_BASE}
                  {IS_DEV_API
                    ? ' (set EXPO_PUBLIC_API_BASE_DEV in mobile_app/.env for your LAN IP)'
                    : ' (release build uses EXPO_PUBLIC_API_BASE_PROD)'}
                </Text>
                <Pressable onPress={useDefault} disabled={busy} style={{ marginBottom: 10 }}>
                  <Text style={[styles.serverHint, { color: '#0B3D5C', fontWeight: '700' }]}>
                    Reset to build default
                  </Text>
                </Pressable>
              </>
            ) : (
              <Text style={styles.serverHint}>Server: {serverUrl}</Text>
            )}
            <Pressable
              style={[buttonStyles.gold, { marginTop: 4 }, busy && buttonStyles.disabled]}
              onPress={submit}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color={colors.navy} />
              ) : (
                <Text style={buttonStyles.goldText}>Sign in</Text>
              )}
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  flex: { flex: 1 },
  scrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(11, 44, 72, 0.72)',
  },
  scroll: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 24,
    paddingTop: 56,
    paddingBottom: 40,
  },
  hero: { alignItems: 'center', marginBottom: 28 },
  logo: {
    width: 96,
    height: 96,
    borderRadius: 22,
    marginBottom: 16,
    borderWidth: 3,
    borderColor: 'rgba(255,255,255,0.25)',
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
  brand: {
    fontSize: 32,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: 0.4,
  },
  tagline: {
    marginTop: 8,
    fontSize: 14,
    color: 'rgba(255,255,255,0.85)',
    textAlign: 'center',
    lineHeight: 20,
    maxWidth: 280,
  },
  card: {
    backgroundColor: colors.white,
    borderRadius: 20,
    padding: 22,
    shadowColor: '#000',
    shadowOpacity: 0.28,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 8,
  },
  cardTitle: { fontSize: 20, fontWeight: '800', color: colors.navy },
  cardSub: { marginTop: 4, marginBottom: 18, color: colors.muted, fontSize: 13 },
  label: {
    fontSize: 12,
    fontWeight: '700',
    color: '#4a5d6c',
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  input: {
    borderWidth: 1.5,
    borderColor: colors.inputBorder,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 13,
    marginBottom: 14,
    fontSize: 16,
    color: colors.navy,
    backgroundColor: colors.inputBg,
  },
  error: { color: colors.danger, marginBottom: 10, fontWeight: '600' },
  serverHint: { color: colors.muted, fontSize: 11, marginBottom: 10 },
});
