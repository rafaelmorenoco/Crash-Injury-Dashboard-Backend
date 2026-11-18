"""
Pull the DDOT layers that mark pedestrian crossings and traffic control, for
the step-out enforcement page.

Writes one raw parquet per layer to crossings/raw/ (not committed), plus a
summary of sign codes so the crossing-site rules can be checked against what
the data actually holds:

  signs.parquet       Signworks SignPoints (FeatureServer/1): pedestrian
                      crossing, school crossing, in-street yield, stop and
                      all-way signs, plus the arrow and AHEAD plaques (matched
                      to the sign above them later, by SUPPORTID)
  flashers.parquet    LED pedestrian flashers (TrafficSafety/9)
  rrfbs.parquet       RRFBs (TrafficSafety/2)
  signals.parquet     traffic signals (Transportation_Signs_Signals_Lights/170)
  sign_codes.csv      count of signs by SIGNCODE, description and status

Every record keeps all its attributes plus LONGITUDE/LATITUDE from the point
geometry (WGS84). The script stops with an error if any layer comes back short
of the service's own count.

Usage:
  python crossings/pull_crossing_layers.py
"""

import os
import sys
import time

import pandas as pd
import requests

OUT_DIR = "crossings/raw"
PAGE_SIZE = 1000

SIGNS = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DDOT/Signworks/FeatureServer/1"
LAYERS = {
    "flashers": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DDOT/TrafficSafety/FeatureServer/9",
    "rrfbs":    "https://maps2.dcgis.dc.gov/dcgis/rest/services/DDOT/TrafficSafety/FeatureServer/2",
    "signals":  ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
                 "Transportation_Signs_Signals_Lights_WebMercator/MapServer/170"),
}

# Signs of interest. LIKE catches variants such as 'W11-2L' or 'R1-6a'.
#   W11-2  pedestrian crossing          S1-1   school crossing
#   W16-7P down-left arrow plaque       W16-9P AHEAD plaque
#   R1-6   in-street yield to peds      R1-9   overhead yield to peds
#   R1-1   stop                         R1-3P  ALL WAY plaque
SIGN_CODES = ["w11-2", "s1-1", "w16-7", "w16-9", "r1-6", "r1-9", "r1-1", "r1-3"]
SIGN_FIELDS = ["OBJECTID", "SUPPORTID", "SIGNID", "SignOrder", "SIGNCODE",
               "SignDescription", "SIGNTEXT", "SIGNSTATUS", "SUPPORTSTATUS",
               "SIGNARROWDIRECTION", "SIDE", "BLOCKKEY", "ROUTEID", "MEASURE",
               "WARD", "Latitude", "Longitude", "LASTEDITDATETIME"]


def get(url, params, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "error" in data:                    # ArcGIS errors come with HTTP 200
                raise requests.exceptions.RequestException(data["error"])
            return data
        except (requests.exceptions.RequestException, ValueError) as e:
            if attempt == retries:
                raise
            print(f"  attempt {attempt} failed ({e}); retrying in {30 * attempt}s")
            time.sleep(30 * attempt)


def domains(url):
    """Coded-value domains of a layer, as {field: {code: name}}."""
    meta = get(url, {"f": "json"})
    out = {}
    for f in meta.get("fields", []):
        d = f.get("domain") or {}
        if d.get("type") == "codedValue":
            out[f["name"]] = {c["code"]: c["name"] for c in d["codedValues"]}
    return out


def fetch(name, url, where="1=1", fields="*"):
    q = url + "/query"
    base = {"where": where, "f": "json"}
    expected = get(q, {**base, "returnCountOnly": "true"})["count"]
    rows, offset = [], 0
    while True:
        page = get(q, {**base, "outFields": fields, "outSR": 4326,
                       "returnGeometry": "true", "orderByFields": "OBJECTID",
                       "resultOffset": offset, "resultRecordCount": PAGE_SIZE})
        feats = page.get("features", [])
        if not feats:
            break
        for f in feats:
            g = f.get("geometry") or {}
            rows.append({**f["attributes"], "LONGITUDE": g.get("x"), "LATITUDE": g.get("y")})
        offset += len(feats)
        if not page.get("exceededTransferLimit") and len(feats) < PAGE_SIZE:
            break
    print(f"{name}: {len(rows)} of {expected}")
    if len(rows) != expected:
        sys.exit(f"{name}: download incomplete ({len(rows)} of {expected})")
    df = pd.DataFrame(rows)
    # decode coded values (status, arrow direction, ...) into readable labels
    for field, codes in domains(url).items():
        if field in df.columns:
            df[field + "_LABEL"] = df[field].map(codes)
    return df


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # signs of interest, then every sign on the same supports (the plaques)
    like = " OR ".join(f"lower(SIGNCODE) LIKE '{c}%'" for c in SIGN_CODES)
    where = f"({like}) OR lower(SignDescription) = 'stop'"
    signs = fetch("signs", SIGNS, where, ",".join(SIGN_FIELDS))
    signs.to_parquet(f"{OUT_DIR}/signs.parquet", index=False)

    status = "SIGNSTATUS_LABEL" if "SIGNSTATUS_LABEL" in signs else "SIGNSTATUS"
    codes = (signs.fillna({"SIGNCODE": "", "SignDescription": ""})
                  .groupby(["SIGNCODE", "SignDescription", status], dropna=False)
                  .size().rename("signs").reset_index()
                  .sort_values("signs", ascending=False))
    codes.to_csv(f"{OUT_DIR}/sign_codes.csv", index=False)
    print(f"sign codes: {len(codes)} combinations -> {OUT_DIR}/sign_codes.csv")

    for name, url in LAYERS.items():
        fetch(name, url).to_parquet(f"{OUT_DIR}/{name}.parquet", index=False)

    print(f"done -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
