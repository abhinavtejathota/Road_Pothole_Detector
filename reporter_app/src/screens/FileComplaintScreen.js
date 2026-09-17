/**
 * FileComplaintScreen — file a new road defect complaint.
 *
 * Improvements over the original:
 *  - Uses useLocation hook for GPS (with live accuracy indicator).
 *  - Supports both photo and video capture/selection.
 *  - Draft auto-save/restore via AsyncStorage (survives app restart).
 *  - MediaPreview component with remove button.
 *  - GPSIndicator component shows accuracy colour-coded.
 *  - ErrorBanner component for dismissible errors.
 *  - Full form validation via validators.js.
 *  - GPS accuracy warning when location fix is poor.
 *  - Retry hint on network failure.
 *  - Queues complaint for offline submission via enqueueDraft when offline.
 *  - Success screen with tracking number + file-another button.
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
import * as ImagePicker from 'expo-image-picker';
import * as FileSystem from 'expo-file-system/legacy';
import { api, enqueueDraft } from '../api';
import {
  MEDIA_META_NAME,
  buildCitizenFrameMeta,
  isVideoAsset,
  mediaFormPart,
} from '../uploadConfig';
import { useLocation } from '../hooks/useLocation';
import GPSIndicator from '../components/GPSIndicator';
import MediaPreview from '../components/MediaPreview';
import ErrorBanner from '../components/ErrorBanner';
import {
  validateComplaintForm,
  gpsAccuracyWarning,
} from '../utils/validators';
import AsyncStorage from '@react-native-async-storage/async-storage';

const DRAFT_KEY = 'sr_reporter_file_draft';

// ---------------------------------------------------------------------------
// Draft persistence helpers
// ---------------------------------------------------------------------------
async function saveDraftLocally(draft) {
  try {
    await AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* non-fatal */
  }
}

async function loadDraftLocally() {
  try {
    const raw = await AsyncStorage.getItem(DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function clearDraftLocally() {
  try {
    await AsyncStorage.removeItem(DRAFT_KEY);
  } catch {
    /* non-fatal */
  }
}

// ---------------------------------------------------------------------------
// Success sub-screen shown after a complaint is filed
// ---------------------------------------------------------------------------
function SuccessScreen({ trackingNumber, onFileAnother }) {
  return (
    <View style={styles.successRoot}>
      <Text style={styles.successIcon}>✅</Text>
      <Text style={styles.successTitle}>Complaint filed!</Text>
      <View style={styles.successCard}>
        <Text style={styles.successLabel}>Tracking number</Text>
        <Text style={styles.successTn} selectable>{trackingNumber}</Text>
      </View>
      <Text style={styles.successHint}>
        You can track the status of your complaint from the Track tab.
      </Text>
      <Pressable style={styles.fileAnotherBtn} onPress={onFileAnother}>
        <Text style={styles.fileAnotherText}>File another complaint</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------
export default function FileComplaintScreen() {
  const [categories, setCategories] = useState([]);
  const [defectType, setDefectType] = useState('');
  const [description, setDescription] = useState('');
  const [asset, setAsset] = useState(null);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState([]);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [draftRestored, setDraftRestored] = useState(false);

  const { coords, acquiring, error: gpsError, refresh: refreshGps } = useLocation();
  const accuracyWarn = coords ? gpsAccuracyWarning(coords) : '';

  // Load categories
  useEffect(() => {
    api.categories().then((r) => setCategories(r.categories || [])).catch(() => {});
  }, []);

  // Restore draft on mount
  useEffect(() => {
    loadDraftLocally().then((draft) => {
      if (!draft) return;
      if (draft.defectType) setDefectType(draft.defectType);
      if (draft.description) setDescription(draft.description);
      setDraftRestored(true);
    });
  }, []);

  // Auto-save draft on change
  useEffect(() => {
    if (!defectType && !description && !asset) return;
    saveDraftLocally({
      defectType,
      description,
      assetUri: asset?.uri || null,
    });
  }, [defectType, description, asset]);

  const resetForm = useCallback(() => {
    setDefectType('');
    setDescription('');
    setAsset(null);
    setErrors([]);
    clearDraftLocally();
    setDraftRestored(false);
  }, []);

  const handleFileAnother = useCallback(() => {
    setTrackingNumber('');
    resetForm();
  }, [resetForm]);

  // ----------- Media pickers -----------
  const pickCamera = useCallback(async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Camera permission needed', 'Please allow camera access in settings.');
      return;
    }
    const res = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.85,
      videoMaxDuration: 60,
      exif: false,
    });
    if (!res.canceled && res.assets?.[0]) {
      setAsset(res.assets[0]);
      setErrors([]);
    }
  }, []);

  const pickGallery = useCallback(async () => {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.85,
    });
    if (!res.canceled && res.assets?.[0]) {
      setAsset(res.assets[0]);
      setErrors([]);
    }
  }, []);

  // ----------- Submit -----------
  const submit = useCallback(async () => {
    setErrors([]);
    const formErrors = validateComplaintForm({ defectType, description, gps: coords, asset });
    if (formErrors.length) {
      setErrors(formErrors);
      return;
    }

    // Lock the UI before any async I/O so double-taps are prevented
    setBusy(true);
    let form;
    try {
      form = new FormData();
      form.append('defect_type', defectType);
      form.append('description', description);
      form.append('latitude', String(coords.latitude));
      form.append('longitude', String(coords.longitude));
      if (coords.accuracy != null) form.append('gps_accuracy_m', String(coords.accuracy));
      if (coords.capturedAt) form.append('captured_at', String(coords.capturedAt));
      form.append('media', mediaFormPart(asset));

      const meta = buildCitizenFrameMeta({
        latitude: coords.latitude,
        longitude: coords.longitude,
        accuracy: coords.accuracy,
        defectType,
        description,
        capturedAt: coords.capturedAt,
        isVideo: isVideoAsset(asset),
      });
      const metaPath = `${FileSystem.cacheDirectory}${MEDIA_META_NAME}`;
      await FileSystem.writeAsStringAsync(metaPath, JSON.stringify(meta, null, 2));
      form.append('frame_meta', {
        uri: metaPath,
        name: MEDIA_META_NAME,
        type: 'application/json',
      });
    } catch (prepErr) {
      setErrors([prepErr.message || 'Failed to prepare submission.']);
      setBusy(false);
      return;
    }

    try {
      const out = await api.submitComplaint(form);
      clearDraftLocally();
      resetForm();
      setTrackingNumber(out.tracking_number || 'submitted');
    } catch (e) {
      // Queue offline if network error (no status code)
      if (!e.status) {
        try {
          await enqueueDraft({
            defect_type: defectType,
            description,
            latitude: coords.latitude,
            longitude: coords.longitude,
            gps_accuracy_m: coords.accuracy,
            captured_at: coords.capturedAt,
            mediaUri: asset.uri,
            mediaName: mediaFormPart(asset).name,
            mediaType: mediaFormPart(asset).type,
          });
          clearDraftLocally();
          resetForm();
          setTrackingNumber('QUEUED_OFFLINE');
        } catch {
          setErrors([e.message || 'Submit failed. Please retry.']);
        }
      } else {
        setErrors([e.message || 'Submit failed. Please retry.']);
      }
    } finally {
      setBusy(false);
    }
  }, [defectType, description, coords, asset, resetForm]);

  // Show success screen
  if (trackingNumber) {
    return (
      <SuccessScreen
        trackingNumber={
          trackingNumber === 'QUEUED_OFFLINE'
            ? 'Queued — will submit when online'
            : trackingNumber
        }
        onFileAnother={handleFileAnother}
      />
    );
  }

  const canSubmit = !busy && coords && asset && defectType;

  return (
    <KeyboardAvoidingView
      style={styles.kavRoot}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.root} contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
        <Text style={styles.h}>File a complaint</Text>

        {/* Draft restored hint */}
        {draftRestored && (
          <ErrorBanner
            message="Draft restored from your last session."
            type="info"
            onDismiss={() => setDraftRestored(false)}
          />
        )}

        {/* GPS */}
        <Text style={styles.label}>Location</Text>
        <GPSIndicator
          coords={coords}
          acquiring={acquiring}
          error={gpsError}
          style={styles.gpsIndicator}
        />
        {!!accuracyWarn && (
          <ErrorBanner message={accuracyWarn} type="warning" style={styles.accuracyWarn} />
        )}
        {gpsError && (
          <Pressable onPress={refreshGps} style={styles.retryGpsBtn}>
            <Text style={styles.retryGpsText}>Retry GPS</Text>
          </Pressable>
        )}

        {/* Validation errors */}
        {errors.map((e, i) => (
          <ErrorBanner
            key={i}
            message={e}
            type="error"
            onDismiss={() => setErrors((prev) => prev.filter((_, j) => j !== i))}
            style={i === 0 ? styles.firstError : undefined}
          />
        ))}

        {/* Category */}
        <Text style={styles.label}>Category *</Text>
        <View style={styles.chips}>
          {categories.length === 0 && (
            <Text style={styles.loadingText}>Loading categories…</Text>
          )}
          {categories.map((c) => (
            <Pressable
              key={c}
              style={[styles.chip, defectType === c && styles.chipOn]}
              onPress={() => setDefectType(c)}
            >
              <Text style={[styles.chipText, defectType === c && styles.chipTextOn]}>{c}</Text>
            </Pressable>
          ))}
        </View>

        {/* Description */}
        <Text style={styles.label}>Description (optional)</Text>
        <TextInput
          style={styles.area}
          multiline
          value={description}
          onChangeText={setDescription}
          placeholder="Landmark, lane blocked, severity…"
          maxLength={1000}
          textAlignVertical="top"
        />
        {description.length > 800 && (
          <Text style={styles.charCount}>{description.length}/1000</Text>
        )}

        {/* Media */}
        <Text style={styles.label}>Photo or Video *</Text>
        <View style={styles.mediaRow}>
          <Pressable
            style={[styles.mediaBtnHalf, (busy || acquiring) && styles.btnDisabled]}
            onPress={pickCamera}
            disabled={busy || acquiring}
          >
            <Text style={styles.mediaBtnIcon}>📷</Text>
            <Text style={styles.mediaBtnText}>Camera</Text>
          </Pressable>
          <Pressable
            style={[styles.mediaBtnHalf, (busy || acquiring) && styles.btnDisabled]}
            onPress={pickGallery}
            disabled={busy || acquiring}
          >
            <Text style={styles.mediaBtnIcon}>🖼</Text>
            <Text style={styles.mediaBtnText}>Gallery</Text>
          </Pressable>
        </View>

        {/* Preview */}
        <MediaPreview
          asset={asset}
          onRemove={asset ? () => setAsset(null) : undefined}
          maxHeight={220}
        />

        {/* Submit */}
        <Pressable
          style={[styles.submitBtn, !canSubmit && styles.submitOff]}
          disabled={!canSubmit}
          onPress={submit}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.submitText}>Submit complaint</Text>
          )}
        </Pressable>

        {/* Form hint */}
        <Text style={styles.formHint}>
          * Required. GPS and a photo/video are mandatory for the complaint to be processed.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  kavRoot: { flex: 1 },
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  pad: { padding: 16, paddingBottom: 48 },
  h: { fontSize: 20, fontWeight: '700', marginBottom: 14, color: '#0f172a' },

  label: { fontSize: 12, fontWeight: '700', color: '#64748b', marginBottom: 6, marginTop: 14, textTransform: 'uppercase', letterSpacing: 0.4 },

  /* GPS */
  gpsIndicator: { marginBottom: 4 },
  accuracyWarn: { marginTop: 6 },
  retryGpsBtn: { marginTop: 6, alignSelf: 'flex-start' },
  retryGpsText: { color: '#0369a1', fontSize: 13, fontWeight: '600' },

  /* Errors */
  firstError: { marginTop: 8 },

  /* Category chips */
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: '#e2e8f0',
    borderWidth: 1,
    borderColor: 'transparent',
  },
  chipOn: { backgroundColor: '#0369a1', borderColor: '#0369a1' },
  chipText: { color: '#334155', fontSize: 13 },
  chipTextOn: { color: '#fff', fontWeight: '600' },
  loadingText: { color: '#94a3b8', fontSize: 13 },

  /* Description */
  area: {
    backgroundColor: '#fff',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    minHeight: 88,
    padding: 12,
    textAlignVertical: 'top',
    fontSize: 14,
    color: '#0f172a',
  },
  charCount: { textAlign: 'right', fontSize: 11, color: '#94a3b8', marginTop: 3 },

  /* Media buttons */
  mediaRow: { flexDirection: 'row', gap: 10 },
  mediaBtnHalf: {
    flex: 1,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    gap: 4,
  },
  btnDisabled: { opacity: 0.5 },
  mediaBtnIcon: { fontSize: 22 },
  mediaBtnText: { fontWeight: '600', color: '#0f172a', fontSize: 13 },

  /* Submit */
  submitBtn: {
    marginTop: 20,
    backgroundColor: '#0369a1',
    padding: 15,
    borderRadius: 12,
    alignItems: 'center',
  },
  submitOff: { opacity: 0.45 },
  submitText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  formHint: { color: '#94a3b8', fontSize: 11, marginTop: 12, textAlign: 'center', lineHeight: 16 },

  /* Success screen */
  successRoot: {
    flex: 1,
    backgroundColor: '#f4f7fb',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  successIcon: { fontSize: 56, marginBottom: 16 },
  successTitle: { fontSize: 22, fontWeight: '800', color: '#0f172a', marginBottom: 16 },
  successCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    padding: 16,
    width: '100%',
    alignItems: 'center',
    marginBottom: 16,
  },
  successLabel: { fontSize: 12, color: '#64748b', marginBottom: 6 },
  successTn: { fontSize: 18, fontWeight: '700', color: '#0369a1', textAlign: 'center' },
  successHint: { fontSize: 13, color: '#64748b', textAlign: 'center', lineHeight: 19, marginBottom: 24 },
  fileAnotherBtn: {
    backgroundColor: '#0369a1',
    paddingHorizontal: 28,
    paddingVertical: 13,
    borderRadius: 12,
  },
  fileAnotherText: { color: '#fff', fontWeight: '700', fontSize: 15 },
});
