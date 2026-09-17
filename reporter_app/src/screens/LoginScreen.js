import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { api } from '../api';
import { formatIndianMobile, isValidIndianMobile, nationalMobileDigits } from '../mobile';

export default function LoginScreen({ onLoggedIn }) {
  const [mobile, setMobile] = useState('');
  const [otp, setOtp] = useState('');
  const [step, setStep] = useState('mobile');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');

  const mobileValid = isValidIndianMobile(mobile);

  const requestOtp = async ({ resend = false } = {}) => {
    setBusy(true);
    setError('');
    setHint('');
    if (resend) setOtp('');
    try {
      const out = await api.requestOtp(mobile, { resend });
      setStep('otp');
      let msg = out.message || 'OTP ready.';
      if (out.dev_otp) msg = `${msg} (dev OTP: ${out.dev_otp})`;
      setHint(msg);
    } catch (e) {
      setError(e.message || 'Could not send OTP');
    } finally {
      setBusy(false);
    }
  };

  const verifyOtp = async () => {
    setBusy(true);
    setError('');
    try {
      const out = await api.verifyOtp(mobile, otp);
      // Token storage and /me fetch are handled by useAuth.login (onLoggedIn).
      // Do NOT call setToken here — it would race with the hook.
      onLoggedIn(out);
    } catch (e) {
      setError(e.message || 'Invalid OTP');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Citizen Reporting</Text>
        <Text style={styles.sub}>Mobile OTP · India +91 only</Text>

        {!!error && <Text style={styles.err}>{error}</Text>}
        {!!hint && step === 'otp' && <Text style={styles.ok}>{hint}</Text>}

        {step === 'mobile' ? (
          <>
            <Text style={styles.label}>Mobile</Text>
            <View style={styles.row}>
              <Text style={styles.prefix}>+91</Text>
              <TextInput
                style={styles.input}
                keyboardType="phone-pad"
                value={mobile}
                onChangeText={(t) => setMobile(nationalMobileDigits(t))}
                placeholder="9876543210"
                maxLength={16}
                editable={!busy}
              />
            </View>
            <Text style={styles.hint}>
              First sign-in sends an OTP; the same code works until you request a new one.
            </Text>
            <Pressable
              style={[styles.btn, (!mobileValid || busy) && styles.btnOff]}
              disabled={!mobileValid || busy}
              onPress={() => requestOtp()}
            >
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Continue</Text>}
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.hint}>
              OTP for {formatIndianMobile(mobile)}{' '}
              <Text style={styles.linkInline} onPress={() => setStep('mobile')}>
                Change
              </Text>
            </Text>
            <Text style={styles.label}>OTP</Text>
            <TextInput
              style={[styles.input, styles.full]}
              keyboardType="number-pad"
              value={otp}
              onChangeText={(t) => setOtp(t.replace(/\D/g, '').slice(0, 8))}
              placeholder="Enter OTP"
              maxLength={8}
              editable={!busy}
            />
            <Pressable
              style={[styles.btn, (!otp.trim() || busy) && styles.btnOff]}
              disabled={!otp.trim() || busy}
              onPress={verifyOtp}
            >
              {busy ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.btnText}>Verify & continue</Text>
              )}
            </Pressable>
            <Pressable
              onPress={() => requestOtp({ resend: true })}
              disabled={busy}
              style={[styles.forgotBtn, busy && styles.btnOff]}
            >
              {busy ? (
                <ActivityIndicator color="#7dd3fc" size="small" />
              ) : (
                <Text style={styles.linkInline}>Forgot OTP? Send a new code</Text>
              )}
            </Pressable>
          </>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#0f172a' },
  pad: { padding: 24, paddingTop: 72 },
  title: { color: '#f8fafc', fontSize: 24, fontWeight: '700' },
  sub: { color: '#94a3b8', marginTop: 6, marginBottom: 24 },
  label: { color: '#cbd5e1', fontSize: 12, marginBottom: 6 },
  row: { flexDirection: 'row', marginBottom: 8 },
  prefix: {
    backgroundColor: '#1e293b',
    color: '#e2e8f0',
    paddingHorizontal: 12,
    paddingVertical: 14,
    borderTopLeftRadius: 10,
    borderBottomLeftRadius: 10,
    overflow: 'hidden',
  },
  input: {
    flex: 1,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderTopRightRadius: 10,
    borderBottomRightRadius: 10,
    fontSize: 16,
  },
  full: { borderRadius: 10, marginBottom: 16, flex: undefined },
  btn: {
    backgroundColor: '#0369a1',
    padding: 14,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 8,
  },
  btnOff: { opacity: 0.5 },
  btnText: { color: '#fff', fontWeight: '600' },
  hint: { color: '#94a3b8', marginBottom: 12, lineHeight: 20 },
  linkInline: { color: '#7dd3fc', textAlign: 'center' },
  forgotBtn: { marginTop: 18, alignItems: 'center', minHeight: 24 },
  err: {
    backgroundColor: '#7f1d1d',
    color: '#fecaca',
    padding: 10,
    borderRadius: 8,
    marginBottom: 12,
  },
  ok: {
    backgroundColor: '#14532d',
    color: '#bbf7d0',
    padding: 10,
    borderRadius: 8,
    marginBottom: 12,
  },
});
