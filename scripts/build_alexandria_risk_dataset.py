"""
build_alexandria_risk_dataset.py

Fetches elevation and hospital data, computes distances in PostGIS, and
builds the composite flood vulnerability score for every H3 cell.

Prerequisite: run generate_alexandria_h3_grid.py first (produces a
land-only grid - see that script for why sea cells are excluded), and
create the `alexandria_gis` PostGIS database with the postgis extension
enabled:
    CREATE EXTENSION IF NOT EXISTS postgis;
"""

import os
import time
import requests
import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.preprocessing import MinMaxScaler
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

GRID_PATH = "data/alexandria_h3_grid.geojson"
HOSPITALS_PATH = "data/alexandria_hospitals.geojson"
OUTPUT_CSV = "data/alexandria_risk_scores.csv"

# Coastal bounding box - must match generate_alexandria_h3_grid.py
NORTH, SOUTH = 31.35, 31.05
EAST, WEST = 30.10, 29.75

CHUNK_SIZE = 100  # Open-Meteo's max points per request


def fetch_elevations(gdf_hex):
    """Fetches elevation for every cell centroid via Open-Meteo, in batches,
    with retry logic for rate limiting (per-minute and per-hour limits)."""

    def get_batch(lats, lons, max_retries=5):
        lat_str = ",".join(map(str, lats))
        lon_str = ",".join(map(str, lons))
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat_str}&longitude={lon_str}"

        for attempt in range(max_retries):
            response = requests.get(url)
            data = response.json()

            if "elevation" in data:
                return data["elevation"]

            reason = data.get("reason", "").lower()
            if "limit exceeded" in reason:
                print(f"Rate limit reached - waiting 60 seconds... (attempt {attempt + 1})")
                time.sleep(60)
            else:
                print(f"Attempt {attempt + 1} failed for a different reason. Response: {data}")
                time.sleep(5)

        raise Exception(f"Failed after {max_retries} attempts. Last response: {data}")

    lats = gdf_hex["center_lat"].tolist()
    lons = gdf_hex["center_lon"].tolist()
    all_elevations = []

    for i in range(0, len(lats), CHUNK_SIZE):
        chunk_lats = lats[i:i + CHUNK_SIZE]
        chunk_lons = lons[i:i + CHUNK_SIZE]
        elevations = get_batch(chunk_lats, chunk_lons)
        all_elevations.extend(elevations)
        print(f"Fetched {len(all_elevations)} of {len(lats)}")
        time.sleep(2)  # politeness delay between requests

    gdf_hex["elevation_m"] = all_elevations
    return gdf_hex


def fetch_hospitals():
    """Fetches hospitals/clinics from OpenStreetMap within the coastal bbox.

    Note: osmnx v2's features_from_bbox expects (west, south, east, north) -
    an earlier version of this script passed (north, south, east, west) by
    mistake, which silently returned data from a different region entirely.
    """
    import osmnx as ox

    tags_medical = {"amenity": ["hospital", "clinic"]}
    gdf_medical = ox.features_from_bbox(bbox=(WEST, SOUTH, EAST, NORTH), tags=tags_medical)
    print(f"Number of hospitals/clinics: {len(gdf_medical)}")

    gdf_clean = gdf_medical.copy()
    gdf_projected = gdf_clean.to_crs(epsg=32636)
    gdf_clean["geometry"] = gdf_projected.geometry.centroid.to_crs(epsg=4326)

    gdf_clean = gdf_clean[["geometry"]].copy()
    if "name" in gdf_medical.columns:
        gdf_clean["name"] = gdf_medical["name"]

    return gdf_clean.reset_index(drop=True)


def compute_distances(engine, gdf_hex, gdf_hospitals):
    """Loads grid + hospitals into PostGIS and computes nearest-hospital
    distance per cell using ST_Distance in EPSG:32636 (UTM Zone 36N)."""
    gdf_hex.to_postgis("hex_grid", engine, if_exists="replace", index=False)
    gdf_hospitals.to_postgis("hospitals", engine, if_exists="replace", index=False)

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS hex_distances;"))
        conn.execute(text("""
            CREATE TABLE hex_distances AS
            SELECT
                hg.h3_index,
                hg.center_lat,
                hg.center_lon,
                hg.elevation_m,
                MIN(ST_Distance(
                    ST_Transform(hg.geometry, 32636),
                    ST_Transform(h.geometry, 32636)
                )) AS dist_to_hospital_m
            FROM hex_grid hg
            CROSS JOIN hospitals h
            GROUP BY hg.h3_index, hg.center_lat, hg.center_lon, hg.elevation_m;
        """))
        conn.commit()

    return pd.read_sql("SELECT * FROM hex_distances", engine)


def compute_vulnerability_score(df_risk):
    """Composite vulnerability score.

    Engineering decision: originally used fixed thresholds (0m/15m for
    elevation, 5000m for distance), which produced a degenerate result -
    168 of 184 cells classified "High" - because 84% of the coastal strip
    sits at or below sea level, so a fixed cutoff couldn't discriminate
    between cells. Fixed by min-max normalizing both factors against the
    actual data distribution instead, and rebalancing weights to 50/50
    since elevation carries much less discriminating power here than a
    generic 70/30 split would assume.

    Note: this normalization is computed AFTER the grid was restricted to
    land-only cells (see generate_alexandria_h3_grid.py). Sea cells would
    otherwise pull the min/max range in a meaningless direction.
    """
    df_risk["elevation_risk"] = 1 - MinMaxScaler().fit_transform(df_risk[["elevation_m"]])
    df_risk["distance_risk"] = MinMaxScaler().fit_transform(df_risk[["dist_to_hospital_m"]])

    df_risk["vulnerability_score"] = (
        (0.5 * df_risk["elevation_risk"]) + (0.5 * df_risk["distance_risk"])
    ) * 100

    def classify(score):
        if score <= 30:
            return "Low"
        elif score <= 60:
            return "Medium"
        return "High"

    df_risk["risk_category"] = df_risk["vulnerability_score"].apply(classify)
    return df_risk


if __name__ == "__main__":
    engine = create_engine(f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    gdf_hex = gpd.read_file(GRID_PATH)
    gdf_hex = fetch_elevations(gdf_hex)
    gdf_hex.to_file(GRID_PATH, driver="GeoJSON")

    gdf_hospitals = fetch_hospitals()
    gdf_hospitals.to_file(HOSPITALS_PATH, driver="GeoJSON")

    df_risk = compute_distances(engine, gdf_hex, gdf_hospitals)
    df_risk = compute_vulnerability_score(df_risk)

    df_risk.to_csv(OUTPUT_CSV, index=False)
    print(f"\nFinal result saved to {OUTPUT_CSV}")
    print(df_risk["risk_category"].value_counts())
