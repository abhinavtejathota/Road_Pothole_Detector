# SmartRoad Citizen — reporter-only React Native (Expo) app

Mobile OTP login + file/track complaint. No admin/staff login.

## DB

### Keep
- `reporter_users`
- `reporter_complaints`
- `reporter_otp_challenges` (hash + `plain_otp` for testing)

### Apply
```bash
psql ... -f migrations/20260720_reporter_portal.sql
psql ... -f migrations/20260720_reporter_otp_restore.sql
psql ... -f migrations/20260721_reporter_otp_plain.sql
```

### Do **not** drop
- `citizen_complaints` — DRGP/staff-side (separate from `reporter_complaints`)
- Staff tables (`users`, `work_orders`, …)

## Run app
```bash
cd reporter_app
npm install
npx expo run:android
# or: npx expo start
```

Set API base (optional):
```
EXPO_PUBLIC_API_BASE=http://YOUR_LAN_IP:5005
```

Server testing OTP (`.env`):
```
REPORTER_OTP_PROVIDER=log
REPORTER_OTP_RETURN=1
REPORTER_OTP_LENGTH=6
REPORTER_OTP_TTL_S=86400
```

OTP is stored in `reporter_otp_challenges` (hash + `plain_otp`) and mirrored to `data/reporter_otps.json`.
The same OTP is reused until **Forgot OTP? Send a new code** (or expiry). Clear before production SMS.

## Screens
- **Login** — mobile → OTP → JWT; **Forgot OTP** resends a new code
- **File** — GPS + capture/upload + category → `/api/reporter/filecomplaint`
- **Track** — `/api/reporter/trackcomplaint`

## Rebuild APK
Release builds are **arm64-v8a only** (`plugins/withArm64Only.js`) to keep APK size down.
```bash
cd reporter_app
npx expo prebuild --platform android --clean
cd android
.\gradlew.bat assembleRelease --no-daemon
```
