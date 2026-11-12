"""Client for Odds-API.io v3 (bet365 lines, events, closing lines). Key from env ODDSAPIIO_KEY."""
import json
import os
import subprocess
import time
import urllib.parse

BASE = "https://api.odds-api.io/v3"
CALLS = 0


def _key():
    k = os.environ.get("ODDSAPIIO_KEY")
    if not k:
        p = os.path.expanduser("~/.secrets/oddsapiio_key")
        if os.path.exists(p):
            k = open(p).read().strip()
    if not k:
        raise SystemExit("Set ODDSAPIIO_KEY")
    return k


def get(path, retries=4, **params):
    global CALLS
    params["apiKey"] = _key()
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for i in range(retries):
        CALLS += 1
        r = subprocess.run(["curl", "-sS", "--compressed", "-m", "60", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        if code == "429":
            time.sleep(20 * (i + 1))
            continue
        if code == "404":
            return None
        try:
            return json.loads(body)
        except Exception:
            time.sleep(2 * (i + 1))
    return None
