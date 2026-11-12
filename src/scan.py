"""Scan upcoming matches: price every Asian line with the model, compare with the book, log value bets.

  python3 src/scan.py [--days 7] [--book sportsapi-bet365] [league ...]

Writes
  data/prices/latest.csv          model fair odds for every upcoming match and line (overwritten)
  data/scans/<UTC stamp>.csv      every book offer seen in this scan, with model fair odds and EV
  data/bets.csv                   value bets (EV >= betting.min_ev), one row per match/market/line/side,
                                  logged the first time it qualifies (later scans update last_seen_odds)
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import api  # noqa: E402
import books  # noqa: E402
from model import CFG, LeagueModel, ev, fair_odds, lines, score_matrix  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BET_FIELDS = ["bet_id", "logged_utc", "league", "match_id", "kickoff_utc", "home", "away", "market", "line",
              "side", "odds", "fair_odds", "ev", "stake", "p_home_2way", "exp_goals", "lam_home", "lam_away",
              "n_rating_h", "n_rating_a", "book", "last_seen_utc", "last_seen_odds",
              "result", "outcome", "profit", "closing_odds", "clv"]


def upcoming(lg, days):
    ss = api.seasons(lg["tid"])
    if not ss:
        return []
    sid = lg.get("current_season") or ss[0]["id"]
    now = time.time()
    evs = [e for e in api.season_events(lg["tid"], sid, "next")
           if e.get("status", {}).get("type") == "notstarted" and now < e["startTimestamp"] < now + days * 86400]
    return evs


def load_bets():
    p = os.path.join(ROOT, "data", "bets.csv")
    if not os.path.exists(p):
        return []
    return list(csv.DictReader(open(p, newline="")))


def save_bets(bets):
    p = os.path.join(ROOT, "data", "bets.csv")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, BET_FIELDS)
        w.writeheader()
        for b in bets:
            w.writerow({k: b.get(k, "") for k in BET_FIELDS})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--book", default="sportsapi-bet365")
    ap.add_argument("leagues", nargs="*")
    a = ap.parse_args()
    bet_cfg = CFG["betting"]
    fetch = books.ADAPTERS[a.book]
    stamp = time.strftime("%Y-%m-%dT%H%MZ", time.gmtime())
    now_txt = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    bets = load_bets()
    by_id = {b["bet_id"]: b for b in bets}
    price_rows, scan_rows, new = [], [], 0
    ah_lines, tot_lines = lines(bet_cfg["ah_lines"]), lines(bet_cfg["total_lines"])

    for key in a.leagues or list(CFG["leagues"]):
        lg = CFG["leagues"][key]
        evs = upcoming(lg, a.days)
        if not evs:
            continue
        lm = LeagueModel(key)
        today = time.strftime("%Y-%m-%d", time.gmtime())
        for e in evs:
            ko = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["startTimestamp"]))
            pr = lm.predict(e["homeTeam"]["id"], e["awayTeam"]["id"], today)
            base = {"league": key, "match_id": e["id"], "kickoff_utc": ko,
                    "home": e["homeTeam"]["name"], "away": e["awayTeam"]["name"]}
            if "lam_home" not in pr:
                price_rows.append({**base, "note": pr.get("reason", "")})
                continue
            mat = score_matrix(pr["lam_home"], pr["lam_away"])
            fair = {}
            for ln in ah_lines:
                for side in ("home", "away"):
                    fair[("ah", ln, side)] = fair_odds(mat, "ah", ln, side)
            for ln in tot_lines:
                for side in ("over", "under"):
                    fair[("total", ln, side)] = fair_odds(mat, "total", ln, side)
            for (mk, ln, side), fo in fair.items():
                price_rows.append({**base, "market": mk, "line": ln, "side": side,
                                   "fair_odds": round(fo, 3) if fo else "", "p_home_2way": round(pr["p_home"], 4),
                                   "exp_goals": round(pr["exp_goals"], 3)})
            try:
                offers = fetch(e)
            except NotImplementedError as ex:
                print(ex)
                offers = []
            for o in offers:
                if not o["odds"]:
                    continue
                e_v = ev(mat, o["market"], o["line"], o["side"], o["odds"])
                fo = fair_odds(mat, o["market"], o["line"], o["side"])
                scan_rows.append({**base, **o, "fair_odds": round(fo, 3) if fo else "", "ev": round(e_v, 4)})
                bid = f"{e['id']}|{o['market']}|{o['line']}|{o['side']}"
                if bid in by_id:
                    by_id[bid]["last_seen_utc"], by_id[bid]["last_seen_odds"] = now_txt, o["odds"]
                    continue
                if e_v >= bet_cfg["min_ev"]:
                    b = {**base, **o, "bet_id": bid, "logged_utc": now_txt, "fair_odds": round(fo, 3),
                         "ev": round(e_v, 4), "stake": bet_cfg["stake"], "p_home_2way": round(pr["p_home"], 4),
                         "exp_goals": round(pr["exp_goals"], 3), "lam_home": round(pr["lam_home"], 3),
                         "lam_away": round(pr["lam_away"], 3), "n_rating_h": pr["n_home"], "n_rating_a": pr["n_away"],
                         "book": a.book, "last_seen_utc": now_txt, "last_seen_odds": o["odds"]}
                    bets.append(b)
                    by_id[bid] = b
                    new += 1
        print(f"[{key}] upcoming={len(evs)}")

    os.makedirs(os.path.join(ROOT, "data", "prices"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "data", "scans"), exist_ok=True)
    pf = ["league", "match_id", "kickoff_utc", "home", "away", "market", "line", "side", "fair_odds",
          "p_home_2way", "exp_goals", "note"]
    with open(os.path.join(ROOT, "data", "prices", "latest.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, pf)
        w.writeheader()
        for r in price_rows:
            w.writerow({k: r.get(k, "") for k in pf})
    if scan_rows:
        sf = ["league", "match_id", "kickoff_utc", "home", "away", "market", "line", "side", "odds", "fair_odds", "ev"]
        with open(os.path.join(ROOT, "data", "scans", f"{stamp}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, sf)
            w.writeheader()
            for r in scan_rows:
                w.writerow({k: r.get(k, "") for k in sf})
    save_bets(bets)
    print(f"offers compared={len(scan_rows)} new bets={new} total bets={len(bets)} api calls={api.CALLS}")


if __name__ == "__main__":
    main()
