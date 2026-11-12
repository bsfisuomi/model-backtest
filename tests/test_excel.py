"""The Python model must reproduce the Excel sheet 'Poland ll' (1X2_med_ny_metod.xlsx).
Run: python3 tests/test_excel.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from model import M, fair_odds, fit_strengths, outcome_probs, score_matrix  # noqa: E402

fx = json.load(open(os.path.join(os.path.dirname(__file__), "excel_poland2_fixture.json")))
train = [{"home_id": h, "away_id": a, "close_1": o1, "close_2": o2} for h, a, o1, o2 in fx["matches"]]
s = fit_strengths(train, fx["ha"])
ph = 1 / (1 + math.exp(-(fx["ha"] + s[fx["home"]] - s[fx["away"]])))
pa = 1 - ph
E = (M["tg_a"] + M["tg_b"] * abs(ph - pa) * 100) * ((fx["tg"][fx["home"]] + fx["tg"][fx["away"]]) / 2) / M["tg_norm"]
lh = math.exp(M["team_a"] + M["team_b"] * ph) * E / M["team_norm"]
la = math.exp(M["team_a"] + M["team_b"] * pa) * E / M["team_norm"]
ex = fx["expect"]
for name, got, want in [("P home %", ph * 100, ex["p_home_pct"]), ("exp goals", E, ex["exp_goals"]),
                        ("team goals home", lh, ex["team_home"]), ("team goals away", la, ex["team_away"])]:
    assert abs(got - want) < 1e-9, (name, got, want)
    print(f"ok  {name:16s} {got:.6f}")

# pricing sanity: AH 0 fair odds == DNB, quarter line between neighbours, over+under consistent
mat = score_matrix(lh, la)
w, l = outcome_probs(mat, "ah", 0.0, "home")
assert abs(fair_odds(mat, "ah", 0.0, "home") - (1 + l / w)) < 1e-12
assert fair_odds(mat, "ah", -0.5, "home") > fair_odds(mat, "ah", -0.25, "home") > fair_odds(mat, "ah", 0.0, "home")
po = 1 / fair_odds(mat, "total", 2.5, "over") + 1 / fair_odds(mat, "total", 2.5, "under")
assert abs(po - 1) < 1e-9
print("ok  pricing checks")
