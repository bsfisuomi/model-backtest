"""Scan bet365's Asian lines for upcoming matches and log value bets.

  python3 src/scan.py [--days 3] [league-slug ...]

For every upcoming match (Odds-API.io, bet365) in a configured league the model prices each Asian
handicap ("Spread") and total ("Totals") line that bet365 offers; offers with EV >= betting.min_ev are
logged in data/bets.csv the first time they qualify. Later scans only update last_seen_odds.

Writes data/prices/latest.csv (model fair odds per match/line), data/scans/<UTC>.csv (every offer
compared) and data/bets.csv.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import oddsapi as o  # noqa: E402
from model import CFG, LeagueModel, ev, fair_odds, score_matrix  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = CFG["betting"].get("book", "Bet365")
BET_FIELDS = ["bet_id", "logged_utc", "league", "event_id", "kickoff_utc", "home", "away", "market", "line",
              "side", "odds", "fair_odds", "ev", "stake", "p_home_2way", "exp_goals", "lam_home", "lam_away",
              "n_rating_h", "n_rating_a", "ha", "book_1", "book_x", "book_2", "last_seen_utc", "last_seen_odds",
              "result", "outcome", "profit", "closing_odds", "clv"]
SCAN_FIELDS = ["league", "event_id", "kickoff_utc", "home", "away", "market", "line", "side", "odds",
               "fair_odds", "ev"]


def bets_path():
    return os.path.join(ROOT, "data", "bets.csv")


def load_bets():
    p = bets_path()
    return list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []


def save_bets(bets):
    with open(bets_path(), "w", newline="") as f:
        w = csv.DictWriter(f, BET_FIELDS)
        w.writeheader()
        for b in bets:
            w.writerow({k: b.get(k, "") for k in BET_FIELDS})


def offers(book_markets):
    """bet365 markets -> [(market, line, side, odds)]; Spread hdp is the home handicap."""
    out = []
    for m in book_markets:
        if m["name"] == "Spread":
            for x in m["odds"]:
                line = float(x["hdp"]) + 0.0
                for side in ("home", "away"):
                    if x.get(side):
                        out.append(("ah", line, side, float(x[side])))
        elif m["name"] == "Totals":
            for x in m["odds"]:
                line = float(x["hdp"])
                for side in ("over", "under"):
                    if x.get(side):
                        out.append(("total", line, side, float(x[side])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("leagues", nargs="*")
    a = ap.parse_args()
    leagues = set(a.leagues or CFG["leagues"])
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    evs = o.get("/events", sport="football", status="pending", bookmaker=BOOK, limit=5000,
                **{"from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "to": (now + dt.timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")}) or []
    evs = [e for e in evs if e["league"]["slug"] in leagues]
    print(f"{len(evs)} upcoming bet365 matches in {len({e['league']['slug'] for e in evs})} leagues")

    min_ev, stake = CFG["betting"]["min_ev"], CFG["betting"]["stake"]
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    now_txt = now.strftime("%Y-%m-%d %H:%M")
    today = now.strftime("%Y-%m-%d")
    bets = load_bets()
    by_id = {b["bet_id"]: b for b in bets}
    models, preds = {}, {}
    price_rows, scan_rows, new, skipped = [], [], 0, {}

    for e in evs:
        slug = e["league"]["slug"]
        if slug not in models:
            models[slug] = LeagueModel(slug)
        pr = models[slug].predict(e["home"], e["away"], today)
        preds[e["id"]] = pr
        if "lam_home" not in pr:
            skipped[pr["reason"]] = skipped.get(pr["reason"], 0) + 1
    priced = [e for e in evs if "lam_home" in preds[e["id"]]]

    for i in range(0, len(priced), 10):
        chunk = priced[i:i + 10]
        d = o.get("/odds/multi", eventIds=",".join(str(e["id"]) for e in chunk), bookmakers=BOOK,
                  markets="ML,Spread,Totals") or []
        odds_by = {x["id"]: x for x in d} if isinstance(d, list) else {}
        for e in chunk:
            pr = preds[e["id"]]
            mat = score_matrix(pr["lam_home"], pr["lam_away"])
            ko = e["date"][:16].replace("T", " ")
            base = {"league": e["league"]["slug"], "event_id": e["id"], "kickoff_utc": ko,
                    "home": e["home"], "away": e["away"]}
            bm = (odds_by.get(e["id"]) or {}).get("bookmakers", {}).get(BOOK, [])
            ml = next((m["odds"][0] for m in bm if m["name"] == "ML" and m.get("odds")), {})
            for mk, line, side, odd in offers(bm):
                fo = fair_odds(mat, mk, line, side)
                e_v = ev(mat, mk, line, side, odd)
                rec = {**base, "market": mk, "line": line, "side": side, "odds": odd,
                       "fair_odds": round(fo, 3) if fo else "", "ev": round(e_v, 4)}
                scan_rows.append(rec)
                bid = f"{e['id']}|{mk}|{line}|{side}"
                if bid in by_id:
                    if not by_id[bid].get("outcome"):
                        by_id[bid]["last_seen_utc"], by_id[bid]["last_seen_odds"] = now_txt, odd
                    continue
                if e_v >= min_ev:
                    b = {**rec, "bet_id": bid, "logged_utc": now_txt, "stake": stake,
                         "p_home_2way": round(pr["p_home"], 4), "exp_goals": round(pr["exp_goals"], 3),
                         "lam_home": round(pr["lam_home"], 3), "lam_away": round(pr["lam_away"], 3),
                         "n_rating_h": pr["n_home"], "n_rating_a": pr["n_away"], "ha": round(pr["ha"], 4),
                         "book_1": ml.get("home", ""), "book_x": ml.get("draw", ""), "book_2": ml.get("away", ""),
                         "last_seen_utc": now_txt, "last_seen_odds": odd}
                    bets.append(b)
                    by_id[bid] = b
                    new += 1
            price_rows.append({**base, "p_home_2way": round(pr["p_home"], 4), "exp_goals": round(pr["exp_goals"], 3),
                               "lam_home": round(pr["lam_home"], 3), "lam_away": round(pr["lam_away"], 3),
                               "fair_ah0_home": round(fair_odds(mat, "ah", 0.0, "home"), 3),
                               "fair_ah0_away": round(fair_odds(mat, "ah", 0.0, "away"), 3),
                               "fair_o25": round(fair_odds(mat, "total", 2.5, "over"), 3),
                               "fair_u25": round(fair_odds(mat, "total", 2.5, "under"), 3),
                               "book_offers": len(offers(bm))})

    for sub in ("prices", "scans"):
        os.makedirs(os.path.join(ROOT, "data", sub), exist_ok=True)
    if price_rows:
        with open(os.path.join(ROOT, "data", "prices", "latest.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, list(price_rows[0]))
            w.writeheader()
            w.writerows(price_rows)
    if scan_rows:
        with open(os.path.join(ROOT, "data", "scans", f"{stamp}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, SCAN_FIELDS)
            w.writeheader()
            w.writerows(scan_rows)
    save_bets(bets)
    print(f"priced {len(priced)} matches, skipped {skipped}")
    print(f"offers compared {len(scan_rows)}, new bets {new}, total bets {len(bets)}, api calls {o.CALLS}")


if __name__ == "__main__":
    main()
