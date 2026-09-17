# SmartRoad Citizen — Reporter App

> **React Native (Expo) · India +91 OTP · GPS-tagged pothole reporting**

A citizen-facing mobile app that lets any resident file a road-defect complaint with a photo or video, GPS coordinates, and a category — and track it through to resolution.

The underlying architecture, folder structure, hooks, components, utility modules, and bug-fix strategy were **designed and implemented with [IBM Bob](https://www.ibm.com/products/bob)** — IBM's AI software engineering assistant.

---

## Quick start

```bash
cd reporter_app
npm install
npx expo start          # Expo Go / dev build
# or
npx expo run:android    # native Android build
```

Set API base URL (optional — overrides the built-in fallback):

```bash
EXPO_PUBLIC_API_BASE=http://YOUR_LAN_IP:5005
```

---

## Architecture

See **[ARCHITECTURE.md](./ARCHITECTURE.md)** for the full structural breakdown designed by IBM Bob.

---

## Screens

| Screen | Route / Tab | Description |
|---|---|---|
| `LoginScreen` | (pre-auth) | Mobile number → OTP → JWT |
| `FileComplaintScreen` | **File** tab | GPS + photo/video + category → submit |
| `TrackComplaintScreen` | **Track** tab | List, search, filter, delete, drill-down |
| `ComplaintDetailScreen` | (pushed from Track) | Full detail, Maps deep-link, media |
| `ProfileScreen` | **Profile** tab | Stats, recent complaints, sign-out |
| `SettingsScreen` | ⚙ header icon | API URL, About, Dev tools |

---

## Database tables used

| Table | Purpose |
|---|---|
| `reporter_users` | Registered citizens |
| `reporter_complaints` | Filed complaints |
| `reporter_otp_challenges` | OTP hash + plain (dev) |

Apply migrations:

```bash
psql ... -f migrations/20260720_reporter_portal.sql
psql ... -f migrations/20260720_reporter_otp_restore.sql
psql ... -f migrations/20260721_reporter_otp_plain.sql
```

Do **not** drop `citizen_complaints` or staff tables (`users`, `work_orders`, …) — they belong to the separate DRGP admin side.

---

## OTP (development)

Add to `.env`:

```
REPORTER_OTP_PROVIDER=log
REPORTER_OTP_RETURN=1
REPORTER_OTP_LENGTH=6
REPORTER_OTP_TTL_S=86400
```

OTP is stored in `reporter_otp_challenges` and mirrored to `data/reporter_otps.json`. The same code is reused until **Forgot OTP? Send a new code** is tapped or TTL expires. Clear `REPORTER_OTP_RETURN` before enabling real SMS in production.

---

## Release APK (arm64 only)

```bash
cd reporter_app
npx expo prebuild --platform android --clean
cd android
.\gradlew.bat assembleRelease --no-daemon
```

Arm64-only stripping is handled by [`plugins/withArm64Only.js`](./plugins/withArm64Only.js) to keep APK size minimal.

---

## S3 media layout

```
user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}.jpg        ← photo
user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}.mp4        ← video
user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}_log.json   ← GPS frame meta (required)
```

Server always writes the `_log.json` sibling (using client-supplied GPS if provided, otherwise synthesised from form fields).

---

*Architecture and implementation assisted by **IBM Bob** — IBM's AI software engineering assistant.*
