# WFO LIX Heat Verification

A map-first retrospective verification app for **WFO New Orleans/Baton Rouge (LIX)** heat products.

The app answers two different questions without conflating them:

1. **Did the gridded URMA analysis meet the product criterion anywhere in each warned forecast zone?**
2. **Which actual ASOS/AWOS observations inside the zone supported verification?**

## LIX criteria used

| Product | VTEC | Verification threshold |
|---|---|---:|
| Heat Advisory | `HT.Y` | Heat Index **≥ 108°F** |
| Extreme Heat Warning | `XH.W` | Heat Index **≥ 113°F** |
| Legacy Excessive Heat Warning | `EH.W` | Heat Index **≥ 113°F** |

The legacy `EH.W` alias is retained so older summers remain useful after the NWS heat-hazard VTEC change.

## What it does

- User chooses a **month** in the web UI.
- Pulls LIX heat events from the IEM VTEC archive and preserves each zone's event-valid period.
- Uses the **latest official NWS public forecast-zone shapefile** (currently the `2026-04-16` set) and filters it to `CWA=LIX`.
- Retrieves hourly **URMA 2-m temperature and 2-m dewpoint** only for product-valid hours.
- Calculates NWS heat index on every URMA grid point in the LIX domain.
- Finds the maximum URMA heat index in each warned forecast zone, with time and location.
- Pulls archived **LA/MS ASOS/AWOS** observations and independently calculates observed heat index.
- Shows verification on an interactive Leaflet map with separate **URMA** and **Observed** modes.
- Exports a zone-by-zone CSV.
- Flags missing URMA hours instead of silently replacing them with RTMA or another dataset.

## Efficient URMA retrieval

A full CONUS URMA analysis GRIB2 is large. The app does **not** download the whole file for every hour.

NCEP's URMA inventory places:

- record 3: `TMP:2 m above ground`
- record 4: `DPT:2 m above ground`

The provider walks GRIB2 section-0 headers with HTTP byte-range requests, extracts only records 3 and 4, decodes those records with `cfgrib/eccodes`, subsets to the LIX bounding box, then caches a small compressed NPZ for repeat runs.

If a configured server ignores byte ranges, the code falls back to one full-file response for that hour rather than repeatedly downloading it.

## Data sources

- **Forecast zones:** NWS GIS public forecast zones, current version pinned at build time to `z_16ap26.zip` (valid 16 April 2026).
- **URMA:** NCEP NOMADS CONUS 2.5-km URMA Western Expansion analysis.
- **Heat products:** Iowa Environmental Mesonet VTEC archive, WFO `LIX`.
- **Observed support:** Iowa Environmental Mesonet ASOS/AWOS archive for `LA_ASOS` and `MS_ASOS`.

URMA is a gridded analysis constrained by observations; it is not itself a raw observation. The UI deliberately reports URMA and station evidence separately.

## Run locally

Python 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Docker

```bash
docker build -t lix-heat-verification .
docker run --rm -p 8000:8000 lix-heat-verification
```

A `render.yaml` is included for a straightforward Render Docker deployment.

## API

- `GET /api/zones` — current LIX forecast-zone GeoJSON
- `POST /api/jobs` with `{ "month": "2026-08" }` — start a monthly verification
- `GET /api/jobs/{id}` — progress/result
- `GET /api/jobs/{id}/csv` — CSV export after completion
- `GET /api/config` — criteria and zone version
- `GET /api/health` — health check

Monthly verification runs as an in-process job so the browser can display progress while URMA hours are retrieved and decoded.

## URMA archive behavior

Public NOMADS is an operational distribution service, not a guaranteed permanent archive. The app accepts a comma-separated `URMA_BASES` environment variable and tries each base in order. If an hour is not found, it is reported as missing. **The app never swaps in RTMA and calls it URMA.**

## Verification definition

For each warned zone and its VTEC-valid interval:

- **URMA verified:** maximum calculated URMA heat index inside the current LIX forecast-zone polygon is greater than or equal to the threshold.
- **Observed verified:** at least one archived ASOS/AWOS observation physically inside that polygon reaches the threshold.
- **No station:** no usable LA/MS ASOS/AWOS value was available inside the polygon during the valid period; this is shown as unavailable, not a miss.

Because the request is to use the **latest** LIX forecast-zone shapefile, old events are intentionally evaluated against today's zone geometry. If an old UGC no longer exists in the current shapefile, the result marks its geometry unavailable.

## Important interpretation note

A heat product can reasonably verify in the gridded analysis even when no ASOS/AWOS station in that zone reaches the threshold. That is why both layers are shown: URMA provides spatial coverage between stations; observations provide traceable station evidence.
