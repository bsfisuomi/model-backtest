"""Settle open bets in data/bets.csv once matches are finished.

  python3 src/settle.py

closing_odds = the last odds seen for that line in a scan before kickoff (last_seen_odds);
clv = odds / closing_odds - 1.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import api  # noqa: E402
from model import _settle, _split  # noqa: E402
from scan import load_bets, save_bets  # noqa: E402


def settle_one(b, hg, ag):
    line, side, odds = float(b["line"]), b["side"], float(b["odds"])
    parts = _split(line)
    profit = 0.0
    for part in parts:
        if b["market"] == "ah":
            m = (hg - ag) + part if side == "home" else (ag - hg) - part
        else:
            m = (hg + ag) - part if side == "over" else part - (hg + ag)
        w, l = _settle(m)
        profit += (w * (odds - 1) - l) / len(parts)
    s = float(b.get("stake") or 1)
    outcome = "Vinst" if profit > 0 else "Förlust" if profit < 0 else "Push"
    if 0 < abs(profit) < (odds - 1) and profit > 0:
        outcome = "Halv vinst"
    if -1 < profit < 0:
        outcome = "Halv förlust"
    return round(profit * s, 4), outcome


def main():
    bets = load_bets()
    done = 0
    for b in bets:
        if b.get("outcome"):
            continue
        d = api.get(f"/api/match/{b['match_id']}")
        ev = ((d or {}).get("data") or {}).get("event") or (d or {}).get("data") or {}
        st = (ev.get("status") or {}).get("type")
        if st != "finished":
            if st in ("canceled", "postponed"):
                b["outcome"], b["profit"] = "Void", 0
            continue
        hg, ag = ev["homeScore"]["current"], ev["awayScore"]["current"]
        b["result"] = f"{hg}-{ag}"
        b["profit"], b["outcome"] = settle_one(b, hg, ag)
        if b.get("last_seen_odds"):
            b["closing_odds"] = b["last_seen_odds"]
            b["clv"] = round(float(b["odds"]) / float(b["last_seen_odds"]) - 1, 4)
        done += 1
    save_bets(bets)
    print(f"settled {done}")


if __name__ == "__main__":
    main()
