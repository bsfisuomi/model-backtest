"""Bet bet365's OPENING Asian lines where the model finds value.

  python3 src/scan.py [--days 7] [--backfill-days 4] [league-slug ...]

For every match in a configured league (upcoming within --days, plus matches played in the last
--backfill-days that a previous run may have missed) the bet365 opening main line of the Asian handicap
("Spread"; only 50/50 lines, both prices within betting.ah_balanced_range) and of the total ("Totals") is read from Odds-API.io /odds/movements. The model is evaluated
with the data available on the day the line opened. Each side with EV >= betting.min_ev is logged once in
data/bets.csv at the opening price. The opening price never changes, so a match only has to be scanned
once - but Odds-API keeps line history only a few days, so run at least every 3-4 days.

Writes data/bets.csv, data/prices/latest.csv (model per upcoming match) and data/scans/<UTC>.csv
(opening and latest main lines seen in this run, with model fair odds and EV).
"""
import argparse
import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import oddsapi as o  # noqa: E402
from model import CFG, LeagueModel, ev, fair_odds, score_matrix  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = CFG["betting"].get("book", "Bet365")
BET_FIELDS = ["bet_id", "logged_utc", "league", "event_id", "kickoff_utc", "home", "away", "market", "line",
              "side", "odds", "opened_utc", "fair_odds", "ev", "stake", "p_home_2way", "exp_goals", "lam_home",
              "lam_away", "n_rating_h", "n_rating_a", "ha", "model_day", "latest_line", "latest_odds",
              "latest_utc", "result", "outcome", "profit", "closing_line", "closing_odds", "clv"]
SCAN_FIELDS = ["league", "event_id", "kickoff_utc", "home", "away", "market", "kind", "line", "side", "odds",
               "time_utc", "fair_odds", "ev"]


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
    """bet365 /odds markets -> [(market, line, side, odds)]; Spread hdp is the home handicap."""
    out = []
    for m in book_markets:
        if m["name"] not in ("Spread", "Totals"):
            continue
        mk = "ah" if m["name"] == "Spread" else "total"
        for x in m["odds"]:
            line = float(x["hdp"]) + 0.0
            for side, key in ((("home", "home"), ("away", "away")) if mk == "ah" else (("over", "over"), ("under", "under"))):
                if x.get(key):
                    out.append((mk, line, side, float(x[key])))
    return out


def sides(mk, entry):
    """A movements entry -> [(side, odds)]. On Totals the over price is in 'home' and the under in 'away'."""
    if mk == "ah":
        return [("home", entry.get("home")), ("away", entry.get("away"))]
    return [("over", entry.get("home")), ("under", entry.get("away"))]


def ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def matches(leagues, days, backfill):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    evs = {}
    up = o.get("/events", sport="football", status="pending", bookmaker=BOOK, limit=5000,
               **{"from": iso(now), "to": iso(now + dt.timedelta(days=days))}) or []
    for e in up:
        if e["league"]["slug"] in leagues:
            evs[e["id"]] = {"id": e["id"], "league": e["league"]["slug"], "home": e["home"], "away": e["away"],
                            "date": e["date"]}
    if backfill:
        sl = sorted(leagues)
        for i in range(0, len(sl), 10):
            skip = 0
            while True:
                d = o.get("/historical/closing-lines", sport="football", leagues=",".join(sl[i:i + 10]),
                          markets="ML", bookmakers=BOOK, limit=100, skip=skip,
                          **{"from": iso(now - dt.timedelta(days=backfill)), "to": iso(now)})
                if not isinstance(d, list):
                    break
                for e in d:
                    evs.setdefault(e["id"], {"id": e["id"], "league": e["league"]["slug"], "home": e["home"],
                                             "away": e["away"], "date": e["date"]})
                if len(d) < 100:
                    break
                skip += 100
    return now, list(evs.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--backfill-days", type=float, default=4)
    ap.add_argument("leagues", nargs="*")
    a = ap.parse_args()
    leagues = set(a.leagues or CFG["leagues"])
    now, evs = matches(leagues, a.days, a.backfill_days)
    print(f"{len(evs)} matches in {len({e['league'] for e in evs})} leagues")
    min_ev, stake = CFG["betting"]["min_ev"], CFG["betting"]["stake"]
    now_txt = now.strftime("%Y-%m-%d %H:%M")
    bets = load_bets()
    by_id = {b["bet_id"]: b for b in bets}
    done_events = {b["event_id"] for b in bets}
    models, price_rows, scan_rows = {}, [], []
    stats = {"no opening line": 0, "new bets": 0, "priced": 0}
    skipped = {}

    for e in sorted(evs, key=lambda e: e["date"]):
        slug = e["league"]
        if slug not in models:
            models[slug] = LeagueModel(slug)
        lm = models[slug]
        ko = e["date"][:16].replace("T", " ")
        base = {"league": slug, "event_id": e["id"], "kickoff_utc": ko, "home": e["home"], "away": e["away"]}
        for mname, mk in (("Spread", "ah"), ("Totals", "total")):
            d = o.get("/odds/movements", eventId=e["id"], bookmaker=BOOK, market=mname)
            op = d.get("opening") if isinstance(d, dict) else None
            if not op or op.get("hdp") is None:
                stats["no opening line"] += 1
                continue
            la = d.get("latest") or {}
            day = ts(op["timestamp"])[:10]
            pr = lm.predict(e["home"], e["away"], day)
            if "lam_home" not in pr:
                skipped[pr["reason"]] = skipped.get(pr["reason"], 0) + 1
                continue
            mat = score_matrix(pr["lam_home"], pr["lam_away"])
            line = float(op["hdp"]) + 0.0
            for kind, entry in (("opening", op), ("latest", la)):
                if not entry or entry.get("hdp") is None:
                    continue
                ln = float(entry["hdp"]) + 0.0
                for side, odd in sides(mk, entry):
                    if odd:
                        fo = fair_odds(mat, mk, ln, side)
                        scan_rows.append({**base, "market": mk, "kind": kind, "line": ln, "side": side, "odds": odd,
                                          "time_utc": ts(entry["timestamp"]), "fair_odds": round(fo, 3) if fo else "",
                                          "ev": round(ev(mat, mk, ln, side, odd), 4)})
            if mk == "ah":  # only 50/50 Asian handicap lines: both opening prices inside the balanced range
                lo_b, hi_b = CFG["betting"].get("ah_balanced_range", [1.70, 2.20])
                if not all(x and lo_b <= float(x) <= hi_b for _, x in sides(mk, op)):
                    stats["ah not 50/50"] = stats.get("ah not 50/50", 0) + 1
                    continue
            for side, odd in sides(mk, op):
                if not odd:
                    continue
                bid = f"{e['id']}|{mk}|open|{side}"
                if bid in by_id:
                    b = by_id[bid]
                    if not b.get("outcome") and la.get("hdp") is not None:
                        lo = dict(sides(mk, la)).get(side)
                        b["latest_line"], b["latest_odds"], b["latest_utc"] = float(la["hdp"]) + 0.0, lo, ts(la["timestamp"])
                    continue
                e_v = ev(mat, mk, line, side, float(odd))
                if e_v < min_ev:
                    continue
                lo = dict(sides(mk, la)).get(side) if la.get("hdp") is not None else ""
                b = {**base, "bet_id": bid, "logged_utc": now_txt, "market": mk, "line": line, "side": side,
                     "odds": odd, "opened_utc": ts(op["timestamp"]), "fair_odds": round(fair_odds(mat, mk, line, side), 3),
                     "ev": round(e_v, 4), "stake": stake, "p_home_2way": round(pr["p_home"], 4),
                     "exp_goals": round(pr["exp_goals"], 3), "lam_home": round(pr["lam_home"], 3),
                     "lam_away": round(pr["lam_away"], 3), "n_rating_h": pr["n_home"], "n_rating_a": pr["n_away"],
                     "ha": round(pr["ha"], 4), "model_day": day,
                     "latest_line": float(la["hdp"]) + 0.0 if la.get("hdp") is not None else "",
                     "latest_odds": lo, "latest_utc": ts(la["timestamp"]) if la.get("timestamp") else ""}
                bets.append(b)
                by_id[bid] = b
                stats["new bets"] += 1
        # model snapshot for upcoming matches
        if ko >= now_txt:
            pr = lm.predict(e["home"], e["away"], now.strftime("%Y-%m-%d"))
            if "lam_home" in pr:
                stats["priced"] += 1
                mat = score_matrix(pr["lam_home"], pr["lam_away"])
                price_rows.append({**base, "p_home_2way": round(pr["p_home"], 4), "exp_goals": round(pr["exp_goals"], 3),
                                   "lam_home": round(pr["lam_home"], 3), "lam_away": round(pr["lam_away"], 3),
                                   "fair_ah0_home": round(fair_odds(mat, "ah", 0.0, "home"), 3),
                                   "fair_ah0_away": round(fair_odds(mat, "ah", 0.0, "away"), 3),
                                   "fair_o25": round(fair_odds(mat, "total", 2.5, "over"), 3),
                                   "fair_u25": round(fair_odds(mat, "total", 2.5, "under"), 3)})

    for sub in ("prices", "scans"):
        os.makedirs(os.path.join(ROOT, "data", sub), exist_ok=True)
    lp = os.path.join(ROOT, "data", "prices", "latest.csv")
    if price_rows and a.leagues and os.path.exists(lp):  # partial scan: keep the other leagues' rows
        price_rows += [r for r in csv.DictReader(open(lp, newline="")) if r["league"] not in leagues]
    if price_rows:
        with open(lp, "w", newline="") as f:
            w = csv.DictWriter(f, list(price_rows[0]))
            w.writeheader()
            w.writerows(price_rows)
    if scan_rows:
        with open(os.path.join(ROOT, "data", "scans", f"{now.strftime('%Y-%m-%dT%H%MZ')}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, SCAN_FIELDS)
            w.writeheader()
            w.writerows(scan_rows)
    save_bets(bets)
    print(f"{stats}, model skipped (per market) {skipped}")
    print(f"total bets {len(bets)}, api calls {o.CALLS}")


if __name__ == "__main__":
    main()
