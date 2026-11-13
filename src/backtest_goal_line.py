"""Backtest of the goal line models (goal_line_tg = model 3, goal_line_avg = model 4) on totals.

  python3 src/backtest_goal_line.py closing [--min-ev 0.10] [--from 2025-08-01]
  python3 src/backtest_goal_line.py opening [--min-ev 0.10] [--since 2026-11-06]

closing: every settled match with a bet365 closing 50/50 goal line (the most balanced closing line). Both
         models are evaluated walk-forward (only matches played on earlier days), each side with
         EV >= min-ev is bet at the closing price and settled on the result.
opening: settled matches whose bet365 line history is still available (Odds-API keeps it a few days).
         The 50/50 opening line is the opening line (of the closing 50/50 line and its +-0.25 neighbours)
         whose two prices are closest. Bets at the opening price; CLV against the same line's close.
Writes data/backtest/goal_line_<mode>.csv and prints a summary per model.
Note: HA (used by model 3) is estimated on this + last season, so it contains some in-season hindsight.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from model import (GoalLineModel, LeagueModel, all_matches, ev, fair_odds, score_matrix,  # noqa: E402
                   total_matrix)
from settle import settle_one  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def prices(lm, glm, r):
    out = {}
    gp = glm.predict(r["home"], r["away"], r["day"])
    if "mu" in gp:
        out["goal_line_avg"] = (total_matrix(gp["mu"]), gp["mu"])
    if lm.ha is not None:
        gt = lm.predict(r["home"], r["away"], r["day"], tg_source="goal_line")
        if "lam_home" in gt:
            out["goal_line_tg"] = (score_matrix(gt["lam_home"], gt["lam_away"]), gt["exp_goals"])
    return out


def summarize(rows, title):
    print(f"\n== {title}")
    by = defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
        by[(r["model"], r["side"])].append(r)
    for k in sorted(by, key=str):
        xs = by[k]
        pl = sum(r["profit"] for r in xs)
        cl = [r["clv"] for r in xs if r.get("clv") not in (None, "")]
        line = f"{str(k):32s} bets {len(xs):6d}  P/L {pl:+9.2f}u  ROI {pl / len(xs) * 100:+6.2f}%"
        if cl:
            line += f"  CLV {sum(cl) / len(cl) * 100:+5.2f}% (n {len(cl)})"
        print(line)


def closing(a):
    by, _ = all_matches()
    glm = GoalLineModel("all")
    out, n_eval = [], 0
    for slug, rows in by.items():
        lm = LeagueModel(slug)
        for r in rows:
            if r["day"] < a.frm or r["close_tl"] is None or not r["close_over"] or not r["close_under"]:
                continue
            ps = prices(lm, glm, r)
            if not ps:
                continue
            n_eval += 1
            for model, (mat, mu) in ps.items():
                for side, odd in (("over", r["close_over"]), ("under", r["close_under"])):
                    e_v = ev(mat, "total", r["close_tl"], side, odd)
                    if e_v < a.min_ev:
                        continue
                    b = {"market": "total", "line": r["close_tl"], "side": side, "odds": odd, "stake": 1}
                    profit, outcome = settle_one(b, r["hg"], r["ag"])
                    out.append({"model": model, "league": slug, "date": r["date"], "home": r["home"],
                                "away": r["away"], "line": r["close_tl"], "side": side, "odds": odd,
                                "model_mu": round(mu, 3), "fair_odds": round(fair_odds(mat, "total", r["close_tl"], side), 3),
                                "ev": round(e_v, 4), "result": f"{r['hg']}-{r['ag']}", "outcome": outcome,
                                "profit": profit})
    print(f"matches evaluated (>=1 model priced): {n_eval}")
    return out


def opening(a):
    import oddsapi as o
    by, _ = all_matches()
    glm = GoalLineModel("all")
    out, n_eval, n_hist = [], 0, 0
    for slug, rows in by.items():
        lm = LeagueModel(slug)
        for r in rows:
            if r["day"] < a.since or r["close_tl"] is None:
                continue
            ps = prices(lm, glm, r)
            if not ps:
                continue
            n_eval += 1
            best = None
            for ln in (r["close_tl"] - 0.25, r["close_tl"], r["close_tl"] + 0.25):
                if ln <= 0:
                    continue
                d = o.get("/odds/movements", eventId=r["event_id"], bookmaker="Bet365", market="Totals",
                          marketLine=f"{ln:g}")
                op = d.get("opening") if isinstance(d, dict) else None
                if not op or not op.get("home") or not op.get("away"):
                    continue
                gap = abs(float(op["home"]) - float(op["away"]))
                if best is None or gap < best[0]:
                    best = (gap, ln, float(op["home"]), float(op["away"]))
            if not best:
                continue
            n_hist += 1
            _, ln, o_over, o_under = best
            for model, (mat, mu) in ps.items():
                for side, odd in (("over", o_over), ("under", o_under)):
                    e_v = ev(mat, "total", ln, side, odd)
                    if e_v < a.min_ev:
                        continue
                    b = {"market": "total", "line": ln, "side": side, "odds": odd, "stake": 1}
                    profit, outcome = settle_one(b, r["hg"], r["ag"])
                    clv = ""
                    if abs(ln - r["close_tl"]) < 1e-9:
                        close = r["close_over"] if side == "over" else r["close_under"]
                        clv = round(odd / close - 1, 4)
                    out.append({"model": model, "league": slug, "date": r["date"], "home": r["home"],
                                "away": r["away"], "line": ln, "side": side, "odds": odd,
                                "model_mu": round(mu, 3), "fair_odds": round(fair_odds(mat, "total", ln, side), 3),
                                "ev": round(e_v, 4), "result": f"{r['hg']}-{r['ag']}", "outcome": outcome,
                                "profit": profit, "clv": clv})
    print(f"matches evaluated: {n_eval}, with opening line history: {n_hist}, api calls {o.CALLS}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["closing", "opening"])
    ap.add_argument("--min-ev", type=float, default=0.10)
    ap.add_argument("--from", dest="frm", default="2025-08-01")
    ap.add_argument("--since", default="2026-11-06")
    a = ap.parse_args()
    rows = closing(a) if a.mode == "closing" else opening(a)
    os.makedirs(os.path.join(ROOT, "data", "backtest"), exist_ok=True)
    p = os.path.join(ROOT, "data", "backtest", f"goal_line_{a.mode}.csv")
    if rows:
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, list(rows[0]) + (["clv"] if "clv" not in rows[0] else []))
            w.writeheader()
            w.writerows(rows)
    summarize(rows, f"{a.mode} odds, EV >= {a.min_ev:.0%}")


if __name__ == "__main__":
    main()
