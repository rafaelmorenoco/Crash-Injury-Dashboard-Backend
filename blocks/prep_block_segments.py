"""
Build the roadway block segments used to assign crashes to a named block.

Runs monthly on the EGH runner (refresh_reference_layers.yml), right after the
intersection refresh, because the piece names use the nearest intersection.
Pulls the Roadway Block layer from the DC GIS REST service (or reads a local
Roadway_Block.geojson when one is passed) and writes two slim files that are
committed:

  Spatial-Files/Roadway_Block_Segments.parquet   used by the backend
      SEGMENT_ID, BLOCKKEY, SEGMENT_NAME, PART, N_PARTS, LENGTH_FT,
      ROUTENAME, DCFUNCTIONALCLASS, ROAD_CLASS, MID_LAT, MID_LON, WKT (EPSG:4326)
  Spatial-Files/Roadway_Block_Segments.geojson   for the frontend detail map
      SEGMENT_ID only, coordinates rounded to 6 decimals

Splitting rule: a block up to 1,000 ft stays whole (about 95% of blocks). A
longer block is cut into equal pieces of about 500 ft: round(length / 500)
pieces, at least 2, so pieces run roughly 420 to 625 ft. This keeps a 5-mile
freeway block from collecting the crashes of 5 miles of road.

Naming:
  whole block         BLOCK_NAME, e.g. "4100 - 4199 BLOCK OF GEORGIA AVENUE NW";
                      blocks with no BLOCK_NAME (freeways) or a placeholder
                      "0 - 0 BLOCK OF ..." use ROUTENAME FROM x TO y
  piece of a block    ROUTENAME NEAR <nearest intersection> (PART k OF n);
                      the nearest intersection to the piece's midpoint, within
                      1,000 ft; otherwise the whole-block name with (PART k OF n)
  still not unique    add FROM x TO y, then (INBOUND)/(OUTBOUND), then a short ID
                      (mostly the two sides of a divided road)

SEGMENT_ID is the BLOCKKEY for a whole block and BLOCKKEY_k for piece k. DDOT
regenerates BLOCKKEY between releases, so IDs change when this file is rebuilt.

Safety: the script exits with an error, leaving the existing files untouched,
when the download comes back short of the service's own count, or when the
number of blocks moves more than MAX_CHANGE from the current file. A real
re-release of the layer stays well inside that; a failed or partial download
does not.

Usage:
  python blocks/prep_block_segments.py                      # from the service
  python blocks/prep_block_segments.py Roadway_Block.geojson  # from a file
"""

import os
import sys
import time
import numpy as np
import pandas as pd
import geopandas as gpd
import requests
import shapely
from shapely.ops import linemerge, substring

SPLIT_OVER_FT = 1000      # blocks longer than this get split
PIECE_TARGET_FT = 500     # target piece length
NEAR_MAX_FT = 1000        # max distance for the "NEAR <intersection>" label
CRS_FT = 2248             # NAD83 / Maryland (ftUS), same as the backend
INTX = "Spatial-Files/Intersection_Points_unique.parquet"
OUT_PARQUET = "Spatial-Files/Roadway_Block_Segments.parquet"
OUT_GEOJSON = "Spatial-Files/Roadway_Block_Segments.geojson"

SERVICE_URL = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
               "Transportation_WebMercator/MapServer/163/query")   # Roadway Block
FIELDS = ["OBJECTID", "BLOCKKEY", "BLOCK_NAME", "ROUTENAME", "FROMSTREET",
          "TOSTREET", "DCFUNCTIONALCLASS", "SUMMARYDIRECTION"]
PAGE_SIZE = 1000          # the server may return fewer per page; we follow it
MAX_CHANGE = 0.10         # refuse a block count more than 10% off the current file

ROAD_CLASS = {
    11: "Interstate", 12: "Freeway/Expressway", 14: "Principal Arterial",
    16: "Minor Arterial", 17: "Collector", 18: "Collector", 19: "Local",
}


def clean(s):
    return " ".join(str(s).split()) if pd.notna(s) else ""


DIRECTION = {"IB": "INBOUND", "OB": "OUTBOUND"}


def first_cross_street(field, own):
    # DDOT lists several streets as "A/B/A/OWN ROAD"; keep the first that isn't
    # the road itself
    for name in (clean(x) for x in str(field).split("/")):
        if name and name != own:
            return name
    return ""


def from_to(r):
    own = clean(r.ROUTENAME)
    frm = first_cross_street(r.FROMSTREET, own) if pd.notna(r.FROMSTREET) else ""
    to = first_cross_street(r.TOSTREET, own) if pd.notna(r.TOSTREET) else ""
    return f" FROM {frm} TO {to}" if frm and to and frm != to else ""


def whole_block_name(r):
    # "0 - 0 BLOCK OF ..." is a placeholder for blocks without addresses
    bn = clean(r.BLOCK_NAME)
    if bn and not bn.startswith("0 - 0 BLOCK OF"):
        return bn
    return clean(r.ROUTENAME) + from_to(r)


def _get(params, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(SERVICE_URL, params=params, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "error" in data:            # ArcGIS reports errors with HTTP 200
                raise requests.exceptions.RequestException(data["error"])
            return data
        except (requests.exceptions.RequestException, ValueError) as e:
            if attempt == max_retries:
                raise
            print(f"attempt {attempt} failed ({e}); retrying in {30 * attempt}s")
            time.sleep(30 * attempt)


def fetch_blocks():
    """Download every Roadway Block feature as GeoJSON, one page at a time."""
    base = {"where": "1=1", "outSR": 4326}
    expected = _get({**base, "returnCountOnly": "true", "f": "json"})["count"]
    feats, offset = [], 0
    while True:
        page = _get({**base, "outFields": ",".join(FIELDS), "f": "geojson",
                     "orderByFields": "OBJECTID", "resultOffset": offset,
                     "resultRecordCount": PAGE_SIZE})
        got = page.get("features", [])
        if not got:
            break
        feats.extend(got)
        offset += len(got)
        print(f"fetched {len(feats)} of {expected}")
        more = (page.get("exceededTransferLimit")
                or page.get("properties", {}).get("exceededTransferLimit"))
        if not more and len(got) < PAGE_SIZE:
            break
    if len(feats) != expected:
        sys.exit(f"download incomplete: {len(feats)} features, service reports "
                 f"{expected}; existing files left untouched")
    return gpd.GeoDataFrame.from_features(feats, crs=4326)


def check_count(n_blocks):
    """Stop if the block count moved too far from the file being replaced."""
    if not os.path.exists(OUT_PARQUET):
        return
    prev = pd.read_parquet(OUT_PARQUET, columns=["BLOCKKEY"]).BLOCKKEY.nunique()
    change = abs(n_blocks - prev) / prev
    print(f"blocks: {n_blocks} now, {prev} in the current file ({change:.1%} change)")
    if change > MAX_CHANGE:
        sys.exit(f"block count changed {change:.1%} (limit {MAX_CHANGE:.0%}); "
                 "existing files left untouched")


def main(src=None):
    cols = ["BLOCKKEY", "BLOCK_NAME", "ROUTENAME", "FROMSTREET", "TOSTREET",
            "DCFUNCTIONALCLASS", "SUMMARYDIRECTION"]
    raw = fetch_blocks() if src is None else gpd.read_file(src, columns=cols)
    blocks = raw[cols + ["geometry"]].to_crs(CRS_FT)
    blocks = blocks[blocks.geometry.notna() & ~blocks.geometry.is_empty]
    # the Open Data file carries a dummy Z = 0 and the service does not; drop it
    blocks["geometry"] = shapely.force_2d(blocks.geometry.values)
    # the service can return a block as a MultiLineString; join its parts when
    # they connect. A block that stays in pieces is kept whole (never split).
    multi = blocks.geom_type == "MultiLineString"
    if multi.any():
        blocks.loc[multi, "geometry"] = blocks.geometry[multi].apply(linemerge)
        print(f"{int(multi.sum())} multi-part blocks; "
              f"{int((blocks.geom_type != 'LineString').sum())} stay multi-part (not split)")
    # fixed order, so the same input always gives the same files
    blocks = blocks.sort_values("BLOCKKEY", kind="mergesort").reset_index(drop=True)
    check_count(blocks.BLOCKKEY.nunique())
    blocks["len_ft"] = blocks.geometry.length
    blocks["n_parts"] = np.where(
        (blocks.len_ft > SPLIT_OVER_FT) & (blocks.geom_type == "LineString"),
        np.maximum(2, np.round(blocks.len_ft / PIECE_TARGET_FT)), 1).astype(int)

    rows = []
    for r in blocks.itertuples(index=False):
        base = whole_block_name(r)
        n, L = r.n_parts, r.len_ft
        for k in range(1, n + 1):
            geom = r.geometry if n == 1 else substring(r.geometry, (k - 1) * L / n, k * L / n)
            rows.append({
                "SEGMENT_ID": r.BLOCKKEY if n == 1 else f"{r.BLOCKKEY}_{k}",
                "BLOCKKEY": r.BLOCKKEY,
                "PART": k, "N_PARTS": n,
                "BASE_NAME": base, "ROUTENAME": clean(r.ROUTENAME),
                "FROM_TO": from_to(r),
                "DIRECTION": DIRECTION.get(clean(r.SUMMARYDIRECTION), ""),
                "DCFUNCTIONALCLASS": r.DCFUNCTIONALCLASS,
                "geometry": geom,
            })
    seg = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS_FT)
    seg["LENGTH_FT"] = seg.geometry.length.round(0)
    seg["ROAD_CLASS"] = seg.DCFUNCTIONALCLASS.map(ROAD_CLASS).fillna("Other")
    mid = seg.geometry.interpolate(0.5, normalized=True)

    # nearest intersection to each piece's midpoint, for split blocks only
    intx_df = pd.read_parquet(INTX)
    intx = gpd.GeoDataFrame(
        intx_df[["canonical_name"]],
        geometry=gpd.points_from_xy(intx_df.LONGITUDE, intx_df.LATITUDE, crs=4326),
    ).to_crs(CRS_FT)
    split = seg.N_PARTS > 1
    mids = gpd.GeoDataFrame({"i": seg.index[split]}, geometry=mid[split].values, crs=CRS_FT)
    near = (gpd.sjoin_nearest(mids, intx, how="left", max_distance=NEAR_MAX_FT,
                              distance_col="d")
              .sort_values(["i", "d", "canonical_name"]).drop_duplicates("i")
              .set_index("i")["canonical_name"])

    part = " (PART " + seg.PART.astype(str) + " OF " + seg.N_PARTS.astype(str) + ")"
    near_name = seg.index.to_series().map(near)
    seg["SEGMENT_NAME"] = np.where(
        ~split, seg.BASE_NAME,
        np.where(near_name.notna(),
                 seg.ROUTENAME + " NEAR " + near_name.fillna("") + part,
                 seg.BASE_NAME + part))

    # Disambiguate names shared by several segments (mostly the two sides of a
    # divided road): add the from/to streets, then the direction, then the ID.
    dup = (seg.SEGMENT_NAME.duplicated(keep=False) & ~seg.SEGMENT_NAME.str.contains(" FROM ")
           & (seg.N_PARTS == 1))   # pieces of a split block go straight to direction
    seg.loc[dup, "SEGMENT_NAME"] = seg.SEGMENT_NAME[dup] + seg.FROM_TO[dup]
    dup = seg.SEGMENT_NAME.duplicated(keep=False) & (seg.DIRECTION != "")
    seg.loc[dup, "SEGMENT_NAME"] = seg.SEGMENT_NAME[dup] + " (" + seg.DIRECTION[dup] + ")"
    dup = seg.SEGMENT_NAME.duplicated(keep=False)
    seg.loc[dup, "SEGMENT_NAME"] = seg.SEGMENT_NAME[dup] + " [" + seg.SEGMENT_ID[dup].str[:6] + "]"

    mid_ll = gpd.GeoSeries(mid, crs=CRS_FT).to_crs(4326)
    seg["MID_LAT"] = mid_ll.y.round(6)
    seg["MID_LON"] = mid_ll.x.round(6)
    seg_ll = seg.to_crs(4326)
    seg["WKT"] = seg_ll.geometry.to_wkt(rounding_precision=6)

    keep = ["SEGMENT_ID", "BLOCKKEY", "SEGMENT_NAME", "PART", "N_PARTS", "LENGTH_FT",
            "ROUTENAME", "DCFUNCTIONALCLASS", "ROAD_CLASS", "MID_LAT", "MID_LON", "WKT"]
    pd.DataFrame(seg[keep]).to_parquet(OUT_PARQUET, index=False)
    seg_ll[["SEGMENT_ID", "geometry"]].to_file(
        OUT_GEOJSON, driver="GeoJSON", COORDINATE_PRECISION=6)

    print(f"{len(blocks)} blocks -> {len(seg)} segments "
          f"({int((blocks.n_parts > 1).sum())} blocks split into "
          f"{int(blocks.n_parts[blocks.n_parts > 1].sum())} pieces)")
    print(f"split pieces named by nearest intersection: {int(near_name.notna().sum())} "
          f"of {int(split.sum())}")
    print(f"unique names: {seg.SEGMENT_NAME.is_unique}")
    print(f"wrote {OUT_PARQUET} and {OUT_GEOJSON}")


if __name__ == "__main__":
    if len(sys.argv) > 2:
        sys.exit("usage: python blocks/prep_block_segments.py [path/to/Roadway_Block.geojson]")
    main(sys.argv[1] if len(sys.argv) == 2 else None)
