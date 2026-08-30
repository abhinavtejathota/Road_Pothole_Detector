/**
 * Offline integrity checks for the field app (no device required).
 * Run: node scripts/smoke_integrity.js
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');

function mustExist(rel) {
  const p = path.join(root, rel);
  assert.ok(fs.existsSync(p), `missing ${rel}`);
}

// Critical assets / screens
[
  'App.js',
  'src/api.js',
  'src/screens/LoginScreen.js',
  'src/screens/DashboardScreen.js',
  'src/screens/SurveyRouteScreen.js',
  'src/screens/CaptureScreen.js',
  'src/components/AppMap.js',
  'src/components/OsmMap.js',
  'src/offlineCapture.js',
  'assets/icon.png',
  'app.json',
  'package.json',
].forEach(mustExist);

const apiSrc = fs.readFileSync(path.join(root, 'src/api.js'), 'utf8');
const requiredApi = [
  'assignmentSummary',
  'assignmentGeoJson',
  'previewRoutes',
  'assignCorridor',
  'assignCustomRoute',
  'trackingPing',
  'trackingDiscardSession',
  '/api/upload/session/init',
  '/api/upload/session/chunk',
  '/api/upload/session/finalize',
  '/api/auth/login',
  '/api/auth/me',
];
for (const needle of requiredApi) {
  assert.ok(apiSrc.includes(needle), `api.js missing ${needle}`);
}

const dash = fs.readFileSync(path.join(root, 'src/screens/DashboardScreen.js'), 'utf8');
assert.ok(dash.includes('assignmentGeoJson'), 'Dashboard must load assignment geojson');
assert.ok(dash.includes('covered_km') || dash.includes('coveredKm'), 'Dashboard must show covered km');
assert.ok(dash.includes('assignment_complete') || dash.includes('isComplete'), 'Dashboard must surface complete');

const capture = fs.readFileSync(path.join(root, 'src/screens/CaptureScreen.js'), 'utf8');
assert.ok(capture.includes('nativeRecordingActiveRef'), 'Capture must guard recordAsync');
assert.ok(capture.includes('trackingPing'), 'Capture must ping tracking');
assert.ok(capture.includes('CHUNK_SECONDS') || capture.includes('maxDuration'), 'Capture must chunk record');

const app = fs.readFileSync(path.join(root, 'App.js'), 'utf8');
assert.ok(app.includes('is_videographer'), 'App must gate non-VG users');
assert.ok(app.includes('sealTick'), 'App must pass seal animation tick');

// linesFromGeo logic (duplicated here so we can unit-check without RN runtime)
function linesFromGeo(geo) {
  const features = geo?.features || [];
  const lines = [];
  features.forEach((f, idx) => {
    const geom = f?.geometry;
    const props = f?.properties || {};
    const st = String(props.status || '');
    const kind = String(props.route_kind || '');
    const isCovered = st === 'completed' || st === 'verified' || kind === 'covered';
    const push = (coords) => {
      if (!Array.isArray(coords) || coords.length < 2) return;
      lines.push({ key: `${idx}-${lines.length}`, isCovered, continuous: kind === 'continuous' });
    };
    if (geom?.type === 'LineString') push(geom.coordinates);
  });
  return lines;
}

const sample = {
  features: [
    {
      properties: { route_kind: 'continuous', status: 'assigned' },
      geometry: { type: 'LineString', coordinates: [[78.1, 17.1], [78.2, 17.2]] },
    },
    {
      properties: { route_kind: 'covered', status: 'completed', trail_overlay: true },
      geometry: { type: 'LineString', coordinates: [[78.11, 17.11], [78.15, 17.15], [78.19, 17.19]] },
    },
  ],
};
const parsed = linesFromGeo(sample);
assert.strictEqual(parsed.length, 2);
assert.strictEqual(parsed.filter((l) => l.isCovered).length, 1);
assert.strictEqual(parsed.filter((l) => l.continuous).length, 1);

const captureSrc = fs.readFileSync(path.join(root, 'src/screens/CaptureScreen.js'), 'utf8');
[
  'AppState',
  'interruptedWhilePausedRef',
  'startNew',
  'activateKeepAwakeAsync',
  'nativeRecordingActiveRef',
  'CHUNK_SECONDS',
].forEach((needle) => {
  assert.ok(captureSrc.includes(needle), `CaptureScreen missing ${needle}`);
});

const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
assert.ok(pkg.dependencies.expo, 'expo dependency required');
assert.ok(pkg.dependencies['expo-camera'], 'expo-camera required');
assert.ok(pkg.dependencies['expo-location'], 'expo-location required');

console.log('OK mobile_app integrity smoke passed');
process.exit(0);
