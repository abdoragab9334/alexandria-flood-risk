# Alexandria Coastal Flood Risk & Emergency Response API

Spatial data engineering project that pre-computes a flood vulnerability
index across a hexagonal grid covering coastal Alexandria, Egypt, and
serves it through a fast lookup API — designed around the emergency
dispatch use case (an ambulance transmits GPS coordinates and needs an
immediate risk assessment for that location).

## Problem

Alexandria is a low-lying coastal city vulnerable to winter storm surges
and flooding. During an emergency, real-time spatial intersection queries
against complex polygon boundaries are too slow for dispatch decisions.
This project pre-computes risk per grid cell offline, so a live lookup at
request time is just a dictionary lookup by cell ID.

## Data

- **Uber H3 hexagonal grid** (Resolution 7, ~5.16 km² per cell) covering a
  manually defined coastal bounding box — **not** the OSM administrative
  boundary for "Alexandria, Egypt", which returns the full governorate
  extending ~137km south into the desert. The bounding box was chosen to
  match the actual urban coastal strip.
- **Elevation** for each cell centroid, from the free Open-Meteo Elevation
  API (Copernicus DEM GLO-90, 90m resolution), fetched in batches of 100
  points to respect the API's rate limits.
- **94 real hospitals/clinics**, fetched from OpenStreetMap via OSMnx,
  reduced to centroids using a metric CRS (EPSG:32636).

## Methodology

1. **Grid resolution trade-off**: started at H3 resolution 8 (~4,800
   cells), which required ~49 batched API calls and repeatedly hit
   Open-Meteo's per-minute and per-hour rate limits. Switched to
   resolution 7 (~180 cells in the coastal bounding box) to keep the
   pipeline reliably within free-tier API limits — a real constraint that
   shaped the design, not an arbitrary choice.
2. **Distance calculation**: nearest-hospital distance per cell computed
   in PostGIS using `ST_Distance` after projecting to EPSG:32636 (UTM
   Zone 36N), the same approach used in the companion Cairo project.
3. **Composite vulnerability score**: originally modeled as
   `0.7 × elevation_risk + 0.3 × distance_risk` with fixed thresholds
   (0m/15m for elevation, 5000m for distance). This produced a degenerate
   result — 168 of 184 cells classified "High" — because 84% of the
   coastal strip sits at or below sea level, so a fixed 0m/15m cutoff
   couldn't discriminate between cells. The fix: both factors are now
   **min-max normalized against the actual data distribution** (same
   normalization approach as the Cairo project) rather than using
   externally-assumed thresholds, and the weighting was rebalanced to
   50/50 since elevation carries much less discriminating power in this
   flat coastal city than it would elsewhere.
4. **Result**: a more meaningful spread — 87 High, 92 Medium, 5 Low —
   driven primarily by distance to the nearest hospital, which is the
   factor that actually varies meaningfully across this terrain.

## Architecture

```
OpenStreetMap (OSMnx)          Open-Meteo Elevation API
   │ hospitals                     │ elevation per cell
   ▼                               ▼
        PostGIS (PostgreSQL)
   - hex_grid, hospitals, hex_distances (ST_Distance in EPSG:32636)
                │
                ▼
        Python / Pandas
   - Min-max normalized composite vulnerability score
   - Exported to alexandria_risk_scores.csv
                │
                ▼
        FastAPI (loads CSV into memory at startup)
   - GET /api/v1/flood-safety/by-location?lat=..&lon=..
   - GET /api/v1/flood-safety/{h3_index}
```

## API

**By GPS coordinates** (e.g. an ambulance's live location):
```
GET /api/v1/flood-safety/by-location?lat=31.2&lon=29.9
```
Converts `(lat, lon)` to an H3 cell index in memory (`h3.latlng_to_cell`)
and returns the pre-computed risk profile for that cell.

**By H3 index directly** (for dispatch systems that already track cells):
```
GET /api/v1/flood-safety/{h3_index}
```

Example response:
```json
{
  "h3_index": "873f5bb29ffffff",
  "elevation_m": 0,
  "dist_to_hospital_m": 16482.54,
  "vulnerability_score": 94.59,
  "risk_category": "High"
}
```

## Setup

```bash
pip install -r requirements.txt
```

Run the data pipeline notebook/scripts to generate the grid, fetch
elevation and hospital data, compute distances in PostGIS, and export
`alexandria_risk_scores.csv`. Then start the API:

```bash
python -m uvicorn alexandria_api:app --reload
```

## Known Limitations

- Elevation is a static snapshot (Copernicus DEM, 90m resolution) — it's a
  reasonable proxy for relative flood susceptibility, not a real-time or
  high-precision flood model.
- No historical flood event data was available for validation; the risk
  index is a structural proxy (elevation + emergency service access), not
  a calibrated hydrological model.
- Hospital data reflects what's tagged in OpenStreetMap at the time of
  fetching, which may miss some smaller clinics.

## Tech Stack

Python (GeoPandas, OSMnx, H3, FastAPI, Pandas/scikit-learn), PostgreSQL +
PostGIS, SQLAlchemy.
