/**
 * GPSIndicator — compact row that shows the current GPS state.
 *
 * Props:
 *   coords     object | null  — { latitude, longitude, accuracy }
 *   acquiring  bool           — true while waiting for first fix
 *   error      string         — error message from useLocation
 *   style      object         — additional container style
 */
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { formatCoords, formatAccuracy, gpsQualityLabel } from '../utils/formatters';

export default function GPSIndicator({ coords, acquiring, error, style }) {
  const qualityColors = {
    Excellent: '#15803d',
    Good: '#0369a1',
    Fair: '#b45309',
    Poor: '#b91c1c',
    Unknown: '#64748b',
  };

  const quality = coords ? gpsQualityLabel(coords.accuracy) : 'Unknown';
  const dotColor = qualityColors[quality] || '#64748b';

  return (
    <View style={[styles.container, style]}>
      {acquiring ? (
        <>
          <ActivityIndicator size="small" color="#0369a1" style={styles.spinner} />
          <Text style={styles.text}>Acquiring GPS…</Text>
        </>
      ) : error ? (
        <>
          <View style={[styles.dot, { backgroundColor: '#b91c1c' }]} />
          <Text style={[styles.text, { color: '#b91c1c' }]} numberOfLines={1}>
            {error}
          </Text>
        </>
      ) : coords ? (
        <>
          <View style={[styles.dot, { backgroundColor: dotColor }]} />
          <Text style={styles.text} numberOfLines={1}>
            {formatCoords(coords.latitude, coords.longitude, { decimals: 4 })}
            {coords.accuracy != null
              ? `  ${formatAccuracy(coords.accuracy)}  (${quality})`
              : ''}
          </Text>
        </>
      ) : (
        <>
          <View style={[styles.dot, { backgroundColor: '#64748b' }]} />
          <Text style={styles.text}>No GPS signal</Text>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#f1f5f9',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 7,
    gap: 6,
  },
  spinner: { marginRight: 2 },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    flexShrink: 0,
  },
  text: {
    fontSize: 12,
    color: '#475569',
    fontFamily: 'monospace',
    flex: 1,
  },
});
