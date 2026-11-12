"""Thin client for SportsAPI Pro (SofaScore-shaped data). Key from env SPORTSAPIPRO_KEY."""
import json
import os
import subprocess
import time

BASE = "https://api.sportsapipro.com/v2/football"


def _key():
    k = os.environ.get("SPORTSAPIPRO_KEY")
    if not k:
        p = os.path.expanduser("~/.secrets/sportsapipro_key")
        if os.path.exists(p):
            k = open(p).read().strip()
    if not k:
        raise SystemExit("Set SPORTSAPIPRO_KEY")
    return k


CALLS = 0


def get(path, retries=3):
    """GET a path; returns parsed JSON or None. curl is used so the session proxy is honoured."""
    global CALLS
    key = _key()
    for i in range(retries):
        CALLS += 1
        r = subprocess.run(["curl", "-sS", "-m", "40", "-H", f"x-api-key: {key}", BASE + path],
                           capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
        except Exception:
            time.sleep(1 + i)
            continue
        if d.get("success") is False and "404" in str(d.get("error", "")):
            return None
        if d.get("success") is False:
            time.sleep(1 + i)
            continue
        return d
    return None


def seasons(tid):
    d = get(f"/api/tournaments/{tid}/seasons") or {}
    return d.get("seasons", [])


def season_events(tid, sid, direction="last"):
    """All events of a season in one direction (last = played, next = upcoming), paged."""
    out, page = [], 0
    while True:
        d = get(f"/api/tournament/{tid}/season/{sid}/events/{direction}/{page}")
        data = (d or {}).get("data") or {}
        evs = data.get("events", [])
        out.extend(evs)
        if not data.get("hasNextPage") or not evs or page > 40:
            break
        page += 1
    return out


def team_last_events(team_id, pages=2):
    out = []
    for page in range(pages):
        d = get(f"/api/teams/{team_id}/events/last/{page}")
        data = (d or {}).get("data") or {}
        out.extend(data.get("events", []))
        if not data.get("hasNextPage"):
            break
    return out


def frac_to_dec(f):
    if not f:
        return None
    n, d = f.split("/")
    return round(1 + int(n) / int(d), 4)


def odds_1x2(match_id):
    """bet365 (provider 1) full-time 1X2: (open1, openX, open2, close1, closeX, close2) or None."""
    d = get(f"/api/match/{match_id}/odds")
    try:
        ch = {c["name"]: c for c in d["data"]["featured"]["fullTime"]["choices"]}
        return tuple(frac_to_dec(ch[k][f]) for f in ("initialFractionalValue", "fractionalValue")
                     for k in ("1", "X", "2"))
    except Exception:
        return None
