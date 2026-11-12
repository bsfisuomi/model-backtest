"""Collect results (+ bet365 1X2 closing for the current season) for every configured league.

  python3 src/collect.py                 # all leagues
  python3 src/collect.py poland-2 ...    # some leagues

Writes data/matches/<league>.csv. Incremental: matches already stored with odds are not re-fetched.
Rows:
  scope = current   current season, with 1X2 odds (used for ratings and goals)
  scope = previous  previous season of the same league, results only (goals model fill-up)
  scope = team      a team's last league matches elsewhere (promoted/relegated teams), results only
"""
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import api  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["scope", "season_id", "season", "tournament_id", "match_id", "round", "kickoff_utc",
          "home", "home_id", "away", "away_id", "home_goals", "away_goals",
          "open_1", "open_x", "open_2", "close_1", "close_x", "close_2"]
CUP_WORDS = ("cup", "puchar", "pokal", "pohár", "pohar", "copa", "friendl", "super", "playoff",
             "play-off", "relegation", "promotion", "kvalifikation", "coupe", "taça", "emperor")


def load_cfg():
    return json.load(open(os.path.join(ROOT, "config", "leagues.json")))


def path_for(key):
    return os.path.join(ROOT, "data", "matches", f"{key}.csv")


def read_rows(key):
    p = path_for(key)
    if not os.path.exists(p):
        return {}
    with open(p, newline="") as f:
        return {(r["scope"], r["match_id"]): r for r in csv.DictReader(f)}


def row_from_event(e, scope):
    return {
        "scope": scope, "season_id": e.get("season", {}).get("id"), "season": e.get("season", {}).get("name"),
        "tournament_id": e["tournament"].get("uniqueTournament", {}).get("id"),
        "match_id": str(e["id"]), "round": (e.get("roundInfo") or {}).get("round", ""),
        "kickoff_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["startTimestamp"])),
        "home": e["homeTeam"]["name"], "home_id": e["homeTeam"]["id"],
        "away": e["awayTeam"]["name"], "away_id": e["awayTeam"]["id"],
        "home_goals": e.get("homeScore", {}).get("current"), "away_goals": e.get("awayScore", {}).get("current"),
        "open_1": "", "open_x": "", "open_2": "", "close_1": "", "close_x": "", "close_2": "",
    }


def finished(e):
    return e.get("status", {}).get("type") == "finished" and e.get("homeScore", {}).get("current") is not None


def collect(key, lg, goals_n):
    rows = read_rows(key)
    ss = lg.get("seasons_override") or api.seasons(lg["tid"])
    if not ss:
        print(f"[{key}] no seasons")
        return
    cur = lg.get("current_season") or ss[0]["id"]
    prev = lg.get("previous_season") or (ss[1]["id"] if len(ss) > 1 else None)

    # current season: results + 1X2 odds
    for e in api.season_events(lg["tid"], cur, "last"):
        if not finished(e):
            continue
        k = ("current", str(e["id"]))
        if k in rows and rows[k]["close_1"]:
            continue
        r = row_from_event(e, "current")
        o = api.odds_1x2(e["id"])
        if o:
            r.update(dict(zip(["open_1", "open_x", "open_2", "close_1", "close_x", "close_2"], o)))
        rows[k] = r

    # previous season: results only
    if prev:
        have_prev = any(s == "previous" for s, _ in rows)
        if not have_prev or lg.get("refresh_previous"):
            for e in api.season_events(lg["tid"], prev, "last"):
                if finished(e):
                    rows[("previous", str(e["id"]))] = row_from_event(e, "previous")

    # teams that still lack goals_n league matches (promoted / relegated): their other league games
    league_ids = {r["match_id"] for (s, _), r in rows.items() if s in ("current", "previous")}
    count = {}
    teams = {}
    for (s, _), r in rows.items():
        if s in ("current", "previous"):
            for side in ("home", "away"):
                count[r[f"{side}_id"]] = count.get(r[f"{side}_id"], 0) + 1
        if s == "current":
            teams[r["home_id"]] = r["home"]
            teams[r["away_id"]] = r["away"]
    for tid_, name in teams.items():
        have = count.get(tid_, 0) + sum(1 for (s, _), r in rows.items()
                                        if s == "team" and tid_ in (r["home_id"], r["away_id"]))
        if have >= goals_n:
            continue
        for e in api.team_last_events(tid_):
            ut = e["tournament"].get("uniqueTournament", {})
            nm = (ut.get("name", "") + " " + e["tournament"].get("name", "")).lower()
            if not finished(e) or str(e["id"]) in league_ids or any(w in nm for w in CUP_WORDS):
                continue
            rows[("team", str(e["id"]))] = row_from_event(e, "team")

    with open(path_for(key), "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["kickoff_utc"], r["match_id"], r["scope"])):
            w.writerow(r)
    n_cur = sum(1 for s, _ in rows if s == "current")
    n_odds = sum(1 for (s, _), r in rows.items() if s == "current" and r["close_1"])
    print(f"[{key}] current={n_cur} (odds {n_odds}) previous={sum(1 for s, _ in rows if s == 'previous')} "
          f"team={sum(1 for s, _ in rows if s == 'team')}  api calls so far {api.CALLS}")


def main(keys):
    cfg = load_cfg()
    keys = keys or list(cfg["leagues"])
    for k in keys:
        try:
            collect(k, cfg["leagues"][k], cfg["model"]["goals_matches"])
        except Exception as ex:  # keep going with the other leagues
            print(f"[{k}] failed: {ex}")


if __name__ == "__main__":
    main(sys.argv[1:])
