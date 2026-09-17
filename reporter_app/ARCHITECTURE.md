# SmartRoad Reporter App — Architecture

> This document was authored by **IBM Bob**, IBM's AI software engineering assistant, as part of defining and implementing the reporter app's structure, data flow, and module boundaries.

---

## Overview

The SmartRoad Citizen Reporter app is a **React Native (Expo)** mobile application that allows Indian citizens to:

1. Authenticate via mobile OTP (India +91).
2. File GPS-tagged road defect complaints with photo or video evidence.
3. Track complaint status through to resolution.
4. Manage their profile and app settings.

All communication goes through a single REST backend at `/api/reporter/*` using Bearer JWT tokens.

---

## Folder structure

```
reporter_app/
├── App.js                        ← Root shell: auth gate, tab bar, settings modal
├── index.js                      ← Expo entry point
├── app.json                      ← Expo config
├── babel.config.js
├── package.json
├── plugins/
│   └── withArm64Only.js          ← Native build plugin (arm64 APK size reduction)
├── assets/                       ← App icons and splash assets
└── src/
    ├── api.js                    ← All HTTP calls + offline draft queue
    ├── mobile.js                 ← Indian mobile number normalisation helpers
    ├── uploadConfig.js           ← S3 path schema + GPS frame meta builder
    ├── hooks/                    ← Reusable React hooks
    │   ├── useAuth.js
    │   ├── useComplaints.js
    │   └── useLocation.js
    ├── components/               ← Shared UI primitives
    │   ├── ConfirmModal.js
    │   ├── EmptyState.js
    │   ├── ErrorBanner.js
    │   ├── GPSIndicator.js
    │   ├── MediaPreview.js
    │   └── StatusBadge.js
    ├── screens/                  ← Full-page screen components
    │   ├── LoginScreen.js
    │   ├── FileComplaintScreen.js
    │   ├── TrackComplaintScreen.js
    │   ├── ComplaintDetailScreen.js
    │   ├── ProfileScreen.js
    │   └── SettingsScreen.js
    └── utils/                    ← Pure utility functions (no React)
        ├── formatters.js
        ├── validators.js
        └── networkRetry.js
```

---

## Module responsibilities

### `App.js` — Root shell

- Renders the boot splash, login gate, or the main tab shell depending on `useAuth` state.
- Owns the three bottom tabs: **File**, **Track**, **Profile**.
- Renders the **Settings** screen inside a `<Modal>` opened via the ⚙ header button.
- Calls `flushDraftQueue()` whenever an authenticated session becomes active, silently retrying any complaints queued while the device was offline.

---

### `src/api.js` — HTTP layer + offline queue

The single source of truth for all network calls. Never import `fetch` directly in screen code.

**Auth endpoints**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/reporter/auth/lookup` | Check if mobile is registered |
| POST | `/api/reporter/auth/otp/request` | Send / resend OTP |
| POST | `/api/reporter/auth/otp/verify` | Verify OTP → receive JWT |
| GET | `/api/reporter/auth/me` | Fetch current reporter profile |
| POST | `/api/reporter/auth/logout` | Server-side token invalidation |

**Complaint endpoints**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/reporter/categories` | Fetch available defect categories |
| GET | `/api/reporter/trackcomplaint` | List all complaints for this user |
| GET | `/api/reporter/complaint/:id` | Fetch a single complaint |
| POST | `/api/reporter/filecomplaint` | Submit a new complaint (multipart) |
| DELETE | `/api/reporter/complaint/:id` | Delete an open complaint |

**Profile endpoints**

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/api/reporter/profile` | Update display name |
| GET | `/api/reporter/stats` | Aggregate complaint counts |

**Offline draft queue**

Complaints that fail due to a network error (no HTTP status code) are serialised to `AsyncStorage` under `sr_reporter_draft_queue`. On next successful session boot, `flushDraftQueue()` iterates the queue and retries each entry. Entries are removed on success.

```
AsyncStorage keys:
  sr_reporter_token        ← JWT
  sr_reporter_api_base     ← overridden server URL
  sr_reporter_draft_queue  ← JSON array of pending complaint payloads
  sr_reporter_file_draft   ← auto-saved form draft (current session)
```

---

### `src/hooks/`

#### `useAuth`
Central authentication state machine.

```
boot()  →  getToken() → api.me() → setReporter(me)
                      ↘ no token → reporter = null

login(payload)  →  setToken(jwt) → api.me() → setReporter(me)

logout()  →  api.logout() → setToken('') → reporter = null
```

`App.js` and `LoginScreen` only ever interact with `useAuth` — no screen imports `setToken` directly.

#### `useComplaints`
Manages the complaint list with client-side filtering.

- Accepts `{ statusFilter, searchQuery, disabled }`.
- `disabled: true` prevents any API call (used in `App.js` before login).
- Exposes `complaints` (filtered), `allComplaints` (unfiltered), `loading`, `refreshing`, `refresh()`, `prependOptimistic()`, `removeById()`, `updateById()`.

#### `useLocation`
Wraps `expo-location` with a clean React interface.

- Requests foreground permission on mount.
- Fetches an initial high-accuracy GPS fix.
- Optionally starts a continuous `watchPositionAsync` subscription (`watch: true`).
- Exposes `coords`, `acquiring`, `error`, `refresh()`.
- Cleans up the watcher subscription on unmount.

---

### `src/components/`

All components are **presentational** — they receive props and render UI, with no direct API calls.

| Component | Purpose |
|---|---|
| `StatusBadge` | Colour-coded pill for complaint status (`open` / `in_progress` / `resolved` / `rejected`) |
| `MediaPreview` | Photo or video thumbnail with play overlay, dimension ribbon, and removable × button |
| `GPSIndicator` | Coloured dot + monospace coords row showing Excellent / Good / Fair / Poor accuracy |
| `EmptyState` | Centred icon + title + subtitle + optional action button for zero-data views |
| `ErrorBanner` | Dismissible inline banner with `error` / `warning` / `info` / `success` themes |
| `ConfirmModal` | Full-screen backdrop modal with cancel / confirm actions and destructive variant |

---

### `src/screens/`

#### `LoginScreen`
Two-step flow: mobile number → OTP entry. Calls `onLoggedIn(payload)` (wired to `useAuth.login` in `App.js`) — does not touch the token directly.

#### `FileComplaintScreen`
The primary complaint-filing form.

**Data flow:**
```
useLocation()         → coords + accuracy
api.categories()      → defect type chips
ImagePicker           → asset (photo or video)
validateComplaintForm → errors[]
FileSystem.write      → capture_log.json (GPS meta sidecar)
api.submitComplaint   → tracking_number  (success screen)
                      ↘ network error   → enqueueDraft()  (offline queue)
```

Draft auto-save: any time `defectType`, `description`, or `asset` changes, the form is serialised to `AsyncStorage`. On next mount, the draft is restored and the user is shown a dismissible info banner.

#### `TrackComplaintScreen`
List view with:
- Live search bar (tracking number / category / description).
- Status filter chips (All / Open / In Progress / Resolved / Rejected).
- Pull-to-refresh via `RefreshControl`.
- Tap a card → `ComplaintDetailScreen` (inline navigation, no React Navigation dependency).
- Long-press an open complaint → `ConfirmModal` delete flow.

#### `ComplaintDetailScreen`
Read-only detail view. Shows all stored metadata, media preview, GPS coordinates with a **Open in Maps** deep-link (Google Maps on Android, Apple Maps on iOS), and a timeline of status timestamps.

#### `ProfileScreen`
Displays five aggregate stat counters (Total / Open / In Progress / Resolved / Rejected), the last five filed complaints, and a sign-out button guarded by `ConfirmModal`.

#### `SettingsScreen`
- **Server section** — editable API base URL with live validation and save/reset.
- **About section** — app version, platform.
- **Developer section** (dev builds only, guarded by `__DEV__`) — current base URL, clear auth token.

---

### `src/utils/`

#### `validators.js`
Pure functions returning `{ valid: boolean, message: string }`. No React dependency — can be unit-tested in isolation.

- `validateMobile` — Indian 10-digit mobile format.
- `validateOtp` — length 4–8 digits.
- `validateDefectType`, `validateDescription`, `validateGps`, `validateMediaAsset`.
- `validateComplaintForm` — runs all form checks, returns `string[]` of errors.
- `gpsAccuracyWarning` — soft warning when accuracy > 50 m.
- `validateApiBaseUrl` — URL format check for Settings screen.
- GPS bounding-box check (India) is skipped in `__DEV__` so emulators work.

#### `formatters.js`
Pure display-formatting functions. No React dependency.

- `formatDateTime`, `formatDate`, `formatRelativeTime` — locale-aware (`en-IN`).
- `formatCoords`, `formatAccuracy`, `gpsQualityLabel` — GPS presentation.
- `formatStatus`, `statusColors` — human labels and colour pairs for each status.
- `formatFileSize`, `formatTrackingNumber`, `formatDefectType`, `formatMobile`.

#### `networkRetry.js`
Exponential back-off retry with jitter.

- `withRetry(fn, opts)` — standalone async helper; non-retriable 4xx codes (except 429) are not retried.
- `useRetry(opts)` — React hook that surfaces `attempt`, `retrying`, `error`, and a `run(fn)` wrapper with cancel-on-unmount.

---

## Authentication flow

```
App boots
  └─ useAuth.boot()
       ├─ no token  →  render LoginScreen
       └─ token exists  →  api.me()
            ├─ 200  →  setReporter(me)  →  render tab shell
            └─ 401  →  clear token  →  render LoginScreen

LoginScreen
  └─ Enter mobile  →  api.requestOtp()
  └─ Enter OTP    →  api.verifyOtp()  →  onLoggedIn(payload)
                                              └─ useAuth.login(payload)
                                                   └─ setToken(jwt) → api.me() → reporter set
```

---

## GPS & media upload contract

The server expects a `multipart/form-data` POST to `/api/reporter/filecomplaint` containing:

| Field | Type | Notes |
|---|---|---|
| `defect_type` | string | Category from `/api/reporter/categories` |
| `description` | string | Optional free text |
| `latitude` | string | Decimal degrees |
| `longitude` | string | Decimal degrees |
| `gps_accuracy_m` | string | Optional, metres |
| `captured_at` | string | ISO 8601 timestamp |
| `media` | file | `capture.jpg` or `capture.mp4` |
| `frame_meta` | file | `capture_log.json` — GPS frame meta (see `uploadConfig.js`) |

The `frame_meta` JSON shape matches the videographer `*_log.json` format used by the DRGP pipeline, enabling unified processing of both citizen and staff-captured media.

---

## Design decisions made with IBM Bob

| Decision | Rationale |
|---|---|
| Custom hooks over inline `useEffect` | `useAuth`, `useComplaints`, `useLocation` are independently testable and prevent logic duplication across screens |
| `disabled` prop on `useComplaints` | Prevents unauthenticated API calls on cold boot before the JWT is verified |
| `setBusy(true)` before all async I/O | Eliminates double-tap race conditions on the Submit button |
| `mountedRef` initialised `false` | Correct lifecycle semantics — prevents stale-closure state updates after unmount |
| Offline draft queue in `api.js` | Decoupled from UI; flushed automatically on next successful session, not on every retry attempt |
| GPS bounding-box skip in `__DEV__` | Emulators return `(0, 0)` — hard-blocking developers would prevent all local testing |
| `Co-authored-by` git trailer | Makes IBM Bob's contribution visible in the GitHub commit history |

---

*This architecture document was written by **IBM Bob** — IBM's AI software engineering assistant.*  
*Repository: [github.com/abhinavtejathota/Road_Pothole_Detector](https://github.com/abhinavtejathota/Road_Pothole_Detector)*
