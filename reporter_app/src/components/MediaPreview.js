/**
 * MediaPreview — displays a photo or video thumbnail with overlay metadata.
 *
 * Props:
 *   asset        object   — expo ImagePicker asset (has .uri, .mimeType / .type, .width, .height)
 *   remoteUrl    string   — alternatively, display a remote URL
 *   onRemove     fn       — if provided, shows a remove (×) button
 *   style        object   — additional container style
 *   maxHeight    number   — max height of the preview (default 220)
 */
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { isVideoAsset } from '../uploadConfig';
import { formatFileSize } from '../utils/formatters';

export default function MediaPreview({
  asset = null,
  remoteUrl = null,
  onRemove = null,
  style,
  maxHeight = 220,
}) {
  const uri = asset?.uri || remoteUrl;
  if (!uri) return null;

  const isVideo = asset ? isVideoAsset(asset) : false;
  const fileSize = asset?.fileSize != null ? formatFileSize(asset.fileSize) : '';
  const dims =
    asset?.width && asset?.height ? `${asset.width}×${asset.height}` : '';

  return (
    <View style={[styles.wrapper, style]}>
      <Image
        source={{ uri }}
        style={[styles.image, { maxHeight }]}
        resizeMode="cover"
      />

      {/* Video overlay */}
      {isVideo && (
        <View style={styles.videoOverlay} pointerEvents="none">
          <View style={styles.playCircle}>
            <Text style={styles.playIcon}>▶</Text>
          </View>
          <Text style={styles.videoLabel}>Video</Text>
        </View>
      )}

      {/* Metadata ribbon */}
      {(fileSize || dims) && (
        <View style={styles.ribbon} pointerEvents="none">
          {!!dims && <Text style={styles.ribbonText}>{dims}</Text>}
          {!!fileSize && <Text style={styles.ribbonText}>{fileSize}</Text>}
        </View>
      )}

      {/* Remove button */}
      {onRemove && (
        <Pressable
          style={styles.removeBtn}
          onPress={onRemove}
          hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
          accessibilityLabel="Remove media"
        >
          <Text style={styles.removeIcon}>✕</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    borderRadius: 10,
    overflow: 'hidden',
    backgroundColor: '#0f172a',
    marginTop: 12,
  },
  image: {
    width: '100%',
    minHeight: 120,
  },
  videoOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  playCircle: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  playIcon: { color: '#fff', fontSize: 20, marginLeft: 3 },
  videoLabel: { color: '#fff', fontSize: 12, fontWeight: '600' },
  ribbon: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(0,0,0,0.45)',
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  ribbonText: { color: '#e2e8f0', fontSize: 11 },
  removeBtn: {
    position: 'absolute',
    top: 8,
    right: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(0,0,0,0.6)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  removeIcon: { color: '#fff', fontSize: 13, fontWeight: '700' },
});
