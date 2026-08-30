/** Shared brand + button styles for the Expo field app. */
import { StyleSheet } from 'react-native';

export const colors = {
  navy: '#0B3D5C',
  gold: '#E8B40A',
  pageBg: '#F3F7FB',
  border: '#D9E3EC',
  muted: '#5b6b7a',
  danger: '#b3261e',
  dangerText: '#C62828',
  tonalBg: '#E8EEF2',
  white: '#fff',
  inputBg: '#F7FAFC',
  inputBorder: '#D8E2EA',
};

export const buttonStyles = StyleSheet.create({
  primary: {
    backgroundColor: colors.navy,
    borderRadius: 14,
    paddingVertical: 15,
    paddingHorizontal: 20,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
  },
  primaryText: {
    color: colors.white,
    fontWeight: '700',
    fontSize: 16,
  },
  outline: {
    borderWidth: 1.5,
    borderColor: colors.navy,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 20,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
    backgroundColor: colors.white,
  },
  outlineText: {
    color: colors.navy,
    fontWeight: '700',
    fontSize: 15,
  },
  tonal: {
    backgroundColor: colors.tonalBg,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 20,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
  },
  tonalText: {
    color: colors.navy,
    fontWeight: '700',
    fontSize: 15,
  },
  tonalDanger: {
    backgroundColor: colors.tonalBg,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 20,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
  },
  tonalDangerText: {
    color: colors.dangerText,
    fontWeight: '700',
    fontSize: 15,
  },
  gold: {
    backgroundColor: colors.gold,
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: 'center',
  },
  goldText: {
    color: colors.navy,
    fontWeight: '800',
    fontSize: 16,
  },
  disabled: { opacity: 0.55 },
});
