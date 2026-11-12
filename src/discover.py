"""Build config/leagues.json: every league (not cups/youth cups/friendlies/UEFA) in the chosen regions that had
bet365 closing lines in a recent window, plus the leagues from the original Excel model.

  python3 src/discover.py [--from 2026-11-03] [--to 2026-11-11] [--europe-only]

Existing entries keep their settings (e.g. a fixed HA); new ones get ha = null (estimated from last season).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
import oddsapi as o  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "leagues.json")

EUROPE = ["Albania", "Andorra", "Armenia", "Austria", "Azerbaijan", "Belarus", "Belgium", "Bosnia", "Bulgaria",
          "Croatia", "Cyprus", "Czech", "Denmark", "England", "Estonia", "Faroe", "Finland", "France", "Georgia",
          "Germany", "Gibraltar", "Greece", "Hungary", "Iceland", "Ireland", "Israel", "Italy", "Kazakhstan",
          "Kosovo", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova", "Montenegro",
          "Netherlands", "North Macedonia", "Northern Ireland", "Norway", "Poland", "Portugal", "Romania", "Russia",
          "San Marino", "Scotland", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "Turkey",
          "Türkiye", "Ukraine", "Wales"]
NOT_A_LEAGUE = re.compile(r"cup|cupen|pokal|coppa|copa|coupe|taça|taca|beker|kupa|kupok|kauss|puchar|pohár|pohar|"
                          r"super ?cup|friendl|play-?off|qualif|trophy|shield|knockout|relegation|promotion|baraj|"
                          r"youth league|u1[0-9]|u2[0-3]|uefa", re.I)

# Leagues from the Excel workbook, with their fixed home advantage.
EXCEL = {
    "poland-ii-liga": 0.3421, "poland-iii-liga-group-1": 0.3421, "poland-iii-liga-group-2": 0.3421,
    "poland-iii-liga-group-3": 0.3421, "poland-iii-liga-group-4": 0.3421, "republic-of-korea-k-league-2": 0.1886,
    "japan-jleague": 0.2188, "latvia-virsliga": 0.1879, "denmark-3rd-division": 0.2611,
    "czechia-cfl-group-a": 0.3421, "czechia-cfl-group-b": 0.3421, "czechia-msfl": 0.4274,
    "saudi-arabia-saudi-professional-league": 0.2375, "sweden-ettan-norra": 0.315, "sweden-ettan-sodra": 0.315,
    "peru-primera-division-clausura": 0.82,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-11-03")
    ap.add_argument("--to", default="2026-11-11")
    a = ap.parse_args()
    cfg = json.load(open(CFG))
    leagues = o.get("/leagues", sport="football") or []
    names = {l["slug"]: l["name"] for l in leagues}
    cand = [l["slug"] for l in leagues if any(l["name"].startswith(c) for c in EUROPE)
            and not NOT_A_LEAGUE.search(l["name"])]
    cnt = Counter()
    for i in range(0, len(cand), 10):
        skip = 0
        while True:
            d = o.get("/historical/closing-lines", sport="football", leagues=",".join(cand[i:i + 10]),
                      markets="ML", bookmakers="Bet365", limit=100, skip=skip,
                      **{"from": f"{a.frm}T00:00:00Z", "to": f"{a.to}T00:00:00Z"})
            if not isinstance(d, list):
                break
            for x in d:
                if x.get("bookmakers", {}).get("Bet365"):
                    cnt[x["league"]["slug"]] += 1
            if len(d) < 100:
                break
            skip += 100
    out = cfg.get("leagues", {})
    for slug in list(cnt) + list(EXCEL):
        e = out.get(slug, {})
        e.setdefault("name", names.get(slug, slug))
        if slug in EXCEL:
            e["ha"] = EXCEL[slug]
            e["ha_source"] = "excel"
        else:
            e.setdefault("ha", None)
        e["bet365_last_window"] = cnt.get(slug, 0)
        out[slug] = e
    cfg["leagues"] = dict(sorted(out.items()))
    cfg["discovered"] = {"from": a.frm, "to": a.to, "leagues_with_bet365": len(cnt)}
    json.dump(cfg, open(CFG, "w"), ensure_ascii=False, indent=1)
    print(f"{len(cnt)} leagues with bet365 in window, {len(out)} in config, calls {o.CALLS}")


if __name__ == "__main__":
    main()
