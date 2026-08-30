import { Component } from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';

/** Catches React render errors so a bad screen cannot kill the whole APK. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    // eslint-disable-next-line no-console
    console.warn('ErrorBoundary', error?.message || error);
  }

  render() {
    if (this.state.error) {
      return (
        <View style={styles.wrap}>
          <Text style={styles.title}>Something went wrong</Text>
          <Text style={styles.msg}>{String(this.state.error?.message || this.state.error)}</Text>
          <Pressable
            style={styles.btn}
            onPress={() => {
              this.setState({ error: null });
              this.props.onReset?.();
            }}
          >
            <Text style={styles.btnText}>Go back</Text>
          </Pressable>
        </View>
      );
    }
    return this.props.children;
  }
}

const styles = StyleSheet.create({
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: '#fff' },
  title: { fontSize: 18, fontWeight: '800', color: '#0B3D5C', marginBottom: 8 },
  msg: { color: '#5b6b7a', textAlign: 'center', marginBottom: 16 },
  btn: { backgroundColor: '#0B3D5C', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10 },
  btnText: { color: '#fff', fontWeight: '700' },
});
