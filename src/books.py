"""Bookmaker odds adapters. Each returns a list of offers:
   {"market": "ah"|"total", "line": float (home handicap for ah), "side": "home"|"away"|"over"|"under", "odds": float}

- sportsapi_bet365: bet365 via SportsAPI Pro (main AH line + half-goal totals). Only where the feed has them
  (e.g. J1, K League 2, Peru); most small leagues only have 1X2 there.
- bet365: placeholder for the dedicated bet365 API (to be plugged in when the key/docs are available).
"""
import re

import api


def sportsapi_bet365(event):
    d = api.get(f"/api/match/{event['id']}/odds/all")
    mk = ((d or {}).get("data") or {}).get("markets", []) if d else []
    out = []
    home, away = event["homeTeam"]["name"], event["awayTeam"]["name"]
    for m in mk:
        if m.get("isLive") or m.get("suspended"):
            continue
        name = m.get("marketName")
        if name == "Asian handicap":
            for c in m["choices"]:
                g = re.match(r"\(([-+]?\d+(?:\.\d+)?)(?:,\s*([-+]?\d+(?:\.\d+)?))?\)\s*(.*)", c["name"])
                if not g:
                    continue
                vals = [float(x) for x in g.groups()[:2] if x is not None]
                hcp = sum(vals) / len(vals)          # "(-0.5, -1)" style -> -0.75
                team = g.group(3).strip()
                side = "home" if team == home else "away" if team == away else None
                if side is None:
                    continue
                line = hcp if side == "home" else -hcp
                out.append({"market": "ah", "line": line, "side": side, "odds": api.frac_to_dec(c["fractionalValue"])})
        elif name == "Match goals" and m.get("choiceGroup"):
            line = float(m["choiceGroup"])
            for c in m["choices"]:
                side = c["name"].lower()
                if side in ("over", "under"):
                    out.append({"market": "total", "line": line, "side": side, "odds": api.frac_to_dec(c["fractionalValue"])})
    return out


def bet365(event):
    raise NotImplementedError("bet365 API not connected yet")


ADAPTERS = {"sportsapi-bet365": sportsapi_bet365, "bet365": bet365}
