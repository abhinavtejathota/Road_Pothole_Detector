/**
 * ErrorBanner — a dismissible inline error banner.
 *
 * Props:
 *   message    string   — the error message to display
 *   onDismiss  fn       — called when user taps ✕ (if omitted, not dismissible)
 *   style      object   — additional container style
 *   type       string   — 'error' (default) | 'warning' | 'info'
 */
import { Pressable, StyleSheet, Text, View } from 'react-native';

const THEMES = {
  error: { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c', icon: '⚠' },
  warning: { bg: '#fffbeb', border: '#fde68a', text: '#92400e', icon: '⚡' },
  info: { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8', icon: 'ℹ' },
  success: { bg: '#f0fdf4', border: '#bbf7d0', text: '#15803d', icon: '✓' },
};

export default function ErrorBanner({
  message = '',
  onDismiss = null,
  style,
  type = 'error',
}) {
  if (!message) return null;

  const theme = THEMES[type] || THEMES.error;

  return (
    <View
      style={[
        styles.banner,
        { backgroundColor: theme.bg, borderColor: theme.border },
        style,
      ]}
    >
      <Text style={[styles.icon, { color: theme.text }]}>{theme.icon}</Text>
      <Text style={[styles.message, { color: theme.text }]} numberOfLines={4}>
        {message}
      </Text>
      {onDismiss && (
        <Pressable
          onPress={onDismiss}
          hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
          style={styles.dismissBtn}
          accessibilityLabel="Dismiss"
        >
          <Text style={[styles.dismiss, { color: theme.text }]}>✕</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 12,
    gap: 8,
  },
  icon: { fontSize: 15, lineHeight: 20, flexShrink: 0 },
  message: { flex: 1, fontSize: 13, lineHeight: 19 },
  dismissBtn: { paddingLeft: 4, flexShrink: 0 },
  dismiss: { fontSize: 14, fontWeight: '700' },
});
