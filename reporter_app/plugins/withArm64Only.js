/**
 * Smaller release APKs: arm64-v8a only (drops x86 / armeabi slices).
 */
const { withGradleProperties } = require('expo/config-plugins');

function setProp(props, key, value) {
  const i = props.findIndex((p) => p.type === 'property' && p.key === key);
  const item = { type: 'property', key, value: String(value) };
  if (i >= 0) props[i] = item;
  else props.push(item);
}

function withArm64Only(config) {
  return withGradleProperties(config, (cfg) => {
    setProp(cfg.modResults, 'reactNativeArchitectures', 'arm64-v8a');
    return cfg;
  });
}

module.exports = withArm64Only;
