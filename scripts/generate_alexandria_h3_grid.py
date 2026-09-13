"""
generate_alexandria_h3_grid.py

Builds an H3 hexagonal grid covering coastal Alexandria, restricted to land
cells only.

Engineering decision: uses a manual bounding box instead of OSM's
administrative boundary for "Alexandria, Egypt", which returns the full
governorate (extends ~137km south into the desert). The bounding box below
matches the actual urban coastal strip.

Engineering decision: resolution 7 (not 8) was chosen after resolution 8
(~4,800 cells) required ~49 batched Open-Meteo API calls and repeatedly hit
rate limits. Resolution 7 keeps the pipeline reliably within free-tier
limits.

Engineering decision: a rectangular bounding box over a coastal city
inevitably includes open sea to the northwest. An initial version of this
script did not filter these out, which produced hexagons sitting entirely
in the Mediterranean that were later scored as flood-risk zones - a
meaningless result, since there's no land there to flood. A first attempt
to build a land polygon from OSM's raw coastline data failed because the
crowd-sourced coastline segments had gaps and could not be merged into a
single continuous line. The current approach instead clips against
Natural Earth's authoritative land dataset and keeps only H3 cells whose
centroid falls on land.
"""

import h3
import geopandas as gpd
from shapely.geometry import Polygon, box, Point

# Coastal bounding box (west, south, east, north) - covers the urban strip
# from Agami/Dekheila in the west to Montaza/Abu Qir in the east
NORTH, SOUTH = 31.35, 31.05
EAST, WEST = 30.10, 29.75
H3_RESOLUTION = 7
OUTPUT_PATH = "data/alexandria_h3_grid.geojson"

NATURAL_EARTH_LAND_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_10m_land.geojson"
)


def get_land_boundary(west, south, east, north):
    """Returns the land geometry within the bounding box, using Natural
    Earth's world land polygons rather than raw OSM coastline data."""
    land = gpd.read_file(NATURAL_EARTH_LAND_URL)
    bbox_poly = box(west, south, east, north)
    land_clipped = gpd.clip(land, bbox_poly)
    return land_clipped.geometry.union_all()


def build_h3_grid(west, south, east, north, resolution):
    land_geom = get_land_boundary(west, south, east, north)

    # Build the full rectangular grid first, then keep only land cells
    bbox_poly = box(west, south, east, north)
    boundary_coords = [[y, x] for x, y in bbox_poly.exterior.coords]
    all_cells = h3.polygon_to_cells(h3.LatLngPoly(boundary_coords), res=resolution)

    records = []
    for cell in all_cells:
        center_lat, center_lon = h3.cell_to_latlng(cell)
        if not land_geom.contains(Point(center_lon, center_lat)):
            continue  # skip cells whose center falls in the sea

        boundary_polygon = h3.cell_to_boundary(cell)
        poly = Polygon([(lon, lat) for lat, lon in boundary_polygon])
        records.append({
            "h3_index": cell,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "geometry": poly
        })

    print(f"Number of hexagonal cells (land only): {len(records)}")
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


if __name__ == "__main__":
    gdf_hex = build_h3_grid(WEST, SOUTH, EAST, NORTH, H3_RESOLUTION)
    gdf_hex.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Grid saved to {OUTPUT_PATH}")
