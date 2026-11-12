# model-backtest

Pappersspel i verkligheten för 1X2-/styrkemodellen och Total Goals-modellen. Det gäller alla ligor där bet365 har odds: europeiska ligor plus de ligor som bet365 hade ute vid skanningen 2026-11-12, inklusive Excel-ligorna. Cuper, playoff och landslag ingår inte.

Modellen räknar ut förväntade mål och sätter egna odds på bet365:s **öppningslinor**, det vill säga den första huvudlinan i asiatiskt handikapp (Spread, **bara 50/50-linor** där båda sidor öppnar mellan 1,70 och 2,20) och i totaler. Båda sidor av varje lina jämförs. Spel med **EV ≥ 10 %** bokförs till öppningspriset, och modellen räknar då bara med data som fanns dagen linan öppnade. Allt körs **manuellt**, inget schema.

Data och odds: **Odds-API.io** med bet365. Därifrån kommer historiska stängningsodds på 1X2 och resultat för lagstyrkorna, och aktuella AH- och totalodds för skanningen.

## Modellen (portad från `1X2_med_ny_metod.xlsx`)

| Del | Regel |
| --- | --- |
| Styrkor | y = ln(pH/pA) − HA, där pH och pA är bet365-stängning 1 och 2 normaliserade utan X. Lagstyrkorna skattas med OLS utan konstant (+1 hemma, −1 borta). |
| Fönster, styrkor | Varje lags senaste ≤ 20 matcher **denna säsong** före matchdagen. **Minst 10** krävs. |
| P(hemma, 2-väg) | logistic(HA + s_hemma − s_borta) |
| HA | Excel-värdet för ligorna i Excel. Övriga ligor: konstanten i samma regression på **förra säsongens** stängningsodds (minst 60 matcher, annars denna säsong). |
| Målsnitt per lag | Snitt totala mål i lagets **senaste 20** ligamatcher, i alla insamlade ligor. Uppflyttade och nedflyttade lag fylls alltså på från serien de spelade i förra säsongen. **Exakt 20** krävs. |
| Förväntade mål E | (3,2811 + 0,008833·\|P hemma − P borta\| i %-enheter) · ((TG hemma + TG borta)/2) / 3,6247 |
| Lagens mål | λ = exp(−0,3104 + 1,1921·p) · E / 2,75, skalat så att λh + λa = E |
| Priser | Oberoende Poisson(λh), Poisson(λa). Kvartslinjer delas i två halvor, heltalslinjer ger push. |
| Säsong | Hittas i datumen: den senaste paus ≥ 21 dagar varefter laguppsättningen ändras. Vinteruppehåll ändrar inte lagen. |

`tests/test_excel.py` kontrollerar att koden ger exakt samma siffror som Excel-fliken *Poland ll*.

## Körning

```bash
export ODDSAPIIO_KEY=...
python3 src/discover.py --from 2026-11-03 --to 2026-11-11   # vilka ligor bet365 hade under en period -> config/leagues.json
python3 src/collect.py                                     # stängningsodds 1X2 + resultat (första gången ~540 dagar bakåt)
python3 src/scan.py --days 7 --backfill-days 4            # bet365 öppningslinor AH/totaler, bokför spel med EV >= 10 %
python3 src/settle.py                                      # avräkna spel, stängningsodds på samma linje och CLV
python3 tests/test_excel.py
```

## Filer

| Fil | Innehåll |
| --- | --- |
| `config/leagues.json` | Ligor (Odds-API-slug, HA för Excel-ligorna), modellkonstanter, EV-gräns |
| `data/matches/<liga>.csv` | Avgjorda matcher: resultat och bet365-stängning 1X2 |
| `data/prices/latest.csv` | Modellen per kommande match: P 2-väg, E, λ, fair AH 0 och 2,5 mål |
| `data/scans/<tid>.csv` | Öppnings- och senaste huvudlina per match och marknad, med fair odds och EV |
| `data/bets.csv` | Bokförda spel: öppningslina, odds och tid, fair odds, EV, modellvärden, senaste lina och odds, resultat, vinst, stängningsodds på samma lina och CLV |

## Kända begränsningar

- Odds-API:s historik börjar omkring maj 2025. Ligor som bytt slug, eller saknar förra säsongen där, har bara denna säsong. HA skattas då på denna säsong, och lagen når 20 matcher för målsnittet först efter 20 omgångar.
- Ligor med färre än 60 matcher med odds får ingen HA och prissätts inte. Det gäller ofta damligor och små serier.
- Odds-API sparar linehistorik (och därmed öppningslinan) bara några dagar. **Skanna minst var 3–4:e dag**, annars missas öppningslinor för matcher som hunnit spelas. `--backfill-days 4` plockar upp nyss spelade matcher.
- Öppningslinor finns bara för huvudlinan, inte för hela stegen av alternativa linjer.
