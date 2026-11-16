"""
List candidate "magnet" points: exact crash coordinates that collect many crashes.

Background: before MPD's mid-October 2026 fix, crash points were snapped to the
centroid of the nearest property (MAR) and then to DDOT's LRS, and crashes the
geocoder could not place landed on a few default points. Those points ("magnets")
pull in crashes that happened elsewhere, so the location they fall on looks far
more dangerous than it is.

This script lists every exact coordinate (LATITUDE/LONGITUDE, 6 decimals) with at
least MIN_CRASHES crashes since START_DATE, and the location it is assigned to by
the same rule the backend uses (intersection within 100 ft, otherwise nearest
block segment within 200 ft). The most common ADDRESS is included as a reading
aid only; nothing is assigned from it.

Review the output and copy confirmed or possible magnets into
Spatial-Files/known_magnets.csv with STATUS = confirmed | possible.
The backend flags crashes that sit exactly on a listed point.

Usage:
  python magnets/list_magnet_candidates.py path/to/Vehicular_Crash_Data.geojson
Output:
  magnets/magnet_candidates.csv
"""

import sys
import pandas as pd
import geopandas as gpd

MIN_CRASHES = 75
START_DATE = "2021-01-01"
INTX_MAX_FT = 100
SEG_MAX_FT = 200
OUT = "magnets/magnet_candidates.csv"


def main(src):
    cols = ["CCN", "REPORTDATE", "LATITUDE", "LONGITUDE", "ADDRESS"]
    df = gpd.read_file(src, columns=cols, ignore_geometry=True)
    df = df[pd.to_datetime(df.REPORTDATE, utc=True) >= pd.Timestamp(START_DATE, tz="UTC")]
    df = df.dropna(subset=["LATITUDE", "LONGITUDE"])
    df["LAT6"] = df.LATITUDE.round(6)
    df["LON6"] = df.LONGITUDE.round(6)
    df["month"] = pd.to_datetime(df.REPORTDATE, utc=True).dt.strftime("%Y-%m")

    pts = (df.groupby(["LAT6", "LON6"])
             .agg(crashes=("CCN", "nunique"), months_active=("month", "nunique"),
                  first_month=("month", "min"), last_month=("month", "max"))
             .reset_index())
    pts = pts[pts.crashes >= MIN_CRASHES].copy()

    addr = (df.assign(a=df.ADDRESS.fillna("").str.split("\n").str[0].str.strip())
              .groupby(["LAT6", "LON6"]).a
              .agg(lambda s: (s.value_counts().index[0], round(s.value_counts(normalize=True).iloc[0], 2))))
    pts = pts.merge(addr.rename("aa").reset_index(), on=["LAT6", "LON6"])
    pts["top_address_reading_aid"] = pts.aa.str[0]
    pts["top_address_share"] = pts.aa.str[1]

    # assigned location, same rule as the backend
    g = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.LON6, pts.LAT6, crs=4326)).to_crs(2248)
    g["pid"] = range(len(g))
    ix = pd.read_parquet("Spatial-Files/Intersection_Points_unique.parquet")
    ix = gpd.GeoDataFrame(ix[["canonical_name"]],
                          geometry=gpd.points_from_xy(ix.LONGITUDE, ix.LATITUDE, crs=4326)).to_crs(2248)
    sg = pd.read_parquet("Spatial-Files/Roadway_Block_Segments.parquet", columns=["SEGMENT_NAME", "WKT"])
    sg = gpd.GeoDataFrame(sg[["SEGMENT_NAME"]], geometry=gpd.GeoSeries.from_wkt(sg.WKT, crs=4326)).to_crs(2248)
    ni = (gpd.sjoin_nearest(g[["pid", "geometry"]], ix, max_distance=INTX_MAX_FT, distance_col="d")
             .sort_values("d").drop_duplicates("pid").set_index("pid").canonical_name)
    ns = (gpd.sjoin_nearest(g[["pid", "geometry"]], sg, max_distance=SEG_MAX_FT, distance_col="d")
             .sort_values("d").drop_duplicates("pid").set_index("pid").SEGMENT_NAME)
    g["location_type"] = g.pid.map(lambda p: "Intersection" if p in ni.index else ("Block" if p in ns.index else ""))
    g["location_name"] = g.pid.map(ni).fillna(g.pid.map(ns)).fillna("")

    out = (pd.DataFrame(g.drop(columns=["geometry", "aa", "pid"]))
             .rename(columns={"LAT6": "LATITUDE", "LON6": "LONGITUDE"})
             .sort_values("crashes", ascending=False))
    out.to_csv(OUT, index=False)
    print(f"{len(out)} points with >= {MIN_CRASHES} crashes since {START_DATE} -> {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python magnets/list_magnet_candidates.py path/to/Vehicular_Crash_Data.geojson")
    main(sys.argv[1])
