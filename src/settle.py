"""Settle open bets in data/bets.csv with the final score and bet365's closing price for the same line.

  python3 src/settle.py

closing_odds = bet365 closing odds for that market/line/side (Odds-API.io historical odds; falls back to
the last odds seen in a scan); clv = odds / closing_odds - 1.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import oddsapi as o  # noqa: E402
from model import _settle, _split  # noqa: E402
from scan import BOOK, load_bets, offers, save_bets  # noqa: E402


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
    profit *= float(b.get("stake") or 1)
    if profit >= (odds - 1) * float(b.get("stake") or 1) - 1e-9:
        outcome = "Vinst"
    elif profit > 0:
        outcome = "Halv vinst"
    elif profit == 0:
        outcome = "Push"
    elif profit > -float(b.get("stake") or 1) + 1e-9:
        outcome = "Halv förlust"
    else:
        outcome = "Förlust"
    return round(profit, 4), outcome


def main():
    bets = load_bets()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    todo = {b["event_id"] for b in bets if not b.get("outcome") and b["kickoff_utc"] < now}
    info = {}
    for eid in todo:
        d = o.get("/historical/odds", eventId=eid, bookmakers=BOOK, markets="Spread,Totals")
        if not d:
            d = o.get(f"/events/{eid}")
        info[eid] = d or {}
    done = 0
    for b in bets:
        d = info.get(b["event_id"])
        if not d or b.get("outcome"):
            continue
        st = d.get("status")
        if st == "cancelled":
            b["outcome"], b["profit"] = "Void", 0
            continue
        if st != "settled":
            continue
        sc = (d.get("scores") or {}).get("periods", {}).get("ft") or d.get("scores") or {}
        hg, ag = int(sc["home"]), int(sc["away"])
        b["result"] = f"{hg}-{ag}"
        b["profit"], b["outcome"] = settle_one(b, hg, ag)
        close = None
        for mk, line, side, odd in offers((d.get("bookmakers") or {}).get(BOOK, [])):
            if mk == b["market"] and abs(line - float(b["line"])) < 1e-9 and side == b["side"]:
                close = odd
        close = close or (float(b["last_seen_odds"]) if b.get("last_seen_odds") else None)
        if close:
            b["closing_odds"] = close
            b["clv"] = round(float(b["odds"]) / close - 1, 4)
        done += 1
    save_bets(bets)
    print(f"settled {done}, open {sum(1 for b in bets if not b.get('outcome'))}, api calls {o.CALLS}")


if __name__ == "__main__":
    main()
