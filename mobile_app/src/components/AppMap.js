/**
 * Shared map — OpenStreetMap only (Leaflet in a WebView via OsmMap).
 * No Google Maps SDK / API key needed, so this never crashes standalone APKs.
 */
import OsmMap from './OsmMap';

export const USE_GOOGLE_MAPS = false;

export default function AppMap({ style, region, initialRegion, polylines = [], markers = [], onPolylinePress }) {
  return (
    <OsmMap
      style={style}
      region={region || initialRegion}
      polylines={polylines}
      markers={markers}
      onPolylinePress={onPolylinePress}
    />
  );
}
