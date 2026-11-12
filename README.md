# model-backtest

Pappersspel i verkligheten för två modeller som hänger ihop:

- **1X2-/styrkemodellen**: lagstyrkor från bet365:s stängningsodds 1X2.
- **Total Goals-modellen**: förväntade mål från styrkorna och lagens målsnitt.

Utifrån förväntade mål sätter modellen egna odds på **asiatiska linjer**: handikapp −3 till +3 och totaler 0,5–6, i steg om 0,25. Oddsen jämförs med bookens. Spel med **EV ≥ 10 %** bokförs.

Allt körs **manuellt**, inget schema.

## Modellen (portad från `1X2_med_ny_metod.xlsx`)

| Del | Regel |
| --- | --- |
| Styrkor | y = ln(pH/pA) − HA. pH och pA är bet365-stängning 1 och 2, normaliserade utan X. Lagstyrkorna skattas med OLS utan konstant (+1 hemma, −1 borta). |
| Fönster, styrkor | Varje lags senaste ≤ 20 matcher denna säsong, före matchdagen. **Minst 10** krävs. |
| P(hemma, 2-väg) | logistic(HA + s_hemma − s_borta). HA är fast per liga, se `config/leagues.json`. |
| Målsnitt per lag | Snitt totala mål i lagets **senaste 20** matcher. Har säsongen färre fylls det på med slutet av förra säsongen. Uppflyttade och nedflyttade lag fylls på med sina ligamatcher från andra serier. |
| Förväntade mål E | (3,2811 + 0,008833·\|P hemma − P borta\| i %-enheter) · ((TG hemma + TG borta)/2) / 3,6247 |
| Lagens mål | λ = exp(−0,3104 + 1,1921·p) · E / 2,75, skalat så att λh + λa = E |
| Priser | Oberoende Poisson(λh), Poisson(λa). Kvartslinjer delas i två halvor, heltalslinjer ger push. |

`tests/test_excel.py` kontrollerar att koden ger exakt samma siffror som Excel-fliken *Poland ll*.

## Körning

```bash
export SPORTSAPIPRO_KEY=...           # SportsAPI Pro (resultat + 1X2-stängning)
python3 src/collect.py                # uppdatera matchdata för alla ligor (eller: python3 src/collect.py poland-2)
python3 src/scan.py --days 7          # prissätt kommande matcher, jämför med booken, bokför spel med EV >= 10 %
python3 src/settle.py                 # avräkna spel vars matcher är färdigspelade
python3 tests/test_excel.py           # modellen = Excel
```

## Filer

| Fil | Innehåll |
| --- | --- |
| `config/leagues.json` | Ligor (SofaScore-turnerings-id, HA), modellkonstanter, EV-gräns, linjer |
| `data/matches/<liga>.csv` | `current` = säsongen med 1X2-odds, `previous` = förra säsongen (bara resultat), `team` = uppflyttade/nedflyttade lags matcher i andra serier |
| `data/prices/latest.csv` | Modellens fair odds för varje kommande match och linje |
| `data/scans/<tid>.csv` | Varje bookodds som jämförts i en skanning, med fair odds och EV |
| `data/bets.csv` | Bokförda spel: odds, fair odds, EV, modellvärden, senast sedda odds, resultat, vinst och CLV |

## Odds från booken

`src/books.py` har en adapter per källa:

- `sportsapi-bet365`: bet365 via SportsAPI Pro (huvudlinjen i AH och totaler på halva mål). Finns bara i en del ligor, t.ex. J1, K League 2 och Peru.
- `bet365`: platshållare för det egna bet365-API:et, kopplas in när det finns.

## Kända luckor

- I SportsAPI Pro är bet365:s öppnings- och stängningspris identiska för II liga. Det priset används som stängning.
- Sweden Ettan Norra/Södra: API:et listar ingen säsong 2026. Det som ligger som `current` är säsong 2025, och ligorna ger därför inga meningsfulla priser förrän säsongen finns i API:et (eller sätts med `current_season` i configen).
- I många ligor ger bet365-flödet i SportsAPI Pro bara 1X2. Där bokförs inga spel förrän bet365-adaptern är inkopplad.
