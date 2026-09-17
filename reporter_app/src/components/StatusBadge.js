/**
 * StatusBadge — pill-shaped badge that colours itself based on complaint status.
 *
 * Props:
 *   status  string   — raw status string from the API (e.g. "open", "in_progress")
 *   small   bool     — reduce padding/font for compact contexts
 */
import { Text, StyleSheet } from 'react-native';
import { formatStatus, statusColors } from '../utils/formatters';

export default function StatusBadge({ status, small = false }) {
  const { bg, fg } = statusColors(status);
  return (
    <Text
      style={[
        styles.badge,
        small && styles.small,
        { backgroundColor: bg, color: fg },
      ]}
      numberOfLines={1}
    >
      {formatStatus(status)}
    </Text>
  );
}

const styles = StyleSheet.create({
  badge: {
    fontSize: 12,
    fontWeight: '600',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    overflow: 'hidden',
    alignSelf: 'flex-start',
  },
  small: {
    fontSize: 10,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
});
