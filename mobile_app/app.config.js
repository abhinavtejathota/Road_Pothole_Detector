/**
 * Expo app config — API base URL by build type (dev vs release).
 * Maps: OpenStreetMap only (see src/components/OsmMap.js) — no Google Maps key needed.
 */
const appJson = require('./app.json');

// Expo CLI loads .env into process.env for app.config.js; also try dotenv if present
try {
  // eslint-disable-next-line import/no-extraneous-dependencies, global-require
  require('dotenv').config({ path: require('path').join(__dirname, '.env') });
} catch {
  /* optional */
}

const apiBaseDev = (
  process.env.EXPO_PUBLIC_API_BASE_DEV ||
  process.env.EXPO_PUBLIC_API_BASE ||
  ''
).trim();

const apiBaseProd = (
  process.env.EXPO_PUBLIC_API_BASE_PROD ||
  process.env.EXPO_PUBLIC_API_BASE ||
  'http://45.194.2.247:5005'
).trim();

const base = appJson.expo;

module.exports = {
  expo: {
    ...base,
    plugins: [
      ...(base.plugins || []),
    ],
    extra: {
      ...(base.extra || {}),
      apiBaseDev: apiBaseDev || 'http://10.0.2.2:5000',
      apiBaseProd,
    },
  },
};
