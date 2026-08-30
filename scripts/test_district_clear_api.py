"""Edge cases for VG district picker + clear survey (mobile_app flows)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_app import create_app  # noqa: E402


def check(results, name, resp, ok_codes=(200,)):
    code = resp.status_code
    body = resp.get_json(silent=True) or {}
    ok = code in ok_codes
    err = ""
    if isinstance(body, dict):
        err = str(body.get("error") or body.get("message") or "")[:140]
    results.append((ok, name, code, err))
    print(("OK  " if ok else "FAIL"), name, "->", code, err or "")
    return body


def main() -> int:
    app = create_app()
    c = app.test_client()
    results = []

    # Unauthenticated
    check(results, "PATCH my-districts anon", c.patch("/api/survey/my-districts", json={"district_ids": [503]}), (401, 403))
    check(results, "POST clear anon", c.post("/api/survey/assignments/clear", json={}), (401, 403))

    login_user = None
    for user, pw in (("video4", "video4"), ("video3", "video3"), ("video2", "video2")):
        r = c.post("/api/auth/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            login_user = user
            check(results, f"login {user}", r)
            break
        print("skip", user, r.status_code)

    if not login_user:
        print("\nNo videographer credentials — auth rejection checks only.")
        fails = sum(1 for ok, *_ in results if not ok)
        print(f"{len(results) - fails}/{len(results)} passed")
        return 0 if fails == 0 else 1

    me = c.get("/api/auth/me").get_json() or {}
    if not me.get("is_videographer"):
        print("Logged in but not videographer — aborting VG-specific checks")
        return 1

    prior = list(me.get("district_ids") or ([me["district_id"]] if me.get("district_id") else []))
    print("prior districts:", len(prior), "sample:", prior[:5])

    # Empty list rejected
    check(
        results,
        "PATCH empty district_ids -> 400",
        c.patch("/api/survey/my-districts", json={"district_ids": []}),
        (400,),
    )
    check(
        results,
        "PATCH missing body -> 400",
        c.patch("/api/survey/my-districts", json={}),
        (400,),
    )

    # Districts catalog (modal source)
    dist = check(
        results,
        "GET districts andhra",
        c.get("/api/survey/districts?state_key=andhra"),
        (200, 503),
    )
    rows = dist if isinstance(dist, list) else (dist.get("districts") if isinstance(dist, dict) else None)
    if rows is None and isinstance(dist, list):
        rows = dist
    # API may return a bare list
    if not isinstance(rows, list):
        rows = dist if isinstance(dist, list) else []
    print("district catalog rows:", len(rows) if isinstance(rows, list) else type(dist))

    pick = []
    if isinstance(rows, list) and rows and len(prior) <= 5:
        pick = [int(rows[0]["district_id"])]
        if len(rows) > 1:
            pick.append(int(rows[1]["district_id"]))
    elif prior and len(prior) <= 5:
        pick = [int(x) for x in prior[:2]]
    else:
        print("skip mutate districts (account has", len(prior), "districts — avoid shrinking)")

    if pick:
        body = check(
            results,
            "PATCH save 1-2 districts",
            c.patch("/api/survey/my-districts", json={"district_ids": pick}),
            (200, 409),
        )
        if body.get("ok") or body.get("district_ids"):
            saved = {str(x) for x in (body.get("district_ids") or [])}
            expect = {str(x) for x in pick}
            if saved != expect:
                print("WARN saved ids mismatch", sorted(saved), sorted(expect))
            me2 = c.get("/api/auth/me").get_json() or {}
            me_ids = {str(x) for x in (me2.get("district_ids") or [])}
            ok_me = me_ids == expect
            results.append((ok_me, "me reflects district_ids after save", 200 if ok_me else 500, f"me={sorted(me_ids)} expect={sorted(expect)}"))
            print(("OK  " if ok_me else "FAIL"), "me reflects district_ids after save", "->", 200 if ok_me else 500)

        # Restore prior districts after the small-set smoke
        if prior:
            restore = check(
                results,
                "restore prior districts",
                c.patch("/api/survey/my-districts", json={"district_ids": [int(x) for x in prior]}),
                (200, 409),
            )
            if restore.get("ok") or restore.get("district_ids"):
                print("restored count:", len(restore.get("district_ids") or prior))
    else:
        # Still exercise a no-op-ish save with current districts (first id only would shrink — use full prior)
        if prior:
            body = check(
                results,
                "PATCH same districts (no shrink)",
                c.patch("/api/survey/my-districts", json={"district_ids": [int(x) for x in prior]}),
                (200, 409),
            )
            if body.get("district_ids"):
                ok_same = len(body["district_ids"]) == len(prior)
                results.append((ok_same, "PATCH preserves district count", 200 if ok_same else 500, ""))
                print(("OK  " if ok_same else "FAIL"), "PATCH preserves district count", "->", len(body.get("district_ids") or []))

    # Clear assignment (idempotent — ok if nothing assigned)
    cleared = check(
        results,
        "POST clear as VG (no user_id)",
        c.post("/api/survey/assignments/clear", json={}),
        (200,),
    )
    if not isinstance(cleared, dict) or "cleared" not in cleared:
        print("WARN clear response shape unexpected:", json.dumps(cleared)[:200])

    # Assign a tiny auto_track then clear it (must cleared:true)
    assign = c.post("/api/survey/assign", json={
        "mode": "auto_track",
        "start_lat": 17.45,
        "start_lon": 78.39,
        "end_lat": 17.46,
        "end_lon": 78.40,
        "start_label": "ClearTestA",
        "end_label": "ClearTestB",
    })
    check(results, "POST assign auto_track for clear test", assign, (200, 400, 403, 409))
    if assign.status_code == 200:
        cleared2 = check(
            results,
            "POST clear after assign -> cleared true",
            c.post("/api/survey/assignments/clear", json={}),
            (200,),
        )
        ok_cleared = bool(cleared2.get("cleared"))
        results.append((ok_cleared, "cleared flag true after assign", 200 if ok_cleared else 500, str(cleared2.get("message") or "")))
        print(("OK  " if ok_cleared else "FAIL"), "cleared flag true after assign", "->", cleared2.get("cleared"), cleared2.get("message") or "")
        me_sum = c.get("/api/survey/assignments")
        body = me_sum.get_json(silent=True) or {}
        # summary endpoint may wrap
        summary = body.get("summary") or body
        still = bool(summary.get("segment_count") or summary.get("mode") or summary.get("start"))
        # after clear, should have no active work
        check(results, "GET assignments after clear", me_sum, (200, 403))
        if me_sum.status_code == 200:
            results.append((not still or not summary.get("start"), "no active start after clear", 200 if (not still or not summary.get("start")) else 500, ""))
            print(("OK  " if (not summary.get("start")) else "FAIL"), "no active start after clear")

    # Second clear still 200
    check(
        results,
        "POST clear again (idempotent)",
        c.post("/api/survey/assignments/clear", json={}),
        (200,),
    )

    # Admin clear without user_id
    c.post("/api/auth/logout")
    ar = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    if ar.status_code == 200:
        check(
            results,
            "admin clear without user_id -> 400",
            c.post("/api/survey/assignments/clear", json={}),
            (400,),
        )
    else:
        print("skip admin clear check", ar.status_code)

    fails = sum(1 for ok, *_ in results if not ok)
    print(f"\n{len(results) - fails}/{len(results)} checks passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
