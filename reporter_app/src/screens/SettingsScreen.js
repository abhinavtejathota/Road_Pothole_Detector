/**
 * SettingsScreen — app-wide settings and developer options.
 *
 * Sections:
 *  1. Server — change the API base URL (persisted via AsyncStorage).
 *  2. About — app version, build details.
 *  3. Developer — toggle __DEV__ info display, clear stored token.
 *
 * Props:
 *   onClose   fn   — dismiss / go back
 */
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { getBaseUrl, setBaseUrl, setToken } from '../api';
import ErrorBanner from '../components/ErrorBanner';
import ConfirmModal from '../components/ConfirmModal';
import { validateApiBaseUrl } from '../utils/validators';

const APP_VERSION = '1.0.0';
const BUILD_DATE = '2025';

export default function SettingsScreen({ onClose }) {
  const [baseUrl, setBaseUrlState] = useState('');
  const [baseUrlDraft, setBaseUrlDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [saveErr, setSaveErr] = useState('');
  const [showClearToken, setShowClearToken] = useState(false);

  useEffect(() => {
    getBaseUrl().then((url) => {
      setBaseUrlState(url);
      setBaseUrlDraft(url);
    });
  }, []);

  const urlValidation = validateApiBaseUrl(baseUrlDraft);
  const urlDirty = baseUrlDraft !== baseUrl;

  const saveBaseUrl = useCallback(async () => {
    setSaveErr('');
    setSaveMsg('');
    const result = validateApiBaseUrl(baseUrlDraft);
    if (!result.valid) {
      setSaveErr(result.message);
      return;
    }
    setSaving(true);
    try {
      await setBaseUrl(baseUrlDraft);
      setBaseUrlState(baseUrlDraft);
      setSaveMsg('Server URL saved.');
    } catch (e) {
      setSaveErr(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }, [baseUrlDraft]);

  const resetUrl = useCallback(() => {
    setBaseUrlDraft(baseUrl);
    setSaveErr('');
    setSaveMsg('');
  }, [baseUrl]);

  const clearToken = useCallback(async () => {
    await setToken('');
    setShowClearToken(false);
    Alert.alert('Done', 'Auth token cleared. The app will ask you to log in again.');
  }, []);

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Settings</Text>
          <Pressable onPress={onClose} hitSlop={12}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>

        {/* ── Server section ── */}
        <Text style={styles.sectionTitle}>Server</Text>
        <View style={styles.card}>
          <Text style={styles.fieldLabel}>API Base URL</Text>
          <TextInput
            style={[
              styles.input,
              !urlValidation.valid && baseUrlDraft.length > 0 && styles.inputErr,
            ]}
            value={baseUrlDraft}
            onChangeText={(t) => {
              setBaseUrlDraft(t.trim());
              setSaveErr('');
              setSaveMsg('');
            }}
            placeholder="http://host:5005"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
          />
          {!urlValidation.valid && baseUrlDraft.length > 0 && (
            <Text style={styles.fieldErr}>{urlValidation.message}</Text>
          )}
          <Text style={styles.fieldHint}>
            Used for all API requests. Change this if you are running the server on a different
            host or port.
          </Text>

          {!!saveErr && (
            <ErrorBanner
              message={saveErr}
              type="error"
              onDismiss={() => setSaveErr('')}
            />
          )}
          {!!saveMsg && (
            <ErrorBanner
              message={saveMsg}
              type="success"
              onDismiss={() => setSaveMsg('')}
            />
          )}

          <View style={styles.btnRow}>
            {urlDirty && (
              <Pressable style={styles.secondaryBtn} onPress={resetUrl} disabled={saving}>
                <Text style={styles.secondaryBtnText}>Reset</Text>
              </Pressable>
            )}
            <Pressable
              style={[styles.primaryBtn, (!urlDirty || !urlValidation.valid || saving) && styles.btnOff]}
              onPress={saveBaseUrl}
              disabled={!urlDirty || !urlValidation.valid || saving}
            >
              {saving ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.primaryBtnText}>Save</Text>
              )}
            </Pressable>
          </View>
        </View>

        {/* ── About section ── */}
        <Text style={styles.sectionTitle}>About</Text>
        <View style={styles.card}>
          <InfoRow label="App" value="SmartRoad Citizen Reporter" />
          <InfoRow label="Version" value={APP_VERSION} />
          <InfoRow label="Build year" value={BUILD_DATE} />
          <InfoRow label="Platform" value={Platform.OS === 'ios' ? 'iOS' : Platform.OS === 'android' ? 'Android' : Platform.OS} />
        </View>

        {/* ── Developer section ── */}
        {__DEV__ && (
          <>
            <Text style={styles.sectionTitle}>Developer</Text>
            <View style={styles.card}>
              <InfoRow label="Mode" value={__DEV__ ? 'Development' : 'Production'} />
              <InfoRow label="Current base URL" value={baseUrl} mono />
              <View style={styles.divider} />
              <Pressable
                style={styles.dangerBtn}
                onPress={() => setShowClearToken(true)}
              >
                <Text style={styles.dangerBtnText}>Clear auth token</Text>
              </Pressable>
            </View>
          </>
        )}

        <ConfirmModal
          visible={showClearToken}
          title="Clear auth token?"
          message="This will sign you out immediately. You will need to verify your mobile number again."
          confirmLabel="Clear token"
          cancelLabel="Cancel"
          destructive
          onConfirm={clearToken}
          onCancel={() => setShowClearToken(false)}
        />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function InfoRow({ label, value, mono = false }) {
  return (
    <View style={infoStyles.row}>
      <Text style={infoStyles.label}>{label}</Text>
      <Text style={[infoStyles.value, mono && infoStyles.mono]} numberOfLines={2}>{value}</Text>
    </View>
  );
}

const infoStyles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  label: { width: 120, fontSize: 13, color: '#64748b', flexShrink: 0 },
  value: { flex: 1, fontSize: 13, color: '#1e293b' },
  mono: { fontFamily: 'monospace', fontSize: 11 },
});

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  content: { padding: 16, paddingBottom: 40 },

  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  },
  headerTitle: { fontSize: 20, fontWeight: '700', color: '#0f172a' },
  closeText: { fontSize: 18, color: '#64748b', fontWeight: '700' },

  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: '#94a3b8',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 8,
    marginTop: 16,
  },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    padding: 14,
    overflow: 'hidden',
  },

  fieldLabel: { fontSize: 12, color: '#64748b', marginBottom: 6 },
  input: {
    backgroundColor: '#f8fafc',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    fontFamily: 'monospace',
  },
  inputErr: { borderColor: '#fca5a5' },
  fieldErr: { color: '#dc2626', fontSize: 12, marginTop: 4 },
  fieldHint: { color: '#94a3b8', fontSize: 12, marginTop: 6, marginBottom: 12, lineHeight: 17 },

  btnRow: { flexDirection: 'row', gap: 8, justifyContent: 'flex-end' },
  primaryBtn: {
    backgroundColor: '#0369a1',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
    minWidth: 70,
  },
  primaryBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  btnOff: { opacity: 0.5 },
  secondaryBtn: {
    backgroundColor: '#f1f5f9',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
  },
  secondaryBtnText: { color: '#334155', fontWeight: '600', fontSize: 14 },

  divider: { height: 1, backgroundColor: '#f1f5f9', marginVertical: 10 },
  dangerBtn: {
    borderWidth: 1,
    borderColor: '#fca5a5',
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: 'center',
  },
  dangerBtnText: { color: '#dc2626', fontWeight: '600', fontSize: 14 },
});
