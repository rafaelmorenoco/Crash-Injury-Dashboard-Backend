"""
Build the roadway block segments used to assign crashes to a named block.

Run by hand when DDOT publishes a new Roadway Block file (not part of the
scheduled pipeline). Reads the full Roadway_Block.geojson from Open Data DC
(~46 MB, not committed) and writes two slim files that are committed:

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

Usage:
  python blocks/prep_block_segments.py path/to/Roadway_Block.geojson
"""

import sys
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import substring

SPLIT_OVER_FT = 1000      # blocks longer than this get split
PIECE_TARGET_FT = 500     # target piece length
NEAR_MAX_FT = 1000        # max distance for the "NEAR <intersection>" label
CRS_FT = 2248             # NAD83 / Maryland (ftUS), same as the backend
INTX = "Spatial-Files/Intersection_Points_unique.parquet"
OUT_PARQUET = "Spatial-Files/Roadway_Block_Segments.parquet"
OUT_GEOJSON = "Spatial-Files/Roadway_Block_Segments.geojson"

ROAD_CLASS = {
    11: "Interstate", 12: "Freeway/Expressway", 14: "Principal Arterial",
    16: "Minor Arterial", 17: "Collector", 18: "Collector", 19: "Local",
}


def clean(s):
    return " ".join(str(s).split()) if pd.notna(s) else ""


DIRECTION = {"IB": "INBOUND", "OB": "OUTBOUND"}


def from_to(r):
    frm, to = clean(r.FROMSTREET), clean(r.TOSTREET)
    return f" FROM {frm} TO {to}" if frm and to else ""


def whole_block_name(r):
    # "0 - 0 BLOCK OF ..." is a placeholder for blocks without addresses
    bn = clean(r.BLOCK_NAME)
    if bn and not bn.startswith("0 - 0 BLOCK OF"):
        return bn
    return clean(r.ROUTENAME) + from_to(r)


def main(src):
    cols = ["BLOCKKEY", "BLOCK_NAME", "ROUTENAME", "FROMSTREET", "TOSTREET",
            "DCFUNCTIONALCLASS", "SUMMARYDIRECTION"]
    blocks = gpd.read_file(src, columns=cols).to_crs(CRS_FT)
    blocks = blocks[blocks.geometry.notna() & ~blocks.geometry.is_empty]
    blocks["len_ft"] = blocks.geometry.length
    blocks["n_parts"] = np.where(
        blocks.len_ft > SPLIT_OVER_FT,
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
              .sort_values("d").drop_duplicates("i").set_index("i")["canonical_name"])

    part = " (PART " + seg.PART.astype(str) + " OF " + seg.N_PARTS.astype(str) + ")"
    near_name = seg.index.to_series().map(near)
    seg["SEGMENT_NAME"] = np.where(
        ~split, seg.BASE_NAME,
        np.where(near_name.notna(),
                 seg.ROUTENAME + " NEAR " + near_name.fillna("") + part,
                 seg.BASE_NAME + part))

    # Disambiguate names shared by several segments (mostly the two sides of a
    # divided road): add the from/to streets, then the direction, then the ID.
    dup = seg.SEGMENT_NAME.duplicated(keep=False) & ~seg.SEGMENT_NAME.str.contains(" FROM ")
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
    if len(sys.argv) != 2:
        sys.exit("usage: python blocks/prep_block_segments.py path/to/Roadway_Block.geojson")
    main(sys.argv[1])
