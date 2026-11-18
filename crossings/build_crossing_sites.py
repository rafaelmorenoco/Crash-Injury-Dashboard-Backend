"""
Uncontrolled pedestrian crossing sites, for the step-out enforcement page.

Runs monthly on the EGH runner (refresh_reference_layers.yml), after the
intersection and block refreshes and pull_crossing_layers.py. Reads the raw
layers in crossings/raw/ and writes two committed files:

  Spatial-Files/Crossing_Sites.parquet    one row per site
  Spatial-Files/Crossing_Devices.parquet  the devices behind each site (map)

A site is either an intersection (its INTERSECTIONKEY, so crashes match the
same way they do everywhere else) or a midblock crossing (a cluster of
devices more than 100 ft from any intersection).

Rules
  Devices that mark a crossing:
    RRFB, LED pedestrian flasher     status Existing only (removed ones dropped)
    HAWK beacon                      from the signal layer (HAWK = Yes)
    In-street yield sign             R1-6* / R1-9*
    Crossing sign                    W11-2, W11-2A, S1-1 with the arrow plaque
                                     (W16-7P) on the same support
    Crossing sign (no plaque)        the same signs with no plaque; kept only
                                     at intersections. Between intersections a
                                     lone unplaqued sign is almost always an
                                     advance warning, so it is dropped.
    Signs under an AHEAD plaque (W16-9P) are advance warnings and dropped.
    Signs not yet installed ("Requested (New)") are dropped.
  Two-way stop intersections are sites even without a device: at least one
    stop sign (R1-1) within 100 ft, no all-way plaque (R1-3P / R1-3 / R1-3A),
    no signal. Their crosswalks across the through street are uncontrolled.
  Signalized sites are dropped: any non-HAWK signal within 100 ft.
  CONTROL per site: HAWK beacon, Two-way stop, All-way stop, Uncontrolled
    (intersection with no stop sign found), or Midblock.

Columns (sites)
  SITE_ID         INTERSECTIONKEY, or a 32-character hash of the midblock
                  devices' rounded coordinates (stable while devices stay put)
  SITE_TYPE       Intersection | Midblock
  SITE_NAME       intersection name, or "<block> (MIDBLOCK)"
  CONTROL         see above
  DEVICE          strongest device at the site (or "None" for a two-way stop
                  without one); DEVICES lists them all
  N_DEVICES
  PSA, POLICEDISTRICT   polygon the site point falls in (nearest if outside)
  LATITUDE, LONGITUDE   intersection point, or the midblock devices' centroid

Usage (from the backend repo root):
  python crossings/build_crossing_sites.py
"""

import hashlib
import sys

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely

RAW = "crossings/raw"
INTX = "Spatial-Files/Intersection_Points_unique.parquet"
SEGS = "Spatial-Files/Roadway_Block_Segments.parquet"
PSA = "Spatial-Files/Police_Service_Areas.geojson"
OUT_SITES = "Spatial-Files/Crossing_Sites.parquet"
OUT_DEVICES = "Spatial-Files/Crossing_Devices.parquet"

FT = 2248          # Maryland State Plane, US feet
R = 100            # every distance rule: 100 ft
MIN_SITES = 1500   # sanity floor; the November 2026 build had about 3,500

# strongest first; a site's DEVICE is the first of these it has
DEVICE_ORDER = ["HAWK beacon", "RRFB", "LED flasher", "In-street yield sign",
                "Crossing sign", "Crossing sign (no plaque)"]
WARNING_CODES = ["W11-2", "W11-2A", "S1-1"]


def points(df, lon="LONGITUDE", lat="LATITUDE"):
    df = df.dropna(subset=[lon, lat])
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat], crs=4326)).to_crs(FT)


def nearest(left, right, cols, tiebreak):
    """Nearest row of `right` within R ft for each row of `left` (ties: lowest tiebreak)."""
    keep = cols + ([tiebreak] if tiebreak not in cols else [])
    j = (gpd.sjoin_nearest(left[["geometry"]], right[keep + ["geometry"]], how="left",
                           max_distance=R, distance_col="_d")
            .reset_index().sort_values(["index", "_d", tiebreak], kind="mergesort")
            .drop_duplicates("index").set_index("index"))
    return j.reindex(left.index)[cols + ["_d"]]


def load_devices():
    signs = pd.read_parquet(f"{RAW}/signs.parquet")
    signs["CODE"] = signs["SIGNCODE"].str.upper().str.strip()
    signs = signs[signs["SIGNSTATUS_LABEL"] != "Requested (New)"]
    on_support = signs.groupby("SUPPORTID")["CODE"].apply(set)
    has = lambda pfx: signs["SUPPORTID"].map(
        lambda s: any(c.startswith(pfx) for c in on_support.get(s, ())))
    signs["arrow"], signs["ahead"] = has("W16-7"), has("W16-9")

    warn = signs[signs["CODE"].isin(WARNING_CODES) & ~(signs["ahead"] & ~signs["arrow"])]
    yld = signs[signs["CODE"].str.startswith(("R1-6", "R1-9"))]
    rrfb = pd.read_parquet(f"{RAW}/rrfbs.parquet")
    rrfb = rrfb[rrfb["Status_LABEL"] == "Existing"]
    flash = pd.read_parquet(f"{RAW}/flashers.parquet")
    flash = flash[flash["STATUS_LABEL"] == "Existing"]
    sig = pd.read_parquet(f"{RAW}/signals.parquet")
    hawk = sig[sig["HAWK_LABEL"] == "Yes"]

    dev = pd.concat([
        pd.DataFrame({"DEVICE": np.where(warn["arrow"], "Crossing sign", "Crossing sign (no plaque)"),
                      "DETAIL": warn["CODE"], "LONGITUDE": warn["LONGITUDE"], "LATITUDE": warn["LATITUDE"]}),
        pd.DataFrame({"DEVICE": "In-street yield sign", "DETAIL": yld["CODE"],
                      "LONGITUDE": yld["LONGITUDE"], "LATITUDE": yld["LATITUDE"]}),
        pd.DataFrame({"DEVICE": "RRFB", "DETAIL": rrfb["Location"],
                      "LONGITUDE": rrfb["LONGITUDE"], "LATITUDE": rrfb["LATITUDE"]}),
        pd.DataFrame({"DEVICE": "LED flasher", "DETAIL": flash["INT_NAME"],
                      "LONGITUDE": flash["LONGITUDE"], "LATITUDE": flash["LATITUDE"]}),
        pd.DataFrame({"DEVICE": "HAWK beacon", "DETAIL": hawk["INTERSECTIONNAME"],
                      "LONGITUDE": hawk["LONGITUDE"], "LATITUDE": hawk["LATITUDE"]}),
    ], ignore_index=True)
    dev["rank"] = dev["DEVICE"].map({d: i for i, d in enumerate(DEVICE_ORDER)})
    dev = dev.sort_values(["rank", "LATITUDE", "LONGITUDE"], kind="mergesort").reset_index(drop=True)
    return points(dev), signs, sig


def summarize(group):
    kinds = [d for d in DEVICE_ORDER if d in set(group["DEVICE"])]
    return pd.Series({"DEVICE": kinds[0], "DEVICES": " + ".join(kinds), "N_DEVICES": len(group)})


def main():
    dev, signs, sig = load_devices()
    ix = points(pd.read_parquet(INTX))[["INTERSECTIONKEY", "canonical_name", "geometry"]]
    print("devices:", dev["DEVICE"].value_counts().to_dict())

    # ---- devices -> nearest intersection within 100 ft -------------------
    n = nearest(dev, ix, ["INTERSECTIONKEY"], "INTERSECTIONKEY")
    dev["INTERSECTIONKEY"] = n["INTERSECTIONKEY"]
    # a lone unplaqued sign between intersections is an advance warning
    dev = dev[dev["INTERSECTIONKEY"].notna() | (dev["DEVICE"] != "Crossing sign (no plaque)")].copy()

    # ---- control at each intersection ------------------------------------
    sig_pts = points(sig)
    plain_sig = sig_pts[sig_pts["HAWK_LABEL"] != "Yes"]
    signalized = set(plain_sig["INTERSECTIONKEY"].dropna()) | set(
        gpd.sjoin_nearest(ix, plain_sig[["geometry"]], how="inner", max_distance=R)["INTERSECTIONKEY"])
    stop_pts = points(signs[signs["CODE"].str.startswith(("R1-1", "R1-3"))].copy())
    s = nearest(stop_pts, ix, ["INTERSECTIONKEY"], "INTERSECTIONKEY")
    stop_pts["INTERSECTIONKEY"] = s["INTERSECTIONKEY"]
    all_way = set(stop_pts.loc[stop_pts["CODE"].str.startswith("R1-3"), "INTERSECTIONKEY"].dropna())
    stops = set(stop_pts.loc[stop_pts["CODE"].str.startswith("R1-1"), "INTERSECTIONKEY"].dropna())
    two_way = stops - all_way

    # ---- intersection sites ----------------------------------------------
    at_ix = dev[dev["INTERSECTIONKEY"].notna()]
    sx = at_ix.groupby("INTERSECTIONKEY").apply(summarize).reset_index()
    bare = sorted(two_way - set(sx["INTERSECTIONKEY"]))          # two-way stops, no device
    sx = pd.concat([sx, pd.DataFrame({"INTERSECTIONKEY": bare, "DEVICE": "None",
                                      "DEVICES": "None", "N_DEVICES": 0})], ignore_index=True)
    hawk_ix = set(at_ix.loc[at_ix["DEVICE"] == "HAWK beacon", "INTERSECTIONKEY"])
    sx = sx[~sx["INTERSECTIONKEY"].isin(signalized - hawk_ix)]
    sx["CONTROL"] = np.select(
        [sx["INTERSECTIONKEY"].isin(hawk_ix), sx["INTERSECTIONKEY"].isin(all_way),
         sx["INTERSECTIONKEY"].isin(two_way)],
        ["HAWK beacon", "All-way stop", "Two-way stop"], "Uncontrolled")
    sx = sx.merge(ix, on="INTERSECTIONKEY").rename(columns={"canonical_name": "SITE_NAME"})
    sx["SITE_ID"], sx["SITE_TYPE"] = sx["INTERSECTIONKEY"], "Intersection"

    # ---- midblock sites: devices clustered within 100 ft -----------------
    mid = dev[dev["INTERSECTIONKEY"].isna()].copy()
    blobs = (gpd.GeoDataFrame(geometry=[shapely.union_all(mid.buffer(R / 2).values)], crs=FT)
               .explode(index_parts=False).reset_index(drop=True))
    mid["cluster"] = gpd.sjoin(mid, blobs, predicate="within", how="left")["index_right"].values
    sm = mid.groupby("cluster").apply(summarize).reset_index()
    sm["geometry"] = mid.dissolve("cluster").centroid.reindex(sm["cluster"]).values
    sm = gpd.GeoDataFrame(sm, geometry="geometry", crs=FT)
    # stable ID: hash of the cluster's device coordinates
    key = (mid.assign(k=mid["LATITUDE"].round(5).astype(str) + "," + mid["LONGITUDE"].round(5).astype(str))
              .groupby("cluster")["k"].apply(lambda k: "|".join(sorted(k))))
    sm["SITE_ID"] = key.reindex(sm["cluster"]).map(lambda k: hashlib.md5(k.encode()).hexdigest()).values
    near_sig = nearest(sm, plain_sig, ["OBJECTID"], "OBJECTID")["_d"].notna()
    has_hawk = sm["DEVICES"].str.contains("HAWK")
    sm = sm[~near_sig.values | has_hawk.values].copy()
    sm["CONTROL"] = np.where(sm["DEVICES"].str.contains("HAWK"), "HAWK beacon", "Midblock")
    sm["SITE_TYPE"], sm["INTERSECTIONKEY"] = "Midblock", None
    # name: the block it sits on
    seg = pd.read_parquet(SEGS, columns=["SEGMENT_ID", "SEGMENT_NAME", "WKT"])
    seg = gpd.GeoDataFrame(seg[["SEGMENT_ID", "SEGMENT_NAME"]],
                           geometry=gpd.GeoSeries.from_wkt(seg["WKT"], crs=4326)).to_crs(FT)
    sm["SITE_NAME"] = (nearest(sm, seg, ["SEGMENT_NAME"], "SEGMENT_ID")["SEGMENT_NAME"]
                         .fillna("UNNAMED BLOCK").values + " (MIDBLOCK)")
    dup = sm.groupby("SITE_NAME").cumcount()
    sm.loc[dup > 0, "SITE_NAME"] = sm["SITE_NAME"].str[:-1] + " " + (dup + 1).astype(str) + ")"

    cols = ["SITE_ID", "SITE_TYPE", "SITE_NAME", "CONTROL", "DEVICE", "DEVICES",
            "N_DEVICES", "INTERSECTIONKEY", "geometry"]
    sites = gpd.GeoDataFrame(pd.concat([sx[cols], sm[cols]], ignore_index=True), crs=FT)

    # ---- PSA / district ---------------------------------------------------
    psa = gpd.read_file(PSA).to_crs(FT)[["PSA", "POLICEDISTRICT", "geometry"]]
    psa["PSA"] = psa["PSA"].astype(str)
    j = (gpd.sjoin_nearest(sites[["geometry"]], psa, how="left", distance_col="_d")
            .assign(_n=lambda x: pd.to_numeric(x["PSA"], errors="coerce"))
            .reset_index().sort_values(["index", "_d", "_n"]).drop_duplicates("index").set_index("index"))
    sites["PSA"], sites["POLICEDISTRICT"] = j["PSA"], j["POLICEDISTRICT"]

    ll = sites.to_crs(4326)
    sites["LATITUDE"], sites["LONGITUDE"] = ll.geometry.y.round(7), ll.geometry.x.round(7)
    sites = pd.DataFrame(sites.drop(columns="geometry")).sort_values("SITE_ID").reset_index(drop=True)

    if not sites["SITE_ID"].is_unique:
        sys.exit("duplicate SITE_ID; files left untouched")
    if len(sites) < MIN_SITES:
        sys.exit(f"only {len(sites)} sites (floor {MIN_SITES}); files left untouched")

    # devices file, for the page's detail map
    cid = dict(zip(sm["cluster"], sm["SITE_ID"]))
    dev["SITE_ID"] = dev["INTERSECTIONKEY"].where(dev["INTERSECTIONKEY"].notna(),
                                                  mid["cluster"].map(cid).reindex(dev.index))
    devices = (pd.DataFrame(dev[dev["SITE_ID"].isin(sites["SITE_ID"])]
                            [["SITE_ID", "DEVICE", "DETAIL", "LATITUDE", "LONGITUDE"]])
                 .sort_values(["SITE_ID", "DEVICE", "LATITUDE", "LONGITUDE"]).reset_index(drop=True))

    sites.to_parquet(OUT_SITES, index=False)
    devices.to_parquet(OUT_DEVICES, index=False)
    print(f"{len(sites)} sites -> {OUT_SITES}; {len(devices)} devices -> {OUT_DEVICES}")
    print(pd.crosstab(sites["CONTROL"], sites["SITE_TYPE"], margins=True).to_string())
    print("strongest device:", sites["DEVICE"].value_counts().to_dict())


if __name__ == "__main__":
    main()
