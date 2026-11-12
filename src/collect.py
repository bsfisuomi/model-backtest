"""Collect settled matches with bet365 closing 1X2 and closing main goal line for every league in config/leagues.json (Odds-API.io).

  python3 src/collect.py              # all leagues
  python3 src/collect.py poland-ii-liga england-premier-league
  python3 src/collect.py --refetch        # full history again

data/matches/<slug>.csv, one row per settled match. First run fetches ~540 days back (this season and
last season); later runs only fetch from a few days before the newest stored match.
"""
import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import oddsapi as o  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["event_id", "date", "home", "away", "home_goals", "away_goals", "close_1", "close_x", "close_2",
          "close_tl", "close_over", "close_under"]
BATCH = 10      # leagues per request (API max)
DAYS_BACK = 540


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def path_for(slug):
    return os.path.join(ROOT, "data", "matches", f"{slug}.csv")


def read(slug):
    p = path_for(slug)
    if not os.path.exists(p):
        return {}
    return {r["event_id"]: r for r in csv.DictReader(open(p, newline=""))}


def write(slug, rows):
    with open(path_for(slug), "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["date"], r["event_id"])):
            w.writerow(r)


def row(x):
    ft = (x.get("scores") or {}).get("periods", {}).get("ft") or x.get("scores") or {}
    r = {"event_id": str(x["id"]), "date": x["date"][:16].replace("T", " "), "home": x["home"], "away": x["away"],
         "home_goals": ft.get("home"), "away_goals": ft.get("away"), "close_1": "", "close_x": "", "close_2": "",
         "close_tl": "", "close_over": "", "close_under": ""}
    for m in (x.get("bookmakers") or {}).get("Bet365", []):
        if m["name"] == "ML" and m.get("odds"):
            od = m["odds"][0]
            r["close_1"], r["close_x"], r["close_2"] = od.get("home", ""), od.get("draw", ""), od.get("away", "")
        if m["name"] == "Totals" and m.get("odds"):
            # main goal line = the most balanced closing line
            ok = [t for t in m["odds"] if t.get("over") and t.get("under")]
            if ok:
                t = min(ok, key=lambda t: abs(float(t["over"]) - float(t["under"])))
                r["close_tl"], r["close_over"], r["close_under"] = t["hdp"], t["over"], t["under"]
    return r


def fetch(slugs, frm, to):
    """All settled matches for up to 10 leagues between frm and to (chunks of <= 366 days)."""
    out = []
    start = frm
    while start < to:
        end = min(to, start + dt.timedelta(days=365))
        skip = 0
        while True:
            d = o.get("/historical/closing-lines", sport="football", leagues=",".join(slugs), markets="ML,Totals",
                      bookmakers="Bet365", limit=100, skip=skip, **{"from": iso(start), "to": iso(end)})
            if not isinstance(d, list):
                if d:
                    print("  api:", str(d)[:150])
                break
            out.extend(d)
            if len(d) < 100:
                break
            skip += 100
        start = end
    return out


def main(only):
    refetch = "--refetch" in only          # fetch the full history again (e.g. after adding columns)
    only = [x for x in only if x != "--refetch"]
    cfg = json.load(open(os.path.join(ROOT, "config", "leagues.json")))
    slugs = only or list(cfg["leagues"])
    os.makedirs(os.path.join(ROOT, "data", "matches"), exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    have = {s: ({} if refetch else read(s)) for s in slugs}
    # group leagues by start date so each request can carry 10 leagues
    groups = {}
    inc = [s for s in slugs if have[s]]
    if inc:
        last = min(max(r["date"] for r in have[s].values()) for s in inc)
        frm = max(dt.datetime.strptime(last[:10], "%Y-%m-%d") - dt.timedelta(days=4),
                  now.replace(tzinfo=None) - dt.timedelta(days=45))
        groups[frm.date().isoformat()] = inc
    full = [s for s in slugs if not have[s]]
    if full:
        groups["full"] = full
    for key, lst in groups.items():
        frm = now - dt.timedelta(days=DAYS_BACK) if key == "full" else \
            dt.datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        for i in range(0, len(lst), BATCH):
            part = lst[i:i + BATCH]
            got = fetch(part, frm, now)
            for x in got:
                s = x["league"]["slug"]
                if s in have and x.get("status") == "settled":
                    r = row(x)
                    if r["home_goals"] is not None:
                        have[s][r["event_id"]] = r
            for s in part:
                write(s, have[s])
            print(f"{', '.join(part)[:110]} ... +{len(got)} rows, calls {o.CALLS}", flush=True)
    tot = sum(len(v) for v in have.values())
    print(f"done: {len(slugs)} leagues, {tot} matches, {o.CALLS} calls")


if __name__ == "__main__":
    main(sys.argv[1:])
