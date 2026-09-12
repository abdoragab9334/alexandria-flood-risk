"""
generate_alexandria_h3_grid.py

Builds an H3 hexagonal grid covering coastal Alexandria.

Engineering decision: uses a manual bounding box instead of OSM's
administrative boundary for "Alexandria, Egypt", which returns the full
governorate (extends ~137km south into the desert). The bounding box below
matches the actual urban coastal strip.

Engineering decision: resolution 7 (not 8) was chosen after resolution 8
(~4,800 cells) required ~49 batched Open-Meteo API calls and repeatedly hit
rate limits. Resolution 7 keeps the pipeline reliably within free-tier
limits.
"""

import h3
import geopandas as gpd
from shapely.geometry import Polygon, box

# Coastal bounding box (west, south, east, north) - covers the urban strip
# from Agami/Dekheila in the west to Montaza/Abu Qir in the east
NORTH, SOUTH = 31.35, 31.05
EAST, WEST = 30.10, 29.75
H3_RESOLUTION = 7
OUTPUT_PATH = "data/alexandria_h3_grid.geojson"


def build_h3_grid(west, south, east, north, resolution):
    boundary_box = box(west, south, east, north)
    boundary_coords = [[y, x] for x, y in boundary_box.exterior.coords]

    h3_cells = h3.polygon_to_cells(h3.LatLngPoly(boundary_coords), res=resolution)
    print(f"Number of hexagonal cells: {len(h3_cells)}")

    records = []
    for cell in h3_cells:
        center_lat, center_lon = h3.cell_to_latlng(cell)
        boundary_polygon = h3.cell_to_boundary(cell)
        poly = Polygon([(lon, lat) for lat, lon in boundary_polygon])
        records.append({
            "h3_index": cell,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "geometry": poly
        })

    return gpd.GeoDataFrame(records, crs="EPSG:4326")


if __name__ == "__main__":
    gdf_hex = build_h3_grid(WEST, SOUTH, EAST, NORTH, H3_RESOLUTION)
    gdf_hex.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Grid saved to {OUTPUT_PATH}")
