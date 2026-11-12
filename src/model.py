"""The model, ported 1:1 from the Excel sheets (verified against 'Poland ll').

1X2 / strength part (needs >= rating_min_matches current-season matches per team):
  y = ln(pH/pA) - HA, pH/pA = bet365 closing 1 and 2, normalised without the draw
  strengths s = OLS without constant on +1 home / -1 away (each team's last <= 20 matches)
  P(home, 2-way) = logistic(HA + s_home - s_away)

Goals part (needs exactly goals_matches = 20 matches per team; filled from last season):
  TG_team = average total goals in the team's last 20 matches
  E  = (tg_a + tg_b * |P_home - P_away| [in %-points]) * ((TG_home + TG_away) / 2) / tg_norm
  λh = exp(team_a + team_b * P_home) * E / team_norm,  λa likewise with P_away
  λh and λa are then scaled so that λh + λa = E (the Excel sum differs by < 1 %).

Prices for Asian lines come from independent Poisson goals with λh, λa.
"""
import csv
import json
import math
import os
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config", "leagues.json")))
M = CFG["model"]


def load_matches(key):
    p = os.path.join(ROOT, "data", "matches", f"{key}.csv")
    rows = []
    if not os.path.exists(p):
        return rows
    seen = set()
    for r in csv.DictReader(open(p, newline="")):
        if r["match_id"] in seen and r["scope"] != "current":
            continue
        seen.add(r["match_id"])
        r["day"] = r["kickoff_utc"][:10]
        r["hg"], r["ag"] = int(r["home_goals"]), int(r["away_goals"])
        for k in ("close_1", "close_x", "close_2"):
            r[k] = float(r[k]) if r[k] else None
        rows.append(r)
    rows.sort(key=lambda r: r["kickoff_utc"])
    return rows


def two_way(o1, o2):
    return (1 / o1) / (1 / o1 + 1 / o2)


def fit_strengths(train, ha):
    teams = sorted({t for m in train for t in (m["home_id"], m["away_id"])})
    ix = {t: i for i, t in enumerate(teams)}
    X = np.zeros((len(train), len(teams)))
    y = np.zeros(len(train))
    for k, m in enumerate(train):
        X[k, ix[m["home_id"]]] = 1
        X[k, ix[m["away_id"]]] = -1
        p = two_way(m["close_1"], m["close_2"])
        y[k] = math.log(p / (1 - p)) - ha
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {t: float(b[ix[t]]) for t in teams}


class LeagueModel:
    def __init__(self, key):
        self.key = key
        self.lg = CFG["leagues"][key]
        self.ha = self.lg["ha"]
        self.rows = load_matches(key)

    # ---- strengths -------------------------------------------------------
    def strengths(self, before_day):
        cur = [r for r in self.rows if r["scope"] == "current" and r["close_1"] and r["day"] < before_day]
        per = defaultdict(list)
        for r in cur:
            per[r["home_id"]].append(r)
            per[r["away_id"]].append(r)
        keep = {}
        for lst in per.values():
            for r in lst[-M["rating_max_matches"]:]:
                keep[r["match_id"]] = r
        if not keep:
            return {}, {}
        s = fit_strengths(list(keep.values()), self.ha)
        n = {t: min(len(v), M["rating_max_matches"]) for t, v in per.items()}
        return s, n

    # ---- goals -----------------------------------------------------------
    def team_goals_avg(self, team_id, before_day):
        lst = [r for r in self.rows if r["day"] < before_day and team_id in (r["home_id"], r["away_id"])]
        lst = lst[-M["goals_matches"]:]
        if len(lst) < M["goals_matches"]:
            return None, len(lst)
        return sum(r["hg"] + r["ag"] for r in lst) / len(lst), len(lst)

    # ---- prediction ------------------------------------------------------
    def predict(self, home_id, away_id, day, _cache={}):
        ck = (self.key, day)
        if ck not in _cache:
            _cache[ck] = self.strengths(day)
        s, n = _cache[ck]
        home_id, away_id = str(home_id), str(away_id)
        out = {"n_home": n.get(home_id, 0), "n_away": n.get(away_id, 0)}
        if out["n_home"] < M["rating_min_matches"] or out["n_away"] < M["rating_min_matches"]:
            out["reason"] = "too few rating matches"
            return out
        L = self.ha + s[home_id] - s[away_id]
        ph = 1 / (1 + math.exp(-L))
        out.update(p_home=ph, s_home=s[home_id], s_away=s[away_id])
        tgh, gn_h = self.team_goals_avg(home_id, day)
        tga, gn_a = self.team_goals_avg(away_id, day)
        out.update(g_home=gn_h, g_away=gn_a)
        if tgh is None or tga is None:
            out["reason"] = "too few goal matches"
            return out
        pa = 1 - ph
        E = (M["tg_a"] + M["tg_b"] * abs(ph - pa) * 100) * ((tgh + tga) / 2) / M["tg_norm"]
        lh = math.exp(M["team_a"] + M["team_b"] * ph) * E / M["team_norm"]
        la = math.exp(M["team_a"] + M["team_b"] * pa) * E / M["team_norm"]
        f = E / (lh + la)
        out.update(tg_home=tgh, tg_away=tga, exp_goals=E, lam_home=lh * f, lam_away=la * f)
        return out


# ---- pricing ---------------------------------------------------------------
def score_matrix(lh, la, n=15):
    ph = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(n)]
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(n)]
    return np.outer(ph, pa)


def _split(line):
    """Quarter lines split in two halves; others are a single part."""
    q = round(line * 4) % 2
    if q == 1:
        return [line - 0.25, line + 0.25]
    return [line]


def _settle(margin):
    """margin > 0 win, = 0 push, < 0 loss -> (win fraction, loss fraction)."""
    if margin > 1e-9:
        return 1.0, 0.0
    if margin < -1e-9:
        return 0.0, 1.0
    return 0.0, 0.0


def outcome_probs(mat, market, line, side):
    """Expected (win, loss) fractions of the stake. market 'ah' (line is the home handicap, side home/away)
    or 'total' (side over/under)."""
    n = mat.shape[0]
    w = l = 0.0
    parts = _split(line)
    for i in range(n):
        for j in range(n):
            p = mat[i, j]
            if p < 1e-12:
                continue
            for part in parts:
                if market == "ah":
                    m = (i - j) + part if side == "home" else (j - i) - part
                else:
                    m = (i + j) - part if side == "over" else part - (i + j)
                a, b = _settle(m)
                w += p * a / len(parts)
                l += p * b / len(parts)
    return w, l


def fair_odds(mat, market, line, side):
    w, l = outcome_probs(mat, market, line, side)
    return 1 + l / w if w > 0 else None


def ev(mat, market, line, side, odds):
    w, l = outcome_probs(mat, market, line, side)
    return w * (odds - 1) - l


def lines(spec):
    a, b, st = spec
    k = int(round((b - a) / st))
    return [round(a + i * st, 2) for i in range(k + 1)]
