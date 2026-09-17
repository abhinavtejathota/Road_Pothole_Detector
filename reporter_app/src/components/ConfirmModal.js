/**
 * ConfirmModal — a reusable confirmation bottom-sheet / modal dialog.
 *
 * Props:
 *   visible      bool     — controls visibility
 *   title        string   — modal heading
 *   message      string   — descriptive message / body
 *   confirmLabel string   — label for the confirm action (default "Confirm")
 *   cancelLabel  string   — label for cancel (default "Cancel")
 *   destructive  bool     — if true, confirm button is rendered in danger red
 *   onConfirm    fn       — called when user confirms
 *   onCancel     fn       — called when user cancels or taps backdrop
 *   loading      bool     — shows spinner on confirm button while processing
 */
import {
  ActivityIndicator,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

export default function ConfirmModal({
  visible = false,
  title = 'Confirm',
  message = '',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
  loading = false,
}) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      statusBarTranslucent
      onRequestClose={onCancel}
    >
      {/* Backdrop */}
      <Pressable style={styles.backdrop} onPress={loading ? undefined : onCancel} />

      {/* Sheet */}
      <View style={styles.sheetWrapper} pointerEvents="box-none">
        <View style={styles.sheet}>
          <Text style={styles.title}>{title}</Text>
          {!!message && <Text style={styles.message}>{message}</Text>}

          <View style={styles.actions}>
            <Pressable
              style={[styles.btn, styles.cancelBtn]}
              onPress={onCancel}
              disabled={loading}
            >
              <Text style={styles.cancelText}>{cancelLabel}</Text>
            </Pressable>

            <Pressable
              style={[
                styles.btn,
                styles.confirmBtn,
                destructive && styles.dangerBtn,
                loading && styles.btnDisabled,
              ]}
              onPress={onConfirm}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.confirmText}>{confirmLabel}</Text>
              )}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.45)',
  },
  sheetWrapper: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  sheet: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 24,
    width: '100%',
    maxWidth: 360,
  },
  title: {
    fontSize: 17,
    fontWeight: '700',
    color: '#0f172a',
    marginBottom: 8,
  },
  message: {
    fontSize: 14,
    color: '#475569',
    lineHeight: 20,
    marginBottom: 20,
  },
  actions: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
  },
  btn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  cancelBtn: {
    backgroundColor: '#f1f5f9',
  },
  confirmBtn: {
    backgroundColor: '#0369a1',
  },
  dangerBtn: {
    backgroundColor: '#dc2626',
  },
  btnDisabled: {
    opacity: 0.6,
  },
  cancelText: {
    color: '#334155',
    fontWeight: '600',
  },
  confirmText: {
    color: '#fff',
    fontWeight: '600',
  },
});
