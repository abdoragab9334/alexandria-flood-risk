"""
alexandria_emergency_api.py

FastAPI service that serves pre-computed flood vulnerability scores per H3
cell. Loads the risk dataset into memory once at startup, so lookups at
request time are dictionary lookups rather than fresh spatial computations.

Run with:
    python -m uvicorn alexandria_emergency_api:app --reload
Then open http://localhost:8000/docs
"""

from fastapi import FastAPI, HTTPException
import pandas as pd
import h3

app = FastAPI()

RISK_DATA_PATH = "data/alexandria_risk_scores.csv"
H3_RESOLUTION = 7  # must match the resolution used to build the grid

risk_data = {}


@app.on_event("startup")
def load_data():
    global risk_data
    df = pd.read_csv(RISK_DATA_PATH)
    risk_data = df.set_index("h3_index").to_dict(orient="index")
    print(f"Loaded {len(risk_data)} cells into memory")


def _format_result(h3_index, result, lat=None, lon=None):
    payload = {
        "h3_index": h3_index,
        "elevation_m": result["elevation_m"],
        "dist_to_hospital_m": round(result["dist_to_hospital_m"], 2),
        "vulnerability_score": round(result["vulnerability_score"], 2),
        "risk_category": result["risk_category"],
    }
    if lat is not None and lon is not None:
        payload = {"latitude": lat, "longitude": lon, **payload}
    return payload


@app.get("/api/v1/flood-safety/by-location")
def get_safety_by_location(lat: float, lon: float):
    h3_index = h3.latlng_to_cell(lat, lon, H3_RESOLUTION)

    if h3_index not in risk_data:
        raise HTTPException(status_code=404, detail="This location is outside the available data coverage")

    return _format_result(h3_index, risk_data[h3_index], lat, lon)


@app.get("/api/v1/flood-safety/{h3_index}")
def get_safety_by_h3(h3_index: str):
    if h3_index not in risk_data:
        raise HTTPException(status_code=404, detail="H3 index not found in the data")

    return _format_result(h3_index, risk_data[h3_index])
