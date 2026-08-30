# Security hardening (portal + field upload + reporter OTP)

This note documents the security fixes applied across SmartRoad AP (Flask portal, field-capture multipart upload, reporter OTP, survey GIS, and map XSS surfaces). Use it as the ops checklist when deploying or auditing.

Related: [DEPLOYMENT.md](./DEPLOYMENT.md), [MULTI_SERVICE.md](./MULTI_SERVICE.md), [server.md](./server.md).

---

## Production environment (required)

Set these in the process `.env` (or systemd/`EnvironmentFile`) **before** marking the host live:

```bash
SMARTROAD_ENV=production
FLASK_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
JWT_SECRET=<same or separate long secret>          # mobile Bearer JWT; falls back to FLASK_SECRET_KEY
CORS_ALLOWED_ORIGINS=https://portal.example.com    # comma-separated; no wildcards
SESSION_COOKIE_SECURE=1                            # HTTPS only (default on when SMARTROAD_ENV=production)
SESSION_COOKIE_SAMESITE=Lax                        # optional override
REPORTER_OTP_PROVIDER=<real SMS provider>          # "log" is forbidden in production
REPORTER_OTP_PEPPER=<optional; defaults to FLASK_SECRET_KEY>
REPORTER_JWT_SECRET=<optional; defaults to JWT_SECRET / FLASK_SECRET_KEY>
```

Bootstrap the first admin (no hardcoded credentials):

```bash
BOOTSTRAP_ADMIN_USERNAME=your_admin
BOOTSTRAP_ADMIN_PASSWORD='long-unique-passphrase'   # ≥ 12 characters
BOOTSTRAP_ADMIN_EMAIL=admin@example.com             # optional
BOOTSTRAP_ADMIN_FULL_NAME=Administrator             # optional
python scripts/bootstrap_admin.py
```

Change the admin password after first login.

---

## What was hardened

### 1. App / sessions (`routes/app_factory.py`, `routes/session_guard.py`, `routes/token_auth.py`)

| Change | Detail |
|--------|--------|
| Strong secrets | `FLASK_SECRET_KEY` must be set in production; the old static fallback `smartroad-phase3-secret-change-me` is refused when `SMARTROAD_ENV=production`. Dev still warns if missing/short. |
| Cookie flags | Session cookie is always `HttpOnly`. `SameSite` defaults to `Lax`. `Secure` defaults on in production (override with `SESSION_COOKIE_SECURE`). |
| CORS allowlist | Origins come from `CORS_ALLOWED_ORIGINS` (comma-separated). Credentials are only echoed for allowlisted origins. Localhost Flask/Vite ports remain the **dev-only** default when the env var is unset. |
| No session echo | Responses no longer set `X-Session-Cookie` (that previously exposed the session value to any script that could read response headers). Legacy **inbound** `X-Session-Cookie` is still accepted for older mobile clients when no Bearer token is present. |
| Idle timeout | Web cookie idle enforcement is **not** bypassed by sending `X-Client: mobile` alone. Mobile auth is Bearer JWT with its own TTL. |
| Debug / bind | Werkzeug `FLASK_DEBUG` is refused on non-loopback hosts and when `SMARTROAD_ENV=production` (debugger is remote RCE). |
| Error leakage | Unhandled 500 responses strip internal `detail` / traceback from the JSON body in production-style runs. |
| JWT secrets | Mobile / reporter JWT signing fails closed in production if no `JWT_SECRET` / `FLASK_SECRET_KEY` / `REPORTER_JWT_SECRET` is configured. |

### 2. Authorization — work orders, vendors, dashboard (`routes/api/tasks.py`, `vendors.py`, `dashboard.py`, `users.py`)

| Change | Detail |
|--------|--------|
| Work-order status | `POST /api/tasks/<id>/status` enforces role + ownership: vendors may only advance their own WOs (`WIP` / `Completed`); Verified/Failed remain staff; Allocated/Created remain allocator/admin. |
| Videographer scope | Videographers cannot list/detail work orders or vendors, and do not receive admin KPI / map payloads on the staff dashboard. |
| Vendor scope | Vendor-role users see only their own vendor record / WOs. |
| Dashboard map | `GET /api/dashboard/map-data` is blocked for videographers (staff/admin only). |
| User create | Role allowlist (`VALID_USER_ROLES`); password minimum length 8; safer create error messages. |

### 3. S3 multipart field upload (`routes/field_upload_service.py`, `routes/api/upload_routes.py`)

| Change | Detail |
|--------|--------|
| Key ownership | `presign_parts` and `abort_multipart` **require** the authenticated username and reject keys that do not start with that user’s upload prefix. |
| Bucket | Client-supplied `bucket` is ignored; the server always uses the configured input bucket (`get_input_bucket()`). |

This closes the previous IDOR where any logged-in user could presign/abort arbitrary keys or point multipart at another bucket.

### 4. Reporter OTP (`routes/reporter_otp_service.py`)

| Change | Detail |
|--------|--------|
| Entropy | OTP digits from `secrets.randbelow`, not `random`. |
| Storage | DB stores **hash only** (`plain_otp` inserted as `NULL`). Pepper = `REPORTER_OTP_PEPPER` or `FLASK_SECRET_KEY`. |
| Consume | Successful verify sets `consumed_at` so codes cannot be replayed. |
| TTL | Default `REPORTER_OTP_TTL_S=300` (5 minutes). Max attempts via `REPORTER_OTP_MAX_ATTEMPTS` (default 5). |
| No prod echo | `REPORTER_OTP_RETURN`, JSON mirror (`REPORTER_OTP_JSON_MIRROR`), and plaintext logging are disabled/forbidden when `SMARTROAD_ENV=production`. `REPORTER_OTP_PROVIDER=log` raises in production. |
| Enumeration | Mobile lookup returns a uniform message and does **not** disclose whether the number exists or has an active OTP. |

Dev-only helpers (never enable in prod):

```bash
REPORTER_OTP_PROVIDER=log
REPORTER_OTP_RETURN=1          # echo otp in JSON as dev_otp
REPORTER_OTP_LOG=1             # print OTP to process stdout
REPORTER_OTP_JSON_MIRROR=1     # write data/reporter_otps.json
```

### 5. Survey GIS path safety (`routes/survey/state.py`, `routes/api/survey_routes.py`)

| Change | Detail |
|--------|--------|
| `state_key` | Must resolve via allowlist (`andhra` / `telangana`) — `../` and unknown keys are rejected. |
| `district_id` | Must be numeric (LGD id). Overview/segments routes coerce with `int(...)`. |
| Path confinement | GeoJSON / overview paths are resolved under `data/gis_states/<state>/` and rejected if they escape that tree. |

### 6. Videographer district self-pick (`routes/survey/state.py` → `update_videographer_districts_self`)

Videographers may only assign themselves districts that belong to **their** state (from `list_districts(state_key)`). Cross-state self-grant is rejected with a clear error.

### 7. XSS — map popups (`frontend/src/utils/roadSegmentLabel.js`, `mobile_app/src/components/OsmMap.js`)

| Surface | Change |
|---------|--------|
| Web Leaflet popups | `roadSegmentPopupHtml` HTML-escapes title, area, road class, **and** status/km meta before `bindPopup`. |
| Mobile OsmMap WebView | Marker titles escaped; stroke/pin colors restricted to `#hex` / `rgb(a)`; `originWhitelist` limited to `about:blank`; `mixedContentMode="never"`. |

### 8. Health endpoint (`routes/api/auth.py`)

Shallow `GET /api/health` returns only liveness fields (`ok`, `utc`, `epoch`, `heavy_inflight`, `service`). It no longer exposes `pid` or `db_host`.

`?deep=1` still runs a DB ping and may include device/queue diagnostics, but still omits host/pid. Treat deep health as ops-facing (restrict at the reverse proxy if the portal is public).

### 9. Bootstrap admin (`scripts/bootstrap_admin.py`)

Hardcoded `senxxel` / password defaults were removed. The script requires `BOOTSTRAP_ADMIN_USERNAME` + `BOOTSTRAP_ADMIN_PASSWORD` (≥12 chars), refuses weak passwords in production, and does **not** print the password on success.

---

## Code map (quick reference)

| Area | Primary modules |
|------|-----------------|
| App factory / CORS / cookies / debug | `routes/app_factory.py` |
| Session idle / epoch | `routes/session_guard.py` |
| Mobile JWT | `routes/token_auth.py` |
| Reporter JWT / OTP | `routes/reporter_token_auth.py`, `routes/reporter_otp_service.py` |
| Tasks / vendors / dashboard / users | `routes/api/tasks.py`, `vendors.py`, `dashboard.py`, `users.py` |
| Multipart S3 | `routes/field_upload_service.py`, `routes/api/upload_routes.py` |
| Survey GIS | `routes/survey/state.py`, `routes/api/survey_routes.py` |
| Map XSS | `frontend/src/utils/roadSegmentLabel.js`, `mobile_app/src/components/OsmMap.js` |
| Admin bootstrap | `scripts/bootstrap_admin.py` |

---

## Deploy checklist

- [ ] `SMARTROAD_ENV=production`
- [ ] Unique `FLASK_SECRET_KEY` (≥32 bytes hex/base64); same value on all multi-service workers (see MULTI_SERVICE.md)
- [ ] `CORS_ALLOWED_ORIGINS` set to the real HTTPS portal origin(s) only
- [ ] TLS terminated so `SESSION_COOKIE_SECURE=1` works
- [ ] `REPORTER_OTP_PROVIDER` is a real SMS integration (not `log`)
- [ ] Admin created via env-based `bootstrap_admin.py`; default/demo passwords rotated
- [ ] Reverse proxy does not forward arbitrary `Origin` / strips debug tooling
- [ ] Confirm shallow `/api/health` does not leak infra (`pid`, `db_host`)
- [ ] Field apps use Bearer JWT (or cookie over HTTPS); do not rely on response `X-Session-Cookie`

---

## Residual risks (not yet implemented)

These remain known gaps — track separately if needed:

1. **Login / OTP rate limiting** — no per-IP / per-account throttle yet (pair with reverse-proxy limits).
2. **CSRF tokens** — browser cookie auth relies on `SameSite=Lax` + CORS allowlist; not full synchronizer-token CSRF.
3. **GIS snap caches** — district snap indexes under `data/gis_states/**/*.snap.pkl` still use `pickle` (server-written files only; treat the GIS tree as trusted).
4. **Deep health** — still anonymous; lock down at nginx if unwanted.
5. **Legacy `_api_monolith.py`** — keep out of import path; prefer modular `routes/api/`.

### Private-name import audit

After the modular splits (`detector/`, `db/`, `routes/survey|tracking|detection`), `from x import *` silently skips `_private` helpers and causes runtime `NameError` (e.g. `_open_video_capture`).

Run before push / deploy:

```bash
python scripts/audit_private_imports.py
```

Must exit 0. Re-wire helpers with `detector._importutil.reexport` / `globals().update(...)` (see `scripts/fix_star_import_privates.py`), and define callables in the module that uses them (not only in a later chain sibling).

---

## Verification smoke

```bash
# Shallow health — no pid / db_host
curl -s http://127.0.0.1:5000/api/health

# App refuses insecure secret when production-flagged
SMARTROAD_ENV=production FLASK_SECRET_KEY= python -c "from routes.app_factory import create_app; create_app()"
# → RuntimeError: FLASK_SECRET_KEY must be set ...

# Bootstrap refuses missing env
python scripts/bootstrap_admin.py
# → exit 1, asks for BOOTSTRAP_ADMIN_*
```

Unit coverage exercised after the hardening pass includes survey match buffers, coverage trail, geocode helpers, and road-hugging display (`python -m unittest …` from the project venv).
