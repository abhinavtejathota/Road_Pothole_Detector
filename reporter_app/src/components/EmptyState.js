/**
 * EmptyState — a centred illustration + message for empty lists or zero results.
 *
 * Props:
 *   icon       string   — single emoji or text used as the visual
 *   title      string   — bold primary message
 *   subtitle   string   — optional secondary text
 *   action     string   — optional action button label
 *   onAction   fn       — handler for the action button
 *   style      object   — additional container style
 */
import { Pressable, StyleSheet, Text, View } from 'react-native';

export default function EmptyState({
  icon = '📋',
  title = 'Nothing here yet',
  subtitle = '',
  action = '',
  onAction = null,
  style,
}) {
  return (
    <View style={[styles.container, style]}>
      <Text style={styles.icon}>{icon}</Text>
      <Text style={styles.title}>{title}</Text>
      {!!subtitle && <Text style={styles.subtitle}>{subtitle}</Text>}
      {!!action && onAction && (
        <Pressable style={styles.btn} onPress={onAction}>
          <Text style={styles.btnText}>{action}</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  icon: { fontSize: 48, marginBottom: 16 },
  title: { fontSize: 17, fontWeight: '700', color: '#1e293b', textAlign: 'center' },
  subtitle: { fontSize: 14, color: '#64748b', textAlign: 'center', marginTop: 6, lineHeight: 20 },
  btn: {
    marginTop: 20,
    backgroundColor: '#0369a1',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  btnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
});
