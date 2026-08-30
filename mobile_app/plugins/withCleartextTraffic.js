/**
 * Ensure Android allows cleartext HTTP to the SmartRoad portal.
 * Expo's usesCleartextTraffic flag alone did not land in the release manifest.
 */
const {
  withAndroidManifest,
  withDangerousMod,
  AndroidConfig,
} = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');

function withCleartextTraffic(config) {
  config = withAndroidManifest(config, (cfg) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(cfg.modResults);
    app.$['android:usesCleartextTraffic'] = 'true';
    app.$['android:networkSecurityConfig'] = '@xml/network_security_config';
    return cfg;
  });

  config = withDangerousMod(config, [
    'android',
    async (cfg) => {
      const destDir = path.join(
        cfg.modRequest.platformProjectRoot,
        'app',
        'src',
        'main',
        'res',
        'xml',
      );
      fs.mkdirSync(destDir, { recursive: true });
      const src = path.join(cfg.modRequest.projectRoot, 'network_security_config.xml');
      const dest = path.join(destDir, 'network_security_config.xml');
      fs.copyFileSync(src, dest);
      return cfg;
    },
  ]);

  return config;
}

module.exports = withCleartextTraffic;
