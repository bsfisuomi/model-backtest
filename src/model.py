"""The model, ported 1:1 from the Excel sheets (tests/test_excel.py checks it against 'Poland ll').

Strength part (1X2), per league, current season only:
  y = ln(pH/pA) - HA, pH/pA = bet365 closing 1 and 2 normalised without the draw
  strengths = OLS without constant on +1 home / -1 away, each team's last <= 20 matches before the match day
  both teams need >= 10 such matches;  P(home, 2-way) = logistic(HA + s_home - s_away)
  HA: Excel value where the league is in the workbook, else estimated as the constant of the same
  regression on LAST season's matches (falls back to this season when last season has < 60 matches).

Goals part:
  TG_team = average total goals in the team's last 20 league matches (any collected league, so
  promoted/relegated teams are filled from the division they played in). Exactly 20 are required.
  E  = (tg_a + tg_b * |P_home - P_away| [%-points]) * ((TG_home + TG_away) / 2) / tg_norm
  λh = exp(team_a + team_b * P_home) * E / team_norm,  λa likewise; scaled so that λh + λa = E.

Prices: independent Poisson goals (λh, λa); quarter lines split, whole lines push.

Seasons are found from the dates: the newest break of >= 21 days after which the set of teams
changes (promotion/relegation) starts the current season. A winter break keeps the same teams.
"""
import csv
import glob
import json
import math
import os
from bisect import bisect_left
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config", "leagues.json")))
M = CFG["model"]


def _load(path):
    rows = []
    for r in csv.DictReader(open(path, newline="")):
        if r["home_goals"] in ("", None):
            continue
        r["day"] = r["date"][:10]
        r["hg"], r["ag"] = int(float(r["home_goals"])), int(float(r["away_goals"]))
        for k in ("close_1", "close_x", "close_2", "close_tl", "close_over", "close_under"):
            try:
                r[k] = float(r[k]) if r.get(k) else None
            except ValueError:
                r[k] = None
        rows.append(r)
    rows.sort(key=lambda r: r["date"])
    return rows


_ALL = None


def all_matches():
    """{slug: rows} for every collected league, and a per-team index of (date, total goals)."""
    global _ALL
    if _ALL is None:
        by = {}
        team = defaultdict(list)
        seen = set()
        for p in glob.glob(os.path.join(ROOT, "data", "matches", "*.csv")):
            slug = os.path.basename(p)[:-4]
            rows = _load(p)
            by[slug] = rows
            for r in rows:
                if r["event_id"] in seen:
                    continue
                seen.add(r["event_id"])
                for t in (r["home"], r["away"]):
                    team[t].append((r["date"], r["hg"] + r["ag"]))
        for t in team:
            team[t].sort()
        _ALL = (by, dict(team))
    return _ALL


def season_start(rows, min_gap=21):
    """Date (YYYY-MM-DD) where the current season starts, or None when only one season is in the data."""
    days = sorted({r["day"] for r in rows})
    if len(days) < 2:
        return None
    import datetime as dt
    d = [dt.date.fromisoformat(x) for x in days]
    for i in range(len(d) - 1, 0, -1):
        if (d[i] - d[i - 1]).days < min_gap:
            continue
        cut = days[i]
        before = {t for r in rows if days[max(0, i - 60)] <= r["day"] < cut for t in (r["home"], r["away"])}
        after = {t for r in rows if r["day"] >= cut for t in (r["home"], r["away"])}
        if len(before ^ after) >= 2:
            return cut
    return None


def two_way(o1, o2):
    return (1 / o1) / (1 / o1 + 1 / o2)


def fit(train, ha=None):
    """OLS of logit(2-way) on +1/-1 dummies. ha=None -> also estimate the constant (= home advantage)."""
    teams = sorted({t for m in train for t in (m["home_id"] if "home_id" in m else m["home"],
                                                 m["away_id"] if "away_id" in m else m["away"])})
    ix = {t: i for i, t in enumerate(teams)}
    k = len(teams) + (1 if ha is None else 0)
    X = np.zeros((len(train), k))
    y = np.zeros(len(train))
    for j, m in enumerate(train):
        h = m["home_id"] if "home_id" in m else m["home"]
        a = m["away_id"] if "away_id" in m else m["away"]
        X[j, ix[h]] = 1
        X[j, ix[a]] = -1
        p = two_way(m["close_1"], m["close_2"])
        y[j] = math.log(p / (1 - p)) - (ha or 0)
        if ha is None:
            X[j, -1] = 1
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    s = {t: float(b[ix[t]]) for t in teams}
    return (s, float(b[-1])) if ha is None else s


fit_strengths = fit  # name used by tests


class LeagueModel:
    def __init__(self, slug):
        self.slug = slug
        self.lg = CFG["leagues"].get(slug, {})
        by, self.team_idx = all_matches()
        self.rows = by.get(slug, [])
        self.start = season_start(self.rows)
        self.cur = [r for r in self.rows if (self.start is None or r["day"] >= self.start)]
        self.prev = [r for r in self.rows if self.start is not None and r["day"] < self.start]
        self.ha, self.ha_source = self._ha()
        self._cache = {}

    def _ha(self):
        """Fixed HA from config (Excel leagues), else the shared constant of the regression on this season
        plus the whole previous season, with a separate strength per team and season."""
        if self.lg.get("ha") is not None:
            return self.lg["ha"], self.lg.get("ha_source", "config")
        tr = []
        for tag, rows in (("cur", self.cur), ("prev", self.prev)):
            for r in rows:
                if r["close_1"] and r["close_2"]:
                    tr.append({"home_id": (r["home"], tag), "away_id": (r["away"], tag),
                               "close_1": r["close_1"], "close_2": r["close_2"]})
        if len(tr) >= 60:
            _, c = fit(tr, None)
            n_prev = sum(1 for x in tr if x["home_id"][1] == "prev")
            return c, f"estimated (this + last season, {len(tr)} matches, {n_prev} last season)"
        return None, "too little data"

    def strengths(self, before_day):
        if before_day not in self._cache:
            cur = [r for r in self.cur if r["close_1"] and r["close_2"] and r["day"] < before_day]
            per = defaultdict(list)
            for r in cur:
                per[r["home"]].append(r)
                per[r["away"]].append(r)
            keep = {}
            for lst in per.values():
                for r in lst[-M["rating_max_matches"]:]:
                    keep[r["event_id"]] = r
            s = fit(list(keep.values()), self.ha) if keep else {}
            n = {t: min(len(v), M["rating_max_matches"]) for t, v in per.items()}
            self._cache[before_day] = (s, n)
        return self._cache[before_day]

    def team_goals_avg(self, team, before_day):
        lst = self.team_idx.get(team, [])
        i = bisect_left(lst, (before_day,))
        last = lst[max(0, i - M["goals_matches"]):i]
        if len(last) < M["goals_matches"]:
            return None, len(last)
        return sum(g for _, g in last) / len(last), len(last)

    def predict(self, home, away, day, tg_source="goals"):
        """tg_source "goals": team TG = average real total goals (the Excel model);
        "goal_line": team TG = average closing goal-line mu (GoalLineModel.team_line)."""
        out = {"ha": self.ha}
        if self.ha is None:
            out["reason"] = "no home advantage (too little data)"
            return out
        s, n = self.strengths(day)
        out.update(n_home=n.get(home, 0), n_away=n.get(away, 0))
        if out["n_home"] < M["rating_min_matches"] or out["n_away"] < M["rating_min_matches"]:
            out["reason"] = "too few rating matches"
            return out
        ph = 1 / (1 + math.exp(-(self.ha + s[home] - s[away])))
        pa = 1 - ph
        out.update(p_home=ph)
        if tg_source == "goal_line":
            glm = GoalLineModel(self.slug)
            tgh, gh = glm.team_line(home, day)
            tga, ga = glm.team_line(away, day)
        else:
            tgh, gh = self.team_goals_avg(home, day)
            tga, ga = self.team_goals_avg(away, day)
        out.update(g_home=gh, g_away=ga)
        if tgh is None or tga is None:
            out["reason"] = "too few goal matches" if tg_source == "goals" else "too few closing goal lines"
            return out
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
    if round(line * 4) % 2 == 1:
        return [line - 0.25, line + 0.25]
    return [line]


def _settle(margin):
    if margin > 1e-9:
        return 1.0, 0.0
    if margin < -1e-9:
        return 0.0, 1.0
    return 0.0, 0.0


def outcome_probs(mat, market, line, side):
    """Expected (win, loss) stake fractions. market 'ah' (line = home handicap; side home/away) or 'total'."""
    n = mat.shape[0]
    i, j = np.meshgrid(range(n), range(n), indexing="ij")
    w = l = 0.0
    parts = _split(line)
    for part in parts:
        if market == "ah":
            m = (i - j) + part if side == "home" else (j - i) - part
        else:
            m = (i + j) - part if side == "over" else part - (i + j)
        w += mat[m > 1e-9].sum() / len(parts)
        l += mat[m < -1e-9].sum() / len(parts)
    return float(w), float(l)


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


# ---- Goal Line model: team goal ratings from bet365 closing goal lines -----------------------------
def _total_probs(mu, line, n=20):
    """(win, loss) stake fractions of OVER `line` when total goals ~ Poisson(mu)."""
    pk = [math.exp(-mu) * mu ** k / math.factorial(k) for k in range(n)]
    w = l = 0.0
    parts = _split(line)
    for part in parts:
        for k, p in enumerate(pk):
            m = k - part
            if m > 1e-9:
                w += p / len(parts)
            elif m < -1e-9:
                l += p / len(parts)
    return w, l


def implied_mu(line, over, under):
    """Expected total goals implied by a closing goal line: the Poisson mean whose no-push over share
    equals the de-vigged over probability."""
    p = (1 / over) / (1 / over + 1 / under)
    lo, hi = 0.05, 12.0
    for _ in range(60):
        mid = (lo + hi) / 2
        w, l = _total_probs(mid, line)
        if w / (w + l) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class GoalLineModel:
    """Total goals from bet365 closing goal lines.

    Every closing main goal line is turned into the expected total mu at which it is 50/50
    (implied_mu, margin removed). A team's goal line = the average mu of its last goal_line_max_matches (20)
    matches with a closing goal line, in any collected league (so last season / the previous division
    fill up when this season has fewer). At least goal_line_min_matches (10) are required.
    Match: mu = (team_home + team_away) / 2 - a team at 2.5 and a team at 3.0 give 2.75.
    Totals are priced with Poisson(mu)."""

    _idx = None

    @classmethod
    def index(cls):
        if cls._idx is None:
            by, _ = all_matches()
            team = defaultdict(list)
            seen = set()
            for rows in by.values():
                for r in rows:
                    if r["event_id"] in seen or r["close_tl"] is None or not r["close_over"] or not r["close_under"]:
                        continue
                    seen.add(r["event_id"])
                    mu = implied_mu(r["close_tl"], r["close_over"], r["close_under"])
                    for t in (r["home"], r["away"]):
                        team[t].append((r["date"], mu))
            for t in team:
                team[t].sort()
            cls._idx = dict(team)
        return cls._idx

    def __init__(self, slug):
        self.slug = slug
        self.idx = self.index()

    def team_line(self, team, before_day):
        lst = self.idx.get(team, [])
        i = bisect_left(lst, (before_day,))
        last = lst[max(0, i - M.get("goal_line_max_matches", 20)):i]
        if len(last) < M.get("goal_line_min_matches", 10):
            return None, len(last)
        return sum(m for _, m in last) / len(last), len(last)

    def predict(self, home, away, day):
        th, nh = self.team_line(home, day)
        ta, na = self.team_line(away, day)
        out = {"n_home": nh, "n_away": na}
        if th is None or ta is None:
            out["reason"] = "too few closing goal lines"
            return out
        out.update(team_home=th, team_away=ta, mu=(th + ta) / 2)
        return out


def fair_line(mu):
    """The quarter line closest to 50/50 for Poisson(mu) total goals."""
    best = None
    for k in range(2, 33):
        ln = k / 4
        w, l = _total_probs(mu, ln)
        d = abs(w - l)
        if best is None or d < best[0]:
            best = (d, ln)
    return best[1]


def total_matrix(mu, n=20):
    """Score matrix stand-in for totals pricing: all mass on (k, 0) so total = k."""
    mat = np.zeros((n, n))
    for k in range(n):
        mat[k, 0] = math.exp(-mu) * mu ** k / math.factorial(k)
    return mat
