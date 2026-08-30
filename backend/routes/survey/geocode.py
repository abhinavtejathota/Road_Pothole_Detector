"""survey.geocode — extends state (includes private _names)."""
from __future__ import annotations

import routes.survey.state as _state

globals().update({k: v for k, v in vars(_state).items() if not k.startswith('__')})

# ── Geocoding + place / road search ───────────────────────────────────────────

_NOMINATIM_CACHE: dict[str, tuple[float, list | dict]] = {}
_NOMINATIM_CACHE_TTL = float(os.getenv("NOMINATIM_CACHE_S", "3600"))  # 1h
_NOMINATIM_LAST_CALL = 0.0
_NOMINATIM_MIN_INTERVAL = 0.35  # soft throttle; public Nominatim is strict

_GEOCODE_RESULT_CACHE: dict[str, tuple[float, list]] = {}
_GEOCODE_RESULT_CACHE_TTL = float(os.getenv("GEOCODE_CACHE_S", "1800"))  # 30 min


def _http_json_get(url: str, *, timeout: float = 4.0) -> list | dict:
    req = urllib.request.Request(url, headers={"User-Agent": NOMINATIM_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _nominatim_get(url: str, *, timeout: float = 4.0) -> list | dict:
    """Cached Nominatim GET. Soft-throttle; never sleep >0.35s on the request path."""
    global _NOMINATIM_LAST_CALL
    now = time.time()
    hit = _NOMINATIM_CACHE.get(url)
    if hit and (now - hit[0]) < _NOMINATIM_CACHE_TTL:
        return hit[1]
    wait = _NOMINATIM_MIN_INTERVAL - (now - _NOMINATIM_LAST_CALL)
    if wait > 0:
        time.sleep(min(wait, _NOMINATIM_MIN_INTERVAL))
    try:
        data = _http_json_get(url, timeout=timeout)
    except Exception:
        _NOMINATIM_LAST_CALL = time.time()
        raise
    _NOMINATIM_LAST_CALL = time.time()
    _NOMINATIM_CACHE[url] = (_NOMINATIM_LAST_CALL, data)
    if len(_NOMINATIM_CACHE) > 400:
        oldest = sorted(_NOMINATIM_CACHE.items(), key=lambda kv: kv[1][0])[:80]
        for k, _ in oldest:
            _NOMINATIM_CACHE.pop(k, None)
    return data


def _photon_search(
    query: str,
    *,
    limit: int = 8,
    lat: float | None = None,
    lon: float | None = None,
    bbox: str | None = None,
    timeout: float = 3.0,
) -> list[dict]:
    """Fast OSM forward geocode via Photon (same map data, typed for autocomplete)."""
    q = (query or "").strip()
    if not q:
        return []
    params: dict[str, str] = {
        "q": q,
        "limit": str(max(1, min(limit, 12))),
        "lang": "en",
    }
    if lat is not None and lon is not None:
        params["lat"] = f"{float(lat):.5f}"
        params["lon"] = f"{float(lon):.5f}"
    if bbox:
        params["bbox"] = bbox
    url = PHOTON_URL + "?" + urllib.parse.urlencode(params)
    now = time.time()
    hit = _NOMINATIM_CACHE.get(url)
    if hit and (now - hit[0]) < _NOMINATIM_CACHE_TTL:
        data = hit[1]
    else:
        data = _http_json_get(url, timeout=timeout)
        _NOMINATIM_CACHE[url] = (now, data)
    out: list[dict] = []
    for feat in (data.get("features") if isinstance(data, dict) else None) or []:
        try:
            props = feat.get("properties") or {}
            cc = str(props.get("countrycode") or props.get("country") or "").lower()
            if cc and cc not in ("in", "india", "ind"):
                continue
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            lon_f, lat_f = float(coords[0]), float(coords[1])
            bits = [
                props.get("name"),
                props.get("street") or props.get("road"),
                props.get("locality") or props.get("district") or props.get("city") or props.get("town"),
                props.get("county"),
                props.get("state"),
                props.get("country"),
            ]
            display = ", ".join(str(b) for b in bits if b)
            if not display:
                continue
            osm_key = str(props.get("osm_key") or "")
            osm_val = str(props.get("osm_value") or props.get("type") or "place")
            out.append({
                "display_name": display,
                "lat": lat_f,
                "lon": lon_f,
                "type": osm_val,
                "class": osm_key or "place",
                "source": "photon",
            })
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _parse_lat_lon_query(query: str) -> tuple[float, float] | None:
    q = (query or "").strip()
    m = re.match(
        r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$",
        q,
    )
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    # India lon ~68–97, lat ~8–35 — accept either order.
    if 8 <= a <= 35 and 68 <= b <= 97:
        return a, b
    if 8 <= b <= 35 and 68 <= a <= 97:
        return b, a
    # Any other valid WGS84 pair (My location / paste outside India).
    if abs(a) <= 90 and abs(b) <= 180:
        return a, b
    if abs(b) <= 90 and abs(a) <= 180:
        return b, a
    return None


def search_roads_in_district(
    query: str,
    *,
    state_key: str,
    district_id: str,
    limit: int = 8,
) -> list[dict]:
    """Substring match against road name/ref in the district GeoJSON index."""
    q = (query or "").strip().lower()
    if not q or len(q) < 2:
        return []
    for junk in (" district", " · telangana", " · andhra pradesh", ", india"):
        q = q.replace(junk, "")
    q_compact = re.sub(r"[^a-z0-9]+", "", q)
    tokens = [t for t in re.sub(r"[—\-_/]+", " ", q).split() if len(t) > 1]
    if not tokens and len(q_compact) < 3:
        return []
    meta = _segment_meta(state_key, str(district_id))
    scored: list[tuple[int, dict]] = []
    for sid, m in meta.items():
        name = (m.get("name") or "").lower()
        ref = (m.get("ref") or "").lower()
        hay = f"{ref} {name}".strip()
        if not hay:
            continue
        hay_compact = re.sub(r"[^a-z0-9]+", "", hay)
        hits = sum(1 for t in tokens if t in hay) if tokens else 0
        compact_hit = bool(q_compact and len(q_compact) >= 4 and q_compact in hay_compact)
        if hits == 0 and not compact_hit:
            continue
        score = hits * 10 + (12 if compact_hit else 0)
        score += 5 if ref and any(t in ref for t in tokens) else 0
        score += min(3, int(m["length"]))
        mid = m["mid"]
        label_bits = [x for x in (m.get("ref"), m.get("name")) if x]
        display = " — ".join(label_bits) if label_bits else sid
        dist_name = m.get("district_name") or ""
        if dist_name:
            display = f"{display} ({dist_name})"
        scored.append((score, {
            "display_name": display,
            "lat": mid[0],
            "lon": mid[1],
            "type": "road",
            "class": m.get("road_class") or "other",
            "source": "road_index",
            "segment_id": sid,
            "district_id": str(district_id),
            "state_key": state_key,
            "length_km": round(m["length"], 3),
        }))
    scored.sort(key=lambda t: (-t[0], -t[1].get("length_km", 0)))
    out = []
    seen = set()
    for _, row in scored:
        key = (round(row["lat"], 4), round(row["lon"], 4), row["display_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


_ROAD_QUERY_RE = re.compile(
    r"^\s*(nh|sh|mdr)\s*-?\s*\d*\s*$"
    r"|^\s*(nh|sh)\s*-?\s*\d+\b"
    r"|\b(highway|expressway|bypass|service road)\b",
    re.I,
)


def _query_prefers_places(query: str) -> bool:
    """Prefer Nominatim places unless the query is clearly a highway/road ref.

    No place-name hardcoding — multi-word landmarks (colleges, hospitals, …)
    go to Nominatim; ``NH44`` / ``SH1`` stay on the local road index.
    """
    q = (query or "").strip()
    if not q:
        return False
    tokens = [t for t in re.split(r"\s+", q) if t]
    if _ROAD_QUERY_RE.search(q) and len(tokens) <= 4:
        return False
    return True


def _geocode_query_variants(query: str) -> list[str]:
    """Light normalization only (punctuation / camelCase / digit boundaries).

    Does not map specific landmarks to canned names — Nominatim gets the
    user's wording (plus district/state context added by the caller).
    """
    raw = (query or "").strip()
    if not raw:
        return []
    spaced = re.sub(r"[^a-zA-Z0-9]+", " ", raw).strip()
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", spaced)
    spaced = re.sub(r"([A-Za-z])(\d)", r"\1 \2", spaced)
    spaced = re.sub(r"(\d)([A-Za-z])", r"\1 \2", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    out: list[str] = []
    for v in (raw, spaced):
        if v and v not in out:
            out.append(v)
    low = spaced.lower()
    if low and low not in {x.lower() for x in out}:
        out.append(low)
    return out


def _build_geocode_remote_queries(
    variants: list[str],
    enrich_districts: list[dict],
    state_label: str,
) -> list[str]:
    """Photon/Nominatim query strings — raw wording first, enrichment last.

    District-enriched strings like ``eluru, Ntr, …`` poison Photon (returns
    "NTR park" instead of Eluru city). Short district labels are skipped.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def _add(s: str):
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            queries.append(s)

    for vq in variants:
        _add(vq)
    for vq in variants:
        q_lower = vq.lower()
        if state_label and state_label.lower() not in q_lower and "india" not in q_lower:
            _add(f"{vq}, {state_label}, India")
    for vq in variants:
        q_lower = vq.lower()
        for drow in enrich_districts:
            dname = (drow.get("name") or "").strip()
            if not dname or dname.lower() in q_lower:
                continue
            if len(re.sub(r"[^a-zA-Z]", "", dname)) < 5:
                continue
            _add(f"{vq}, {dname}, {state_label or 'India'}".strip(", "))
    return queries


def _geocode_display_label(row: dict) -> str:
    """Stable UI label key for deduping autocomplete rows."""
    name = str(row.get("display_name") or "").strip()
    primary = name.split(",")[0].strip().lower()
    dist = str(row.get("district_name") or row.get("district_id") or "").strip().lower()
    return f"{primary}|{dist}"


def _dedupe_geocode_results(rows: list[dict]) -> list[dict]:
    """Drop duplicate autocomplete rows (same place name + district label)."""
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows or []:
        key = _geocode_display_label(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _geocode_rows_match_query(rows: list[dict], query: str) -> bool:
    """True when a hit's primary name overlaps the user's search text."""
    q = (query or "").strip().lower()
    if not q:
        return False
    q_tokens = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) > 2]
    prefix = q[: max(3, min(len(q), 6))]
    for r in rows or []:
        name = str(r.get("display_name") or "").lower()
        primary = name.split(",")[0].strip()
        if primary == q or (prefix and primary.startswith(prefix)):
            return True
        if q_tokens and any(t in name for t in q_tokens):
            return True
    return False


def locate_point_in_state(
    lat: float,
    lon: float,
    state_key: str,
    *,
    max_km: float = 8.0,
    allow_center_fallback: bool = True,
    max_center_km: float = 35.0,
) -> dict | None:
    """Find nearest district whose road network is close to the point.

    Center-only fallback is capped by max_center_km so a Telangana city is not
    falsely snapped to a distant Andhra district (and vice versa).
    """
    districts = list_districts(state_key)
    # Sort by distance to district center so we check likely districts first
    ranked = []
    for dist in districts:
        c = dist.get("center") or [None, None]
        if c[0] is None:
            ranked.append((1e9, dist))
        else:
            ranked.append((_haversine_km(lat, lon, float(c[0]), float(c[1])), dist))
    ranked.sort(key=lambda t: t[0])

    best = None
    best_d = max_km
    for center_d, dist in ranked[:8]:
        # Skip far centers unless nothing closer found yet
        if center_d > 60 and best is not None:
            continue
        did = str(dist["district_id"])
        meta = _segment_meta(state_key, did)
        local_best = None
        local_d = max_km
        for m in meta.values():
            ml, mo = m["mid"]
            if abs(ml - lat) > 0.12 or abs(mo - lon) > 0.12:
                continue
            d = _haversine_km(lat, lon, ml, mo)
            if d < local_d:
                local_d = d
                local_best = m
                if d < 0.15:
                    break
        if local_best is not None and local_d < best_d:
            best_d = local_d
            best = {
                "district_id": did,
                "district_name": dist.get("name") or did,
                "state_key": state_key,
                "state_id": STATE_ID_BY_KEY.get(state_key),
                "distance_km": round(local_d, 3),
                "snap_lat": local_best["mid"][0],
                "snap_lon": local_best["mid"][1],
            }
            if best_d < 0.25:
                return best
    if best:
        return best
    if allow_center_fallback and ranked and ranked[0][0] <= max_center_km:
        _, dist = ranked[0]
        return {
            "district_id": str(dist["district_id"]),
            "district_name": dist.get("name") or str(dist["district_id"]),
            "state_key": state_key,
            "state_id": STATE_ID_BY_KEY.get(state_key),
            "distance_km": round(ranked[0][0], 3),
            "by_center": True,
        }
    return None


def locate_point_by_district_center(
    lat: float,
    lon: float,
    *,
    state_keys: list[str] | None = None,
    max_center_km: float = 40.0,
) -> dict | None:
    """Fast district guess from centers only (no segment index load)."""
    keys = [k for k in (state_keys or list(STATE_ID_BY_KEY.keys())) if k in STATE_ID_BY_KEY]
    best = None
    best_d = max_center_km
    for sk in keys:
        for dist in list_districts(sk):
            c = dist.get("center") or [None, None]
            if c[0] is None:
                continue
            d = _haversine_km(lat, lon, float(c[0]), float(c[1]))
            if d < best_d:
                best_d = d
                best = {
                    "district_id": str(dist["district_id"]),
                    "district_name": dist.get("name") or str(dist["district_id"]),
                    "state_key": sk,
                    "state_id": STATE_ID_BY_KEY.get(sk),
                    "distance_km": round(d, 3),
                    "by_center": True,
                }
    return best


def locate_point_any_state(
    lat: float,
    lon: float,
    *,
    state_keys: list[str] | None = None,
    max_km: float = 8.0,
    allow_center_fallback: bool = True,
    max_center_km: float = 35.0,
) -> dict | None:
    """Best district match across one or more states (AP + TG)."""
    keys = [k for k in (state_keys or list(STATE_ID_BY_KEY.keys())) if k in STATE_ID_BY_KEY]
    best = None
    for sk in keys:
        loc = locate_point_in_state(
            lat, lon, sk,
            max_km=max_km,
            allow_center_fallback=False,
        )
        if not loc:
            continue
        if best is None or float(loc.get("distance_km") or 1e9) < float(best.get("distance_km") or 1e9):
            best = loc
    if best:
        return best
    if not allow_center_fallback:
        return None
    for sk in keys:
        loc = locate_point_in_state(
            lat, lon, sk,
            max_km=max_km,
            allow_center_fallback=True,
            max_center_km=max_center_km,
        )
        if not loc or not loc.get("by_center"):
            continue
        if best is None or float(loc.get("distance_km") or 1e9) < float(best.get("distance_km") or 1e9):
            best = loc
    return best


def locate_point_in_allowed_districts(
    lat: float,
    lon: float,
    allowed_district_ids: list | set | None,
    *,
    max_km: float = 10.0,
    max_center_km: float = 55.0,
) -> dict | None:
    """Snap a point to the VG's allowed districts only.

    Avoids Hyderabad-center wins over Ranga Reddy (and similar neighbour-district
    bias from global ``locate_point_by_district_center``).
    Loads segment indexes for nearby allowed districts only (closest first).
    """
    allowed = []
    for x in (allowed_district_ids or []):
        if x is None or str(x).strip() == "":
            continue
        s = str(x)
        if s not in allowed:
            allowed.append(s)
    if not allowed:
        return None

    # Rank by district-center distance; skip far districts before loading GeoJSON.
    ranked: list[tuple[float, str, str, dict]] = []
    for did in allowed:
        sk = _state_key_for_district_id(did)
        if not sk:
            continue
        dist = get_district(did, sk) or {}
        c = dist.get("center") or [None, None]
        if c[0] is None:
            ranked.append((1e9, did, sk, dist))
        else:
            ranked.append((
                _haversine_km(lat, lon, float(c[0]), float(c[1])),
                did, sk, dist,
            ))
    ranked.sort(key=lambda t: t[0])

    best = None
    best_d = max_km
    for center_d, did, sk, dist in ranked:
        if center_d > max(max_center_km, 80.0) and best is not None:
            continue
        # Ranga Reddy's center is far from Madhapur/Shilparamam even when RR roads
        # are metres away — never skip peers in a small allowed set (typical VG).
        if len(ranked) > 6 and best is not None and center_d > best_d + 40.0:
            continue
        hit = _nearest_snap_in_district(lat, lon, sk, did, max_km=best_d)
        if not hit:
            continue
        ml, mo, _name, _ref, _dname, d = hit
        if d < best_d:
            best_d = d
            best = {
                "district_id": did,
                "district_name": dist.get("name") or did,
                "state_key": sk,
                "state_id": STATE_ID_BY_KEY.get(sk),
                "distance_km": round(d, 3),
                "snap_lat": ml,
                "snap_lon": mo,
                "by_allowed": True,
            }
        # Do not early-return mid-loop: a later elongated district (RR) may have
        # a closer road than an earlier centre-nearer neighbour (Medchal).
    if best:
        return best

    # Center fallback — only among districts the user actually has
    if ranked and ranked[0][0] <= max_center_km:
        center_d, did, sk, dist = ranked[0]
        return {
            "district_id": did,
            "district_name": dist.get("name") or did,
            "state_key": sk,
            "state_id": STATE_ID_BY_KEY.get(sk),
            "distance_km": round(center_d, 3),
            "by_center": True,
            "by_allowed": True,
        }
    return None


def state_keys_for_district_ids(district_ids: list | set | None) -> list[str]:
    """Which GIS states contain any of the given LGD district ids."""
    want = {str(x) for x in (district_ids or []) if x is not None and str(x).strip() != ""}
    if not want:
        return []
    keys: list[str] = []
    for sk in STATE_ID_BY_KEY:
        for d in list_districts(sk):
            if str(d.get("district_id")) in want:
                keys.append(sk)
                break
    return keys


def annotate_geocode_access(
    results: list[dict],
    allowed_district_ids: list | set | None,
    *,
    require_allowed: bool = True,
) -> list[dict]:
    """Attach district/state + access_ok / access_message to geocode hits.

    For videographers: map pins onto their allowed districts only (fast center /
    border snap). Do not keep a global Hyderabad label that denies Madhapur /
    Shilparamam / Cyber Towers when the VG has Ranga Reddy or Medchal.
    """
    allowed = {str(x) for x in (allowed_district_ids or []) if x is not None and str(x).strip() != ""}
    out: list[dict] = []
    locate_memo: list[tuple[float, float, dict | None]] = []

    def _locate(lat: float, lon: float, row: dict) -> dict | None:
        did_known = str(row.get("district_id") or "").strip()
        if did_known and (not require_allowed or not allowed or did_known in allowed):
            return {
                "district_id": did_known,
                "district_name": row.get("district_name"),
                "state_key": row.get("state_key"),
                "state_id": row.get("state_id"),
            }
        for plat, plon, ploc in locate_memo:
            if abs(plat - lat) < 0.01 and abs(plon - lon) < 0.01:
                return dict(ploc) if ploc else None

        if require_allowed and allowed:
            # VG path: only their districts (RR/Medchal), never invent Hyd 507.
            located = locate_point_in_allowed_districts_fast(
                lat, lon, allowed, max_center_km=55.0, border_km=32.0,
            )
        else:
            located = locate_point_by_district_center(lat, lon, max_center_km=40.0)
            if allowed and located and str(located.get("district_id")) not in allowed:
                # Staff view with a district filter — soft remap to nearest allowed.
                alt = locate_point_in_allowed_districts_fast(
                    lat, lon, allowed, max_center_km=55.0, border_km=32.0,
                )
                if alt:
                    located = alt
        locate_memo.append((lat, lon, located))
        return located

    for r in results or []:
        row = dict(r)
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            row["access_ok"] = False
            row["access_message"] = "Invalid coordinates."
            out.append(row)
            continue
        located = _locate(lat, lon, row)
        if located:
            row["district_id"] = located.get("district_id")
            row["district_name"] = located.get("district_name") or row.get("district_name")
            row["state_key"] = located.get("state_key") or row.get("state_key")
            row["state_id"] = located.get("state_id") or row.get("state_id")
            # Nominatim/Photon display_names use OSM administrative boundaries that
            # may lag behind real-world reorganisations (e.g. Chintalapudi still
            # shows "West Godavari District" even though it's now Eluru district).
            # Strip the administrative suffix and use only the specific place name
            # so the dropdown always reflects the correct, snapped district.
            if row.get("source") in ("nominatim", "photon") and row.get("district_name"):
                full_dn = str(row.get("display_name") or "").strip()
                if full_dn:
                    parts = [p.strip() for p in full_dn.split(",") if p.strip()]
                    # Keep only the first comma-segment (the specific place/village/road name).
                    # The 2nd+ segments are typically district/state/country text that may
                    # use outdated administrative names after AP's 2022 reorganisation
                    # (e.g. Nominatim still returns "West Godavari" for Chintalapudi even
                    # though it's now Eluru district). The correct district_name is already
                    # set above from road-snapping; the frontend appends it separately.
                    row["display_name"] = parts[0] if parts else full_dn
        did = str(row.get("district_id") or "")
        if require_allowed:
            if not allowed:
                ok = False
                msg = "No districts are linked to your account. Ask an admin to assign districts."
            elif not did:
                ok = False
                msg = "Could not match this place to a survey district. Try a clearer landmark inside your districts."
            elif did not in allowed:
                name = row.get("district_name") or did
                sk = row.get("state_key") or ""
                state_label = STATE_BY_ID.get(STATE_ID_BY_KEY.get(sk, -1), {}).get("label") or sk
                ok = False
                msg = (
                    f"You do not have access to {name}"
                    + (f" ({state_label})" if state_label else "")
                    + f" (district {did}). Search can show nearby places, but you can only assign routes in your districts."
                )
            else:
                ok = True
                msg = None
        else:
            ok = True
            msg = None
        row["access_ok"] = ok
        row["access_message"] = msg
        out.append(row)
    return _dedupe_geocode_results(out)


def locate_point_in_allowed_districts_fast(
    lat: float,
    lon: float,
    allowed_district_ids: list | set | None,
    *,
    max_center_km: float = 55.0,
    border_km: float = 28.0,
) -> dict | None:
    """Map a point to a VG's districts by nearest road, not district-center.

    Ranga Reddy is elongated — Madhapur / Shilparamam / HITEC are RR even when
    Medchal's centroid is closer. With 2+ allowed districts we always road-snap
    every allowed district and keep the closest road (never invent Hyderabad).
    """
    allowed = []
    for x in (allowed_district_ids or []):
        if x is None or str(x).strip() == "":
            continue
        s = str(x)
        if s not in allowed:
            allowed.append(s)
    if not allowed:
        return None

    ranked: list[tuple[float, str, str, dict]] = []
    for did in allowed:
        sk = _state_key_for_district_id(did)
        if not sk:
            continue
        dist = get_district(did, sk) or {}
        c = dist.get("center") or [None, None]
        if c[0] is None:
            continue
        d = _haversine_km(lat, lon, float(c[0]), float(c[1]))
        ranked.append((d, did, sk, dist))
    if not ranked:
        return None
    ranked.sort(key=lambda t: t[0])

    def _by_center(row: tuple[float, str, str, dict]) -> dict:
        d, did, sk, dist = row
        return {
            "district_id": did,
            "district_name": dist.get("name") or did,
            "state_key": sk,
            "state_id": STATE_ID_BY_KEY.get(sk),
            "distance_km": round(d, 3),
            "by_center": True,
            "by_allowed": True,
        }

    # Prefer road snap for any allowed set (1 district included). Centroid-only
    # labeling near borders / elongated districts (e.g. Ranga Reddy) jumps people
    # into the wrong sibling district or state.
    snap = locate_point_in_allowed_districts(
        lat, lon,
        [r[1] for r in ranked],
        max_km=15.0 if len(ranked) >= 2 else 12.0,
        max_center_km=90.0,
    )
    if snap and not snap.get("by_center"):
        return snap

    if ranked[0][0] <= max_center_km:
        return _by_center(ranked[0])
    return None


def check_location_access(
    lat: float,
    lon: float,
    *,
    state_key: str | None = None,
    state_keys: list[str] | None = None,
    allowed_district_ids: list | set | None,
    selected_district_id: str | int | None = None,
    require_allowed: bool = True,
) -> dict:
    """Report whether a point falls in an allowed district; name missing access if not."""
    allowed = {str(x) for x in (allowed_district_ids or []) if x is not None and str(x).strip() != ""}
    keys = list(state_keys or [])
    if state_key and state_key not in keys:
        keys.insert(0, state_key)

    located = None
    if require_allowed and allowed:
        # Fast: VG districts only — do not load all-TG GIS or invent Medchal via center.
        located = locate_point_in_allowed_districts_fast(lat, lon, allowed)
        if not located:
            # Outside all allowed centers — label nearest global district for the deny message
            outsider = locate_point_by_district_center(lat, lon, max_center_km=40.0)
            return {
                "ok": False,
                "in_scope": False,
                "selected_ok": False,
                "missing_districts": (
                    [{
                        "district_id": outsider.get("district_id"),
                        "district_name": outsider.get("district_name"),
                        "state_key": outsider.get("state_key"),
                    }]
                    if outsider else []
                ),
                "located": outsider,
                "message": (
                    (
                        f"This location is in {outsider.get('district_name')} "
                        f"(district {outsider.get('district_id')}), which is not on your account. "
                        f"Ask an admin to grant that district access."
                    )
                    if outsider
                    else "Could not match this location to a survey district on your account."
                ),
            }
    else:
        # Staff / unrestricted: center first (fast), GIS only if needed elsewhere
        locate_keys = keys or list(STATE_ID_BY_KEY.keys())
        located = locate_point_by_district_center(
            lat, lon, state_keys=locate_keys, max_center_km=40.0,
        )
        if not located:
            located = locate_point_any_state(
                lat, lon, state_keys=locate_keys, allow_center_fallback=True, max_center_km=35.0,
            )
    if not located:
        return {
            "ok": False,
            "in_scope": False,
            "message": "Could not match this location to a district road network.",
            "located": None,
        }
    did = str(located["district_id"])
    if require_allowed:
        in_allowed = bool(allowed) and did in allowed
    else:
        in_allowed = (not allowed) or (did in allowed)
    selected_ok = True
    if selected_district_id is not None and str(selected_district_id).strip() != "":
        selected_ok = did == str(selected_district_id)
    missing = []
    if not in_allowed:
        missing.append({
            "district_id": did,
            "district_name": located.get("district_name") or did,
            "state_key": located.get("state_key"),
        })
    msg = None
    if require_allowed and not allowed:
        msg = "No districts are linked to your account. Ask an admin to assign districts."
    elif not in_allowed:
        miss = missing[0]
        state_label = ""
        sk = miss.get("state_key")
        if sk and sk in STATE_ID_BY_KEY:
            state_label = f" in {STATE_BY_ID[STATE_ID_BY_KEY[sk]]['label']}"
        msg = (
            f"This location is in {miss['district_name']}{state_label} "
            f"(district {miss['district_id']}), which is not on your account. "
            f"Ask an admin to grant that district access."
        )
    elif not selected_ok:
        msg = (
            f"This location is in {located.get('district_name')} — "
            f"it is on your account and will be used for this leg."
        )
        selected_ok = True
    return {
        "ok": in_allowed and selected_ok,
        "in_scope": in_allowed,
        "selected_ok": True if in_allowed else selected_ok,
        "missing_districts": missing,
        "located": located,
        "message": msg if not in_allowed else (
            f"Located in {located.get('district_name')}."
            if located else None
        ),
    }


def _bbox_for_districts(
    district_ids: list[str],
    state_key: str | None,
    *,
    pad: float = 0.55,
) -> tuple[float | None, float | None, str | None, str | None]:
    """Combined map bias + viewbox/photon bbox for one or more districts.

    Resolves each district under its own state so a TG district mixed into an
    AP-scoped list still contributes its real center (avoids falling back to
    whole-state Photons that jump My location / search into the wrong state).
    """
    centers: list[tuple[float, float]] = []
    for did in district_ids:
        if not did:
            continue
        sk = _state_key_for_district_id(did) or state_key
        if not sk:
            continue
        dist = get_district(did, sk)
        c = (dist or {}).get("center")
        if c and c[0] is not None and c[1] is not None:
            centers.append((float(c[0]), float(c[1])))

    if not centers:
        if state_key and state_key in STATE_BOUNDS:
            sw, ne = STATE_BOUNDS[state_key]
            viewbox = f"{sw[1]},{ne[0]},{ne[1]},{sw[0]}"
            photon_bbox = f"{sw[1]},{sw[0]},{ne[1]},{ne[0]}"
            c = STATE_CENTER.get(state_key)
            bias = (float(c[0]), float(c[1])) if c else (None, None)
            return bias[0], bias[1], viewbox, photon_bbox
        return None, None, None, None

    min_lat = min(c[0] for c in centers) - pad
    max_lat = max(c[0] for c in centers) + pad
    min_lon = min(c[1] for c in centers) - pad
    max_lon = max(c[1] for c in centers) + pad
    bias_lat = sum(c[0] for c in centers) / len(centers)
    bias_lon = sum(c[1] for c in centers) / len(centers)
    viewbox = f"{min_lon},{max_lat},{max_lon},{min_lat}"
    photon_bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    return bias_lat, bias_lon, viewbox, photon_bbox


def _is_generic_road_name(name: str | None) -> bool:
    """True for useless labels like 'Road', 'Road Number 10', 'Unnamed'."""
    n = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if not n or n in ("road", "street", "lane", "path"):
        return True
    if re.match(r"^(unnamed|unknown)\b", n):
        return True
    if re.match(r"^road\s*(number|no\.?|#)?\s*\d+$", n):
        return True
    if re.match(r"^(nh|sh|mdr)\s*-?\s*\d+$", n):
        # Bare highway refs alone are weak as a place label
        return True
    return False


def _reverse_label_for_point(
    lat: float,
    lon: float,
    *,
    district_ids: list | set | None = None,
    state_keys: list[str] | None = None,
    allow_nominatim: bool = True,
    nominatim_timeout: float = 2.0,
) -> dict:
    """Human label for a lat/lon: place/neighbourhood first, then a specific road.

    Skips generic GIS labels like ``Road Number 10`` when Nominatim has a suburb /
    colony / named street nearby.
    """
    local = reverse_geocode_local(
        lat, lon, district_ids=district_ids, state_keys=state_keys,
    )
    place = neighbourhood = road_nom = ""
    addr: dict = {}
    full = ""
    if allow_nominatim:
        params = {
            "lat": str(lat),
            "lon": str(lon),
            "format": "json",
            "zoom": "18",
            "addressdetails": "1",
        }
        url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)
        try:
            r = _nominatim_get(url, timeout=nominatim_timeout)
            if isinstance(r, dict):
                addr = r.get("address") or {}
                full = str(r.get("display_name") or "")
                road_nom = (
                    addr.get("road") or addr.get("pedestrian")
                    or addr.get("residential") or ""
                )
                neighbourhood = (
                    addr.get("neighbourhood") or addr.get("quarter")
                    or addr.get("hamlet") or addr.get("residential") or ""
                )
                # Prefer fine place names over huge admin units (GHMC / Hyderabad city).
                place = (
                    addr.get("suburb") or addr.get("village") or addr.get("town")
                    or addr.get("city_district") or ""
                )
                # "Ward 107 Madhapur" sometimes only appears in display_name.
                if not place and full:
                    m = re.search(r"\bWard\s+\d+\s+([A-Za-z][A-Za-z .'-]{2,40})\b", full)
                    if m:
                        place = m.group(1).strip()
        except Exception:
            pass

    local_road = (local or {}).get("road") or ""
    # Prefer a named Nominatim street over generic GIS refs (Road Number N).
    if road_nom and not _is_generic_road_name(road_nom):
        road = road_nom
    elif local_road and not _is_generic_road_name(local_road):
        road = local_road
    elif road_nom:
        road = road_nom
    else:
        road = "" if _is_generic_road_name(local_road) else local_road

    # Place-first labels — avoid "HITEC City · Road Number 10".
    if neighbourhood and place and neighbourhood.lower() != place.lower():
        short = f"{neighbourhood} · {place}"
        source = "nominatim"
    elif neighbourhood and road and not _is_generic_road_name(road):
        short = f"{neighbourhood} · {road}"
        source = "nominatim"
    elif place and road and not _is_generic_road_name(road):
        short = f"{place} · {road}"
        source = "road_index+nominatim" if local else "nominatim"
    elif neighbourhood:
        short = neighbourhood
        source = "nominatim"
    elif place:
        short = place
        source = "nominatim"
    elif road and not _is_generic_road_name(road):
        dname = (local or {}).get("district_name")
        short = f"{road} · {dname}" if dname else road
        source = "road_index+nominatim" if local else "nominatim"
    elif local:
        # Last resort: may still be "Road Number 10 · Ranga Reddy"
        short = local.get("display_name") or f"{lat:.5f}, {lon:.5f}"
        source = local.get("source") or "road_index"
        road = local_road or road
    else:
        short = f"{lat:.5f}, {lon:.5f}"
        source = "coords"

    out = {
        "display_name": short,
        "full_display_name": full or short,
        "lat": lat,
        "lon": lon,
        "road": road or None,
        "place": place or neighbourhood or None,
        "source": source,
        "address": addr or (local or {}).get("address") or {},
    }
    if local:
        out.setdefault("district_id", local.get("district_id"))
        out.setdefault("district_name", local.get("district_name"))
        out.setdefault("state_key", local.get("state_key"))
        out.setdefault("state_id", local.get("state_id"))
        if local.get("distance_km") is not None:
            out["distance_km"] = local.get("distance_km")
    return out


def _geocode_coordinate_hit(
    lat: float,
    lon: float,
    *,
    state_key: str | None = None,
    district_ids: list | None = None,
    state_keys: list[str] | None = None,
) -> dict:
    """Resolve pasted/GPS coordinates to a named place + district (fast path)."""
    keys = [k for k in (state_keys or []) if k in STATE_ID_BY_KEY]
    if state_key and state_key in STATE_ID_BY_KEY and state_key not in keys:
        keys.insert(0, state_key)
    allowed = []
    for d in (district_ids or []):
        if d is None or not str(d).strip():
            continue
        s = str(d)
        if s not in allowed:
            allowed.append(s)

    located = None
    if allowed:
        located = locate_point_in_allowed_districts_fast(lat, lon, allowed)
    if not located:
        locate_keys = keys or ([state_key] if state_key else list(STATE_ID_BY_KEY.keys()))
        located = locate_point_any_state(lat, lon, state_keys=locate_keys)

    rev = _reverse_label_for_point(
        lat, lon,
        district_ids=allowed or None,
        state_keys=keys or ([located.get("state_key")] if located and located.get("state_key") else None),
        allow_nominatim=True,
        nominatim_timeout=2.0,
    )

    row = {
        "display_name": rev.get("display_name") or f"{lat:.5f}, {lon:.5f}",
        "full_display_name": rev.get("full_display_name"),
        "lat": lat,
        "lon": lon,
        "type": "coordinate",
        "class": "place",
        "source": "coords",
        "reverse_source": rev.get("source"),
    }
    if located:
        row["district_id"] = located.get("district_id")
        row["district_name"] = located.get("district_name")
        row["state_key"] = located.get("state_key")
        row["state_id"] = located.get("state_id")
    else:
        if rev.get("district_id"):
            row["district_id"] = rev.get("district_id")
            row["district_name"] = rev.get("district_name")
            row["state_key"] = rev.get("state_key")
            row["state_id"] = rev.get("state_id")
    return row


def geocode_search(
    query: str,
    *,
    state_key: str | None = None,
    district_id: str | int | None = None,
    district_ids: list | None = None,
    limit: int = 8,
    state_keys: list[str] | None = None,
) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    # Lat/lon must run before multi-state recursion — otherwise each state re-parses
    # the same coords and cold-builds snap indexes twice (felt like 10–20s).
    coords = _parse_lat_lon_query(q)
    if coords:
        lat, lon = coords
        search_dids = []
        for d in (district_ids or []):
            if d is not None and str(d).strip() and str(d) not in search_dids:
                search_dids.append(str(d))
        if district_id is not None and str(district_id).strip() and str(district_id) not in search_dids:
            search_dids.insert(0, str(district_id))
        return [_geocode_coordinate_hit(
            lat, lon,
            state_key=state_key,
            district_ids=search_dids or None,
            state_keys=state_keys,
        )]

    keys = [k for k in (state_keys or []) if k in STATE_ID_BY_KEY]
    if state_key and state_key in STATE_ID_BY_KEY and state_key not in keys:
        keys.insert(0, state_key)
    # Single state_keys entry → treat as state_key so local district search runs
    if not state_key and len(keys) == 1:
        state_key = keys[0]
        keys = []
    # Multi-state: search each state but KEEP district filter (local road index first).
    if len(keys) > 1:
        out: list[dict] = []
        seen: set[tuple] = set()
        per = max(3, (limit + len(keys) - 1) // len(keys))
        # Prefer districts belonging to each state when district_ids provided
        dids_by_state: dict[str, list[str]] = {sk: [] for sk in keys}
        for d in (district_ids or []):
            if d is None or not str(d).strip():
                continue
            sks = state_keys_for_district_ids([d])
            for sk in sks:
                if sk in dids_by_state and str(d) not in dids_by_state[sk]:
                    dids_by_state[sk].append(str(d))
        for sk in keys:
            sk_dids = dids_by_state.get(sk) or None
            for r in geocode_search(
                q,
                state_key=sk,
                district_id=None,
                district_ids=sk_dids,
                limit=per,
            ):
                key = (round(float(r["lat"]), 5), round(float(r["lon"]), 5), r.get("display_name"))
                if key in seen:
                    continue
                seen.add(key)
                row = dict(r)
                row.setdefault("state_key", sk)
                out.append(row)
                if len(out) >= limit:
                    return out[:limit]
        return out[:limit]

    out: list[dict] = []
    seen: set[tuple] = set()
    variants = _geocode_query_variants(q)
    prefer_places = _query_prefers_places(q)

    def _add(rows: list[dict]):
        for r in rows:
            key = (round(float(r["lat"]), 5), round(float(r["lon"]), 5), r.get("display_name"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)

    search_districts: list[str] = []
    for d in (district_ids or []):
        if d is not None and str(d).strip() and str(d) not in search_districts:
            search_districts.append(str(d))
    if district_id is not None and str(district_id).strip() and str(district_id) not in search_districts:
        search_districts.insert(0, str(district_id))

    cache_key = "|".join([
        q.lower(),
        state_key or "",
        ",".join(search_districts),
        str(limit),
        "p" if prefer_places else "r",
    ])
    cached = _GEOCODE_RESULT_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _GEOCODE_RESULT_CACHE_TTL:
        return [dict(r) for r in cached[1][:limit]]

    road_hits: list[dict] = []

    def _add_roads(rows: list[dict]):
        for r in rows:
            key = (round(float(r["lat"]), 5), round(float(r["lon"]), 5), r.get("display_name"))
            if key in seen:
                continue
            seen.add(key)
            road_hits.append(r)

    # Local road index only for highway/road queries. Place searches go straight
    # to OSM geocoders so colleges/landmarks are not crowded out by road names.
    road_cap = limit
    if (not prefer_places) and state_key and search_districts:
        for vq in variants:
            for did in search_districts:
                _add_roads(search_roads_in_district(
                    vq, state_key=state_key, district_id=did, limit=road_cap,
                ))
                if len(road_hits) >= road_cap:
                    break
            if len(road_hits) >= road_cap:
                break

    # Sibling districts only for explicit single-district road searches with no hits
    if (
        not prefer_places
        and state_key
        and district_id
        and len(road_hits) == 0
        and not district_ids
    ):
        sel = get_district(district_id, state_key)
        c0 = (sel or {}).get("center")
        for dist in list_districts(state_key):
            did = str(dist["district_id"])
            if did == str(district_id):
                continue
            c1 = dist.get("center")
            if c0 and c1:
                if _haversine_km(float(c0[0]), float(c0[1]), float(c1[0]), float(c1[1])) > 40:
                    continue
            for vq in variants[:2]:
                _add_roads(search_roads_in_district(vq, state_key=state_key, district_id=did, limit=4))
            if len(road_hits) >= limit:
                break

    # Highway-style queries with enough road hits can skip remote geocoders
    if not prefer_places and len(road_hits) >= min(3, limit):
        _GEOCODE_RESULT_CACHE[cache_key] = (time.time(), list(road_hits[:limit]))
        return road_hits[:limit]

    primary_did = search_districts[0] if search_districts else district_id
    dist = get_district(primary_did, state_key) if (state_key and primary_did) else None
    state_label = STATE_BY_ID[STATE_ID_BY_KEY[state_key]]["label"] if state_key and state_key in STATE_ID_BY_KEY else ""
    enrich_districts: list[dict] = []
    seen_enrich: set[str] = set()
    if state_key and search_districts:
        for did in search_districts:
            drow = get_district(did, state_key)
            did_s = str((drow or {}).get("district_id") or did)
            if drow and did_s not in seen_enrich:
                seen_enrich.add(did_s)
                enrich_districts.append(drow)
    elif dist:
        enrich_districts = [dist]

    queries = _build_geocode_remote_queries(variants, enrich_districts, state_label)

    pad = 0.85 if prefer_places else 0.55
    bias_lat, bias_lon, viewbox, photon_bbox = _bbox_for_districts(
        search_districts, state_key, pad=pad,
    )

    place_hits: list[dict] = []

    def _add_places(rows: list[dict]):
        for r in rows:
            key = (round(float(r["lat"]), 5), round(float(r["lon"]), 5), r.get("display_name"))
            if key in seen:
                continue
            seen.add(key)
            place_hits.append(r)

    def _filter_place_row(cls: str, typ: str) -> bool:
        if prefer_places and cls in ("highway", "route") and typ not in ("bus_stop",):
            return False
        return True

    # Photon first (fast OSM autocomplete), then Nominatim per query until a good match.
    use_photon = GEOCODE_PROVIDER != "nominatim"
    remote_budget = 2 if prefer_places else 1
    if prefer_places and len(search_districts) > 1:
        remote_budget = min(len(queries), max(3, len(search_districts) + 1))
    for nq in queries[:remote_budget]:
        if prefer_places and len(place_hits) >= limit:
            break
        if not prefer_places and (len(place_hits) + len(road_hits)) >= limit:
            break
        if prefer_places and _geocode_rows_match_query(place_hits, q):
            break

        if use_photon:
            try:
                photon_rows = _photon_search(
                    nq,
                    limit=max(5, limit),
                    lat=bias_lat,
                    lon=bias_lon,
                    bbox=photon_bbox,
                    timeout=3.0,
                )
            except Exception:
                photon_rows = []
            nom = []
            for r in photon_rows:
                cls = str(r.get("class") or "")
                typ = str(r.get("type") or "")
                if not _filter_place_row(cls, typ):
                    continue
                row = dict(r)
                row["state_key"] = state_key
                nom.append(row)
            _add_places(nom)
            if _geocode_rows_match_query(place_hits, q):
                break

        params = {
            "q": nq,
            "format": "json",
            "limit": str(max(5, limit)),
            "addressdetails": "1",
            "countrycodes": "in",
        }
        if viewbox:
            params["viewbox"] = viewbox
            params["bounded"] = "0"
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
        try:
            rows = _nominatim_get(url, timeout=4.0)
        except Exception:
            rows = []
        nom = []
        for r in rows or []:
            try:
                cls = str(r.get("class") or "")
                typ = str(r.get("type") or "")
                if not _filter_place_row(cls, typ):
                    continue
                nom.append({
                    "display_name": r.get("display_name"),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "type": typ,
                    "class": cls or "place",
                    "source": "nominatim",
                    "state_key": state_key,
                })
            except (KeyError, TypeError, ValueError):
                continue
        _add_places(nom)
        if _geocode_rows_match_query(place_hits, q):
            break

    if prefer_places:
        q_low = q.lower()
        q_tokens = [t for t in re.split(r"[^a-z0-9]+", q_low) if len(t) > 2]
        prefix = q_low[: max(3, min(len(q_low), 6))]

        def _place_rank(row: dict) -> tuple:
            name = str(row.get("display_name") or "").lower()
            primary = name.split(",")[0].strip()
            hits = sum(1 for t in q_tokens if t in name)
            primary_exact = primary == q_low
            primary_prefix = bool(prefix and primary.startswith(prefix))
            return (
                0 if primary_exact else (1 if primary_prefix else 2),
                -hits,
                0 if row.get("source") == "photon" else 1,
            )

        place_hits.sort(key=_place_rank)
        merged = place_hits + road_hits
    else:
        merged = road_hits + place_hits

    # Place search with no remote hits — try local road index across all assigned districts.
    if prefer_places and not merged and state_key and search_districts:
        for vq in variants[:2]:
            for did in search_districts:
                _add_roads(search_roads_in_district(
                    vq, state_key=state_key, district_id=did, limit=road_cap,
                ))
                if len(road_hits) >= limit:
                    break
            if len(road_hits) >= limit:
                break
        merged = road_hits[:limit]

    result = _dedupe_geocode_results(merged)[:limit]
    _GEOCODE_RESULT_CACHE[cache_key] = (time.time(), list(result))
    if len(_GEOCODE_RESULT_CACHE) > 300:
        oldest = sorted(_GEOCODE_RESULT_CACHE.items(), key=lambda kv: kv[1][0])[:60]
        for k, _ in oldest:
            _GEOCODE_RESULT_CACHE.pop(k, None)
    return result


def reverse_geocode_local(
    lat: float,
    lon: float,
    *,
    district_ids: list | set | None = None,
    state_keys: list[str] | None = None,
    max_km: float = 0.45,
) -> dict | None:
    """Nearest road name from district GIS — fast, no Nominatim."""
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    scopes: list[tuple[str, str]] = []
    allowed = [str(x) for x in (district_ids or []) if x is not None and str(x).strip()]
    if allowed:
        for did in allowed:
            for sk in state_keys_for_district_ids([did]) or []:
                scopes.append((sk, did))
    else:
        keys = [k for k in (state_keys or list(STATE_ID_BY_KEY.keys())) if k in STATE_ID_BY_KEY]
        # Prefer districts whose center is nearby, then snap to roads
        ranked: list[tuple[float, str, str]] = []
        for sk in keys:
            for dist in list_districts(sk):
                c = dist.get("center") or [None, None]
                if c[0] is None:
                    continue
                d = _haversine_km(lat_f, lon_f, float(c[0]), float(c[1]))
                if d <= 45:
                    ranked.append((d, sk, str(dist["district_id"])))
        ranked.sort(key=lambda t: t[0])
        scopes = [(sk, did) for _, sk, did in ranked[:6]]

    best = None
    best_d = max_km
    for sk, did in scopes:
        hit = _nearest_snap_in_district(lat_f, lon_f, sk, did, max_km=best_d)
        if not hit:
            continue
        _ml, _mo, name, ref, dname_prop, d = hit
        dist = get_district(did, sk) or {}
        best_d = d
        bits = [x for x in (ref, name) if x]
        road = " — ".join(bits) if bits else "Road"
        dname = dist.get("name") or dname_prop or did
        best = {
            "display_name": f"{road} · {dname}",
            "lat": lat_f,
            "lon": lon_f,
            "road": road,
            "district_id": did,
            "district_name": dname,
            "state_key": sk,
            "state_id": STATE_ID_BY_KEY.get(sk),
            "distance_km": round(d, 3),
            "source": "road_index",
            "address": {
                "road": name or ref or "",
                "county": dname,
                "state": STATE_BY_ID.get(STATE_ID_BY_KEY.get(sk, -1), {}).get("label") or "",
            },
        }
        if d < 0.08:
            return best
    return best


def reverse_geocode(
    lat: float,
    lon: float,
    *,
    district_ids: list | set | None = None,
    state_keys: list[str] | None = None,
    allow_nominatim: bool = True,
    prefer_places: bool = False,
) -> dict:
    """Reverse geocode a point.

    Default: prefer local road snap; Nominatim as fallback.
    ``prefer_places=True``: skip GIS road labels (``Road · District``) and use
    Nominatim place names first (suburb / village / town).
    """
    local = None
    if not prefer_places:
        local = reverse_geocode_local(
            lat, lon, district_ids=district_ids, state_keys=state_keys,
        )
        if local and float(local.get("distance_km") or 99) <= 0.35:
            return local

    if not allow_nominatim:
        if local:
            return local
        return {"display_name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "source": "coords"}

    params = {
        "lat": str(lat),
        "lon": str(lon),
        "format": "json",
        "zoom": "16" if prefer_places else "16",
        "addressdetails": "1",
    }
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)
    try:
        r = _nominatim_get(url, timeout=4.0)
    except Exception:
        if local:
            return local
        return {"display_name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "source": "coords"}
    if not isinstance(r, dict):
        if local:
            return local
        return {"display_name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "source": "coords"}
    addr = r.get("address") or {}
    road = addr.get("road") or addr.get("pedestrian") or addr.get("neighbourhood") or ""
    place = (
        addr.get("suburb") or addr.get("village") or addr.get("town")
        or addr.get("city") or addr.get("county") or ""
    )
    display = r.get("display_name") or ""
    if prefer_places:
        # Place-first labels for Detection route display
        if place and road:
            short = f"{place} · {road}"
        elif place:
            short = place
        elif road:
            short = road
        else:
            short = display or f"{lat:.5f}, {lon:.5f}"
    elif road and place:
        short = f"{road} · {place}"
    elif road:
        short = road
    elif place:
        short = place
    else:
        short = display or f"{lat:.5f}, {lon:.5f}"
    out = {
        "display_name": short,
        "full_display_name": display or short,
        "lat": lat,
        "lon": lon,
        "address": addr,
        "source": "nominatim",
    }
    if local is None and not prefer_places:
        local = reverse_geocode_local(
            lat, lon, district_ids=district_ids, state_keys=state_keys,
        )
    # Enrich with our district when possible
    if local:
        out.setdefault("district_id", local.get("district_id"))
        out.setdefault("district_name", local.get("district_name"))
        out.setdefault("state_key", local.get("state_key"))
        if (not prefer_places) and local.get("road") and not road:
            out["display_name"] = local["display_name"]
            out["source"] = "road_index+nominatim"
    return out


