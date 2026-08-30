import { useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Image,
  Alert,
} from 'react-native';
import * as Location from 'expo-location';
import * as ImagePicker from 'expo-image-picker';
import * as FileSystem from 'expo-file-system/legacy';
import { api } from '../api';
import {
  MEDIA_META_NAME,
  buildCitizenFrameMeta,
  isVideoAsset,
  mediaFormPart,
} from '../uploadConfig';

export default function FileComplaintScreen() {
  const [categories, setCategories] = useState([]);
  const [defectType, setDefectType] = useState('');
  const [description, setDescription] = useState('');
  const [gps, setGps] = useState(null);
  const [asset, setAsset] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    api.categories().then((r) => setCategories(r.categories || [])).catch(() => {});
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setErr('Location permission required.');
        return;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      setGps({
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
        capturedAt: new Date().toISOString(),
      });
    })();
  }, []);

  const pickCamera = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Camera permission needed');
      return;
    }
    const res = await ImagePicker.launchCameraAsync({
      quality: 0.85,
      exif: false,
    });
    if (!res.canceled && res.assets?.[0]) setAsset(res.assets[0]);
  };

  const pickGallery = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.85,
    });
    if (!res.canceled && res.assets?.[0]) setAsset(res.assets[0]);
  };

  const submit = async () => {
    setErr('');
    setMsg('');
    if (!defectType) {
      setErr('Select a category.');
      return;
    }
    if (!gps) {
      setErr('GPS required.');
      return;
    }
    if (!asset?.uri) {
      setErr('Capture or upload a photo.');
      return;
    }
    const form = new FormData();
    form.append('defect_type', defectType);
    form.append('description', description);
    form.append('latitude', String(gps.latitude));
    form.append('longitude', String(gps.longitude));
    if (gps.accuracy != null) form.append('gps_accuracy_m', String(gps.accuracy));
    if (gps.capturedAt) form.append('captured_at', String(gps.capturedAt));
    form.append('media', mediaFormPart(asset));

    const meta = buildCitizenFrameMeta({
      latitude: gps.latitude,
      longitude: gps.longitude,
      accuracy: gps.accuracy,
      defectType,
      description,
      capturedAt: gps.capturedAt,
      isVideo: isVideoAsset(asset),
    });
    const metaPath = `${FileSystem.cacheDirectory}${MEDIA_META_NAME}`;
    await FileSystem.writeAsStringAsync(metaPath, JSON.stringify(meta, null, 2));
    form.append('frame_meta', {
      uri: metaPath,
      name: MEDIA_META_NAME,
      type: 'application/json',
    });

    setBusy(true);
    try {
      const out = await api.submitComplaint(form);
      setMsg(`Submitted: ${out.tracking_number}`);
      setDefectType('');
      setDescription('');
      setAsset(null);
    } catch (e) {
      setErr(e.message || 'Submit failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.pad}>
      <Text style={styles.h}>File a complaint</Text>
      <Text style={styles.gps}>
        {gps
          ? `GPS ${gps.latitude.toFixed(5)}, ${gps.longitude.toFixed(5)}`
          : 'Acquiring GPS…'}
      </Text>
      {!!err && <Text style={styles.err}>{err}</Text>}
      {!!msg && <Text style={styles.ok}>{msg}</Text>}

      <Text style={styles.label}>Category</Text>
      <View style={styles.chips}>
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

      <Text style={styles.label}>Description (optional)</Text>
      <TextInput
        style={styles.area}
        multiline
        value={description}
        onChangeText={setDescription}
        placeholder="Landmark, lane blocked…"
      />

      <View style={styles.row}>
        <Pressable style={styles.btnSec} onPress={pickCamera} disabled={busy || !gps}>
          <Text style={styles.btnSecText}>Capture</Text>
        </Pressable>
        <Pressable style={styles.btnSec} onPress={pickGallery} disabled={busy || !gps}>
          <Text style={styles.btnSecText}>Upload</Text>
        </Pressable>
      </View>
      {asset?.uri ? (
        <Image source={{ uri: asset.uri }} style={styles.preview} />
      ) : null}

      <Pressable
        style={[styles.btn, (busy || !gps || !asset || !defectType) && styles.off]}
        disabled={busy || !gps || !asset || !defectType}
        onPress={submit}
      >
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Submit</Text>}
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f4f7fb' },
  pad: { padding: 16, paddingBottom: 40 },
  h: { fontSize: 20, fontWeight: '700', marginBottom: 8 },
  gps: { fontFamily: PlatformSelectMono(), fontSize: 12, color: '#475569', marginBottom: 12 },
  label: { fontSize: 12, color: '#64748b', marginBottom: 6, marginTop: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: '#e2e8f0',
  },
  chipOn: { backgroundColor: '#0369a1' },
  chipText: { color: '#334155', fontSize: 13 },
  chipTextOn: { color: '#fff' },
  area: {
    backgroundColor: '#fff',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#e2e8f0',
    minHeight: 80,
    padding: 12,
    textAlignVertical: 'top',
  },
  row: { flexDirection: 'row', gap: 10, marginTop: 12 },
  btnSec: {
    flex: 1,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#cbd5e1',
    padding: 12,
    borderRadius: 10,
    alignItems: 'center',
  },
  btnSecText: { fontWeight: '600', color: '#0f172a' },
  preview: { width: '100%', height: 200, borderRadius: 10, marginTop: 12, backgroundColor: '#0f172a' },
  btn: {
    marginTop: 16,
    backgroundColor: '#0369a1',
    padding: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  off: { opacity: 0.5 },
  btnText: { color: '#fff', fontWeight: '600' },
  err: { color: '#b91c1c', marginBottom: 8 },
  ok: { color: '#15803d', marginBottom: 8, fontWeight: '600' },
});

function PlatformSelectMono() {
  return 'monospace';
}
