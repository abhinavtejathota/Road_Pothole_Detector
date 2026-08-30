"""survey.geometry — extends geocode (includes private _names)."""
from __future__ import annotations

import routes.survey.geocode as _geocode

globals().update({k: v for k, v in vars(_geocode).items() if not k.startswith('__')})

# ── Corridor geometry helpers ─────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_to_segment_km(lat, lon, a_lat, a_lon, b_lat, b_lon) -> float:
    """Approximate distance from point to segment AB in km (equirectangular)."""
    # Local projection metres around mid-lat
    mid_lat = (a_lat + b_lat + lat) / 3.0
    kx = 111.32 * math.cos(math.radians(mid_lat))
    ky = 110.57
    ax, ay = a_lon * kx, a_lat * ky
    bx, by = b_lon * kx, b_lat * ky
    px, py = lon * kx, lat * ky
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 <= 1e-12:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)


def _geom_midpoint(geom: dict) -> tuple[float, float] | None:
    if not geom:
        return None
    t = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if t == "LineString":
        pts = coords
    elif t == "MultiLineString":
        pts = [p for line in coords for p in line]
    else:
        return None
    if not pts:
        return None
    mid = pts[len(pts) // 2]
    return float(mid[1]), float(mid[0])  # lat, lon


# Endpoint rounding for topology joins after district clipping (~1 m)
_NODE_PREC = 5
_UNAVAILABLE = frozenset({"approved", "pending_approval", "assigned"})
# Virtual edges across tiny OSM/clip gaps (not real survey segments — walk-only)
_GAP_BRIDGE_SID = "__gap_bridge__"
_GAP_BRIDGE_KM = 0.075  # 75 m — typical clip / digitizing disconnect


def _node_key(lon: float, lat: float) -> tuple[float, float]:
    return (round(float(lon), _NODE_PREC), round(float(lat), _NODE_PREC))


def _add_endpoint_gap_bridges(
    adj: dict[tuple[float, float], list[tuple[tuple[float, float], str]]],
    *,
    max_km: float = _GAP_BRIDGE_KM,
) -> int:
    """Link nearby endpoints that OSM/clipping left disconnected.

    Adds zero-length virtual edges (``_GAP_BRIDGE_SID``) so corridor / nearest
    walks can cross ~50–80 m gaps between NH/SH/MDR/local pieces. These are not
    assigned as survey segments and add no km.
    """
    if max_km <= 0 or len(adj) < 2:
        return 0
    nodes = list(adj.keys())
    # ~max_km cells in lon/lat degrees (lon shrinks near poles; India ~OK)
    cell = max(max_km / 111.0, 1e-5)
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for n in nodes:
        lon, lat = float(n[0]), float(n[1])
        buckets.setdefault((int(lon / cell), int(lat / cell)), []).append(n)

    # Existing neighbor sets for O(1) skip
    existing: dict[tuple[float, float], set] = {
        n: {other for other, _sid in nbrs} for n, nbrs in adj.items()
    }
    added = 0
    seen_pair: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for (cx, cy), group in buckets.items():
        candidates: list[tuple[float, float]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(buckets.get((cx + dx, cy + dy), ()))
        if len(candidates) < 2:
            continue
        for i, a in enumerate(group):
            for b in candidates:
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                if pair in seen_pair:
                    continue
                seen_pair.add(pair)
                if b in existing.get(a, ()):
                    continue
                # node key is (lon, lat)
                d = _haversine_km(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
                if d < 0.002 or d > max_km:
                    continue
                adj.setdefault(a, []).append((b, _GAP_BRIDGE_SID))
                adj.setdefault(b, []).append((a, _GAP_BRIDGE_SID))
                existing.setdefault(a, set()).add(b)
                existing.setdefault(b, set()).add(a)
                added += 1
    return added


def _line_coord_parts(geom: dict | None) -> list[list]:
    """Return LineString coordinate lists (MultiLineString → one list per part)."""
    if not geom:
        return []
    t = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return []
    if t == "LineString":
        return [coords] if len(coords) >= 2 else []
    if t == "MultiLineString":
        return [line for line in coords if line and len(line) >= 2]
    return []


def _class_traverse_cost(rc: str, focus_set: set[str] | None) -> float:
    """Soft class preference: focus roads cheapest; others allowed as connectors."""
    order = ROAD_CLASS_ORDER.get(rc, 3)
    if focus_set is None:
        return 1.0 + 0.08 * order
    if rc in focus_set:
        return 1.0 + 0.04 * order
    # Bridge gaps with SH/MDR/local when preferred class is clipped/disconnected
    return 1.55 + 0.12 * order


def _pick_continuous_corridor(
    state_key: str,
    district_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    corridor_km: float = 2.5,
    focus: str = "all",
    target_km: float = 100.0,
    status_map: dict | None = None,
    extra_district_ids: list | set | None = None,
    allow_reverse: bool = True,
    blocked_sids: set[str] | None = None,
    penalized_sids: dict[str, float] | None = None,
) -> tuple[list[str], float, dict]:
    """Grow a single connected walk along the corridor up to the destination.

    Stops when the walk reaches the end point (not when filling a daily km quota).
    blocked_sids / penalized_sids are used to produce alternate route options.
    """
    if status_map is None:
        status_map = _load_state().get("segment_status", {})
    s_lat, s_lon = start
    e_lat, e_lon = end
    focus_set = parse_focus_classes(focus)
    district_id = str(district_id)
    district_ids = [district_id]
    for did in (extra_district_ids or []):
        d = str(did)
        if d and d not in district_ids:
            district_ids.append(d)
    meta_all: dict[str, dict] = {}
    for did in district_ids:
        meta_all.update(_segment_meta(state_key, did))
    blocked = set(blocked_sids or ())
    penalties = dict(penalized_sids or {})

    # Progressive corridor widths — cheap first, widen only if empty
    widths = [corridor_km]
    for w in (4.0, 6.5, 10.0, 14.0):
        if w > corridor_km + 0.1:
            widths.append(w)

    picked: list[str] = []
    total = 0.0
    preferred_km = 0.0
    connector_km = 0.0
    start_node = None
    used_width = corridor_km
    best = None  # (picked, total, preferred_km, connector_km, start_node, width, end_prox)

    for width in widths:
        # Bbox prefilter around corridor (degrees ≈ km/111)
        pad = (width + 1.0) / 100.0
        min_lat = min(s_lat, e_lat) - pad
        max_lat = max(s_lat, e_lat) + pad
        min_lon = min(s_lon, e_lon) - pad
        max_lon = max(s_lon, e_lon) + pad

        segments: dict[str, dict] = {}
        adj: dict[tuple[float, float], list[tuple[tuple[float, float], str]]] = {}
        sid_nodes: dict[str, set[tuple[float, float]]] = {}

        for sid, m in meta_all.items():
            if sid in blocked:
                continue
            if status_map.get(sid) in _UNAVAILABLE:
                continue
            ml, mo = m["mid"]
            if ml < min_lat or ml > max_lat or mo < min_lon or mo > max_lon:
                continue
            dist = _point_to_segment_km(ml, mo, s_lat, s_lon, e_lat, e_lon)
            if dist > width:
                continue
            rc = m["road_class"]
            segments[sid] = {
                "length": m["length"],
                "road_class": rc,
                "cost": _class_traverse_cost(rc, focus_set),
                "preferred": focus_set is None or rc in focus_set,
                "corridor_dist": dist,
                "penalty": float(penalties.get(sid) or 0),
            }
            nodes_for_sid = sid_nodes.setdefault(sid, set())
            for u, v in m["ends"]:
                adj.setdefault(u, []).append((v, sid))
                adj.setdefault(v, []).append((u, sid))
                nodes_for_sid.add(u)
                nodes_for_sid.add(v)

        if not segments or not adj:
            continue

        # Soft-join near endpoints across district boundary clips (~40 m)
        if len(district_ids) > 1:
            parent = {n: n for n in adj}

            def _find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def _union(a, b):
                ra, rb = _find(a), _find(b)
                if ra != rb:
                    parent[rb] = ra

            cell: dict[tuple[int, int], list] = {}
            for n in list(adj.keys()):
                # ~80–100 m soft join across district clips
                key = (round(n[0] / 0.0008), round(n[1] / 0.0008))
                cell.setdefault(key, []).append(n)
            for nodes_in_cell in cell.values():
                if len(nodes_in_cell) < 2:
                    continue
                base = nodes_in_cell[0]
                for other in nodes_in_cell[1:]:
                    _union(base, other)
            if any(parent[n] != n for n in parent):
                new_adj: dict[tuple[float, float], list[tuple[tuple[float, float], str]]] = {}
                new_sid_nodes: dict[str, set[tuple[float, float]]] = {}
                for sid, nodes in sid_nodes.items():
                    new_sid_nodes[sid] = {_find(n) for n in nodes}
                for u, nbrs in adj.items():
                    ru = _find(u)
                    for v, sid in nbrs:
                        rv = _find(v)
                        if ru == rv:
                            continue
                        new_adj.setdefault(ru, []).append((rv, sid))
                        new_adj.setdefault(rv, []).append((ru, sid))
                adj, sid_nodes = new_adj, new_sid_nodes

        # Bridge tiny digitizing / clip gaps so NH↔SH↔MDR↔local walks stay continuous
        _add_endpoint_gap_bridges(adj, max_km=_GAP_BRIDGE_KM)

        def _node_snap_score(node: tuple[float, float]) -> tuple:
            d_start = _haversine_km(s_lat, s_lon, node[1], node[0])
            d_end = _haversine_km(e_lat, e_lon, node[1], node[0])
            degree = len([1 for _, sid in adj.get(node, []) if sid != _GAP_BRIDGE_SID])
            has_pref = any(
                segments[sid]["preferred"]
                for _, sid in adj.get(node, [])
                if sid in segments
            )
            # Prefer nodes near start that head toward the end and are well connected
            # (avoids snapping onto tiny isolated islands)
            return (
                0 if d_start <= 1.2 else 1,
                0 if has_pref else 1,
                -degree,
                d_end,
                d_start,
            )

        start_node = min(adj.keys(), key=_node_snap_score)
        used: set[str] = set()
        used_bridges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        picked = []
        total = 0.0
        preferred_km = 0.0
        connector_km = 0.0
        bridges_used = 0
        frontier: set[tuple[float, float]] = {start_node}
        used_width = width

        def _edge_score(from_node: tuple[float, float], sid: str, other: tuple[float, float]) -> tuple:
            d_end = _haversine_km(other[1], other[0], e_lat, e_lon)
            d_cur = _haversine_km(from_node[1], from_node[0], e_lat, e_lon)
            progress = d_cur - d_end
            if sid == _GAP_BRIDGE_SID:
                # Prefer bridges that advance toward the destination; never preferred over real roads
                return (2, 1.2 - 5.0 * progress, 0.0, 0.0)
            meta = segments[sid]
            penalty = float(meta.get("penalty") or 0)
            return (
                0 if meta["preferred"] else 1,
                meta["cost"] * meta["length"] - 4.0 * progress + penalty,
                meta["corridor_dist"],
                -meta["length"],
            )

        # Grow toward destination — stop when we reach the end (do NOT pad to daily km)
        while total < target_km:
            # Already close enough to destination?
            if min((_haversine_km(e_lat, e_lon, n[1], n[0]) for n in frontier), default=999) <= 0.55:
                break
            options: list[tuple[tuple[float, float], tuple[float, float], str]] = []
            for node in frontier:
                for other, sid in adj.get(node, []):
                    if sid == _GAP_BRIDGE_SID:
                        pair = (node, other) if node < other else (other, node)
                        if pair in used_bridges or other in frontier:
                            continue
                        options.append((node, other, sid))
                        continue
                    if sid in used or sid not in segments:
                        continue
                    options.append((node, other, sid))
            if not options:
                break
            from_node, other, sid = min(options, key=lambda t: _edge_score(t[0], t[2], t[1]))
            if sid == _GAP_BRIDGE_SID:
                pair = (from_node, other) if from_node < other else (other, from_node)
                used_bridges.add(pair)
                frontier.add(other)
                bridges_used += 1
                if _haversine_km(other[1], other[0], e_lat, e_lon) <= 0.55:
                    break
                continue
            used.add(sid)
            picked.append(sid)
            length = segments[sid]["length"]
            total += length
            if segments[sid]["preferred"]:
                preferred_km += length
            else:
                connector_km += length
            frontier.update(sid_nodes.get(sid) or (from_node, other))
            # Reached end via this edge tip
            if _haversine_km(other[1], other[0], e_lat, e_lon) <= 0.55:
                break

        if picked:
            # Drive order: DFS from start (real segments only; bridges are walk glue)
            picked_set = set(picked)
            ordered: list[str] = []
            seen_sid: set[str] = set()
            stack = [start_node]
            visited_nodes: set[tuple[float, float]] = set()
            while stack:
                node = stack.pop()
                if node in visited_nodes:
                    continue
                visited_nodes.add(node)
                nbrs = sorted(
                    (
                        (other, sid)
                        for other, sid in adj.get(node, [])
                        if sid in picked_set or sid == _GAP_BRIDGE_SID
                    ),
                    key=lambda t: (
                        0 if t[1] != _GAP_BRIDGE_SID and segments.get(t[1], {}).get("preferred") else 1,
                        0 if t[1] != _GAP_BRIDGE_SID else 1,
                        -float(segments.get(t[1], {}).get("length") or 0),
                    ),
                )
                for other, sid in nbrs:
                    if sid != _GAP_BRIDGE_SID and sid not in seen_sid:
                        seen_sid.add(sid)
                        ordered.append(sid)
                    if other not in visited_nodes:
                        stack.append(other)
            for sid in picked:
                if sid not in seen_sid:
                    ordered.append(sid)
            picked = ordered

            # Distance from grown frontier to destination
            end_prox = min(
                (_haversine_km(e_lat, e_lon, n[1], n[0]) for n in frontier),
                default=999.0,
            )
            # Keep best across widths; only stop early when we actually reach the end
            candidate = (picked, total, preferred_km, connector_km, start_node, width, end_prox, bridges_used)
            if end_prox < 1.25 and total >= min(1.0, target_km * 0.05):
                best = candidate
                break
            if best is None or (end_prox, -total) < (best[6], -best[1]):
                best = candidate
            # Narrow band found a dead-end island — widen and try again
            continue

    bridges_used = 0
    if best is not None:
        if len(best) >= 8:
            picked, total, preferred_km, connector_km, start_node, used_width, end_prox, bridges_used = best
        else:
            picked, total, preferred_km, connector_km, start_node, used_width, end_prox = best
    else:
        end_prox = 999.0

    # If start snapped onto an island, grow from the destination back toward start
    if allow_reverse and (not picked or end_prox > 1.5 or total < 1.0):
        rev_picked, rev_total, rev_meta = _pick_continuous_corridor(
            state_key,
            district_id,
            end,
            start,
            corridor_km=corridor_km,
            focus=focus,
            target_km=target_km,
            status_map=status_map,
            extra_district_ids=extra_district_ids,
            allow_reverse=False,
            blocked_sids=blocked,
            penalized_sids=penalties,
        )
        rev_better = False
        if rev_picked:
            if not picked:
                rev_better = True
            elif rev_total > total * 1.5 or (
                rev_meta.get("district_ids_used") and len(rev_meta.get("district_ids_used") or []) > len(
                    {str((meta_all.get(s) or {}).get("district_id") or district_id) for s in picked}
                )
            ):
                rev_better = True
            elif rev_total > total and end_prox > 1.5:
                rev_better = True
        if rev_better:
            # Reverse drive order so assignment still reads start → end
            rev_picked = list(reversed(rev_picked))
            return rev_picked, rev_total, rev_meta

    if not picked or start_node is None:
        return [], 0.0, {"connector_km": 0.0, "preferred_km": 0.0, "continuous": True, "gap_bridges_used": 0}

    return picked, total, {
        "connector_km": round(connector_km, 2),
        "preferred_km": round(preferred_km, 2),
        "continuous": True,
        "corridor_km_used": used_width,
        "gap_bridges_used": int(bridges_used),
        "start_snap_km": round(_haversine_km(s_lat, s_lon, start_node[1], start_node[0]), 3),
        "district_ids_used": sorted({
            str((meta_all.get(s) or {}).get("district_id") or district_id) for s in picked
        }),
        "sid_district": {
            s: str((meta_all.get(s) or {}).get("district_id") or district_id) for s in picked
        },
        "end_prox_km": round(end_prox, 3) if end_prox < 900 else None,
        "reversed": False,
    }


