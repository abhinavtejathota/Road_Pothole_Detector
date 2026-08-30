import { useEffect, useMemo, useRef } from 'react';
import { StyleSheet, View } from 'react-native';
import { WebView } from 'react-native-webview';

/** Allow only safe CSS color tokens for Leaflet HTML (no url()/expression). */
function safeCssColor(value) {
  const s = String(value || '').trim();
  if (/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(s)) return s;
  if (/^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(0|1|0?\.\d+))?\s*\)$/.test(s)) {
    return s;
  }
  return '';
}

/**
 * OpenStreetMap map via Leaflet in a WebView.
 * Avoids Google Maps API key crashes that kill react-native-maps in release APKs.
 *
 * props:
 *  - region: { latitude, longitude, latitudeDelta?, longitudeDelta? }
 *  - polylines: [{ id?, coordinates: [{latitude,longitude}], strokeColor, strokeWidth }]
 *  - markers: [{ id?, latitude, longitude, title?, color? }]
 *  - onPolylinePress?: (id) => void
 */
export default function OsmMap({
  style,
  region,
  polylines = [],
  markers = [],
  onPolylinePress,
}) {
  const ref = useRef(null);

  const center = useMemo(() => {
    const lat = Number(region?.latitude);
    const lon = Number(region?.longitude);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon };
    return { lat: 17.45, lon: 78.39 };
  }, [region?.latitude, region?.longitude]);

  const zoom = useMemo(() => {
    const d = Number(region?.latitudeDelta);
    if (!Number.isFinite(d) || d <= 0) return 12;
    if (d > 1) return 8;
    if (d > 0.4) return 9;
    if (d > 0.2) return 10;
    if (d > 0.1) return 11;
    if (d > 0.05) return 12;
    if (d > 0.02) return 13;
    return 14;
  }, [region?.latitudeDelta]);

  const payload = useMemo(
    () => ({
      center,
      zoom,
      polylines: (polylines || [])
        .map((p, i) => ({
          id: String(p.id ?? i),
          color: safeCssColor(p.strokeColor) || '#6366F1',
          weight: Number(p.strokeWidth) || 5,
          latlngs: (p.coordinates || [])
            .filter((c) => Number.isFinite(c?.latitude) && Number.isFinite(c?.longitude))
            .map((c) => [c.latitude, c.longitude]),
        }))
        .filter((p) => p.latlngs.length >= 2),
      markers: (markers || [])
        .filter((m) => Number.isFinite(m?.latitude) && Number.isFinite(m?.longitude))
        .map((m, i) => ({
          id: String(m.id ?? i),
          lat: m.latitude,
          lon: m.longitude,
          title: String(m.title || '').slice(0, 200),
          color: safeCssColor(m.color) || '#2563eb',
        })),
    }),
    [center, zoom, polylines, markers],
  );

  useEffect(() => {
    const js = `window.__updateMap && window.__updateMap(${JSON.stringify(payload)}); true;`;
    ref.current?.injectJavaScript(js);
  }, [payload]);

  const html = useMemo(
    () => `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { margin:0; padding:0; height:100%; width:100%; background:#e8eef3; }
    .leaflet-container { background:#e8eef3; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    var map = L.map('map', { zoomControl: true, attributionControl: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }).addTo(map);
    var layerGroup = L.layerGroup().addTo(map);
    var didFit = false;

    function escHtml(s) {
      return String(s || '').replace(/[&<>"']/g, function(c) {
        return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
      });
    }

    function safeColor(c) {
      c = String(c || '');
      if (/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(c)) return c;
      if (/^rgba?\\(\\s*\\d{1,3}\\s*,\\s*\\d{1,3}\\s*,\\s*\\d{1,3}(?:\\s*,\\s*(0|1|0?\\.\\d+))?\\s*\\)$/.test(c)) return c;
      return '#2563eb';
    }

    function pinIcon(color) {
      color = safeColor(color);
      return L.divIcon({
        className: '',
        html: '<div style="width:14px;height:14px;border-radius:50%;background:' + color +
          ';border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7]
      });
    }

    window.__updateMap = function(data) {
      try {
        layerGroup.clearLayers();
        var bounds = [];
        (data.polylines || []).forEach(function(p) {
          var line = L.polyline(p.latlngs, { color: safeColor(p.color), weight: p.weight, opacity: 0.95 });
          line.on('click', function() {
            if (window.ReactNativeWebView) {
              window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'polyline', id: p.id }));
            }
          });
          line.addTo(layerGroup);
          p.latlngs.forEach(function(ll) { bounds.push(ll); });
        });
        (data.markers || []).forEach(function(m) {
          var mk = L.marker([m.lat, m.lon], { icon: pinIcon(m.color || '#2563eb') });
          if (m.title) mk.bindPopup(escHtml(String(m.title)));
          mk.addTo(layerGroup);
          bounds.push([m.lat, m.lon]);
        });
        if (bounds.length >= 2) {
          map.fitBounds(bounds, { padding: [28, 28], maxZoom: 15 });
          didFit = true;
        } else if (bounds.length === 1) {
          map.setView(bounds[0], Math.max(data.zoom || 13, 13));
          didFit = true;
        } else if (!didFit) {
          map.setView([data.center.lat, data.center.lon], data.zoom || 12);
          didFit = true;
        } else {
          map.setView([data.center.lat, data.center.lon], data.zoom || map.getZoom());
        }
      } catch (e) {}
    };

    window.__updateMap(${JSON.stringify(payload)});
  </script>
</body>
</html>`,
    // initial HTML only — updates go through injectJavaScript
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return (
    <View style={[styles.wrap, style]}>
      <WebView
        ref={ref}
        originWhitelist={['about:blank']}
        source={{ html }}
        style={styles.web}
        onMessage={(e) => {
          try {
            const data = JSON.parse(e.nativeEvent.data);
            if (data?.type === 'polyline' && onPolylinePress) onPolylinePress(data.id);
          } catch {
            /* ignore */
          }
        }}
        javaScriptEnabled
        domStorageEnabled
        mixedContentMode="never"
        setSupportMultipleWindows={false}
        androidLayerType="hardware"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, overflow: 'hidden', backgroundColor: '#e8eef3' },
  web: { flex: 1, backgroundColor: 'transparent' },
});
