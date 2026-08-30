/**
 * Smaller + faster release APKs:
 * - arm64-v8a only (phones; drops x86/armeabi emulator slices ~3× native size)
 * - R8 minify + resource shrink
 * - compress JS bundle + native libs in APK
 */
const { withGradleProperties } = require('expo/config-plugins');

function setProp(props, key, value) {
  const i = props.findIndex((p) => p.type === 'property' && p.key === key);
  const item = { type: 'property', key, value: String(value) };
  if (i >= 0) props[i] = item;
  else props.push(item);
}

function withApkOptimize(config) {
  return withGradleProperties(config, (cfg) => {
    const props = cfg.modResults;
    setProp(props, 'reactNativeArchitectures', 'arm64-v8a');
    setProp(props, 'android.enableMinifyInReleaseBuilds', 'true');
    setProp(props, 'android.enableShrinkResourcesInReleaseBuilds', 'true');
    setProp(props, 'android.enableBundleCompression', 'true');
    setProp(props, 'expo.useLegacyPackaging', 'true');
    setProp(props, 'org.gradle.caching', 'true');
    setProp(props, 'org.gradle.parallel', 'true');
    setProp(
      props,
      'org.gradle.jvmargs',
      '-Xmx4096m -XX:MaxMetaspaceSize=1024m -XX:+HeapDumpOnOutOfMemoryError -Dfile.encoding=UTF-8',
    );
    return cfg;
  });
}

module.exports = withApkOptimize;
