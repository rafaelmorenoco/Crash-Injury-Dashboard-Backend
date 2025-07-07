import os
import pandas as pd
import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
from arcgis.features import FeatureLayer

# Increase pandas display options for debugging
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 150)

# 0. Constants
CRS = "EPSG:26985"
BASE_PATH = "/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files"

# 1. Helper to load a feature layer as a GeoDataFrame with just geometry + label


def load_improvement(env_var: str, name: str, where: str = "1=1") -> gpd.GeoDataFrame:
    url = os.environ.get(env_var)
    fl = FeatureLayer(url)
    resp = fl.query(where=where, out_fields="*", return_geometry=True)
    sdf = resp.sdf[["SHAPE"]]
    gdf = gpd.GeoDataFrame(sdf, geometry="SHAPE", crs=CRS)
    gdf["improvement"] = name
    return gdf


# 2. Load each improvement
gdf_lpi = load_improvement(
    "ARCGIS_FL_LPI",  "Leading Pedestrian Intervals (LPI)")
gdf_rrfb = load_improvement(
    "ARCGIS_FL_RRFB", "Rectangular Rapid Flashing Beacon (RRFB)", where="Status = 'Existing'")
gdf_ce = load_improvement("ARCGIS_FL_CE",   "Curb Extensions",
                          where="HASSAFETYIMP IN ('Tactical','Permanent')")
sls_where = (
    "SIGNSTATUS = 1 AND SIGNNUMBER = 20 AND "
    "SIGNCODE IN ('R2-1','o-ns-018','r-ns-037','R-NS-071',"
    "'R-NS-110','R-NS-104','r-ns-173','r2-12','S5-1','W13-7') "
    "AND SignDescription LIKE '%Speed Limit%'"
)
# gdf_sls = load_improvement(
#     "ARCGIS_FL_SLS", "20 MPH Speed Limit Signs", where=sls_where)
gdf_asap_p = load_improvement(
    "ARCGIS_FL_ASAP_P", "Annual Safety Improvement Program (ASAP) - Intersections", where="(ProjectStatus = 9 Or ProjectStatus = 10) And WorkType IN ('CPDO-MM-SAFETY', 'COO-HSIP-IMP', 'COO-PFCS-IMP', 'COO-PAS_IMP', 'CPDO-HS-SPMGT') And ProjectIdentifier IN ('TRAFFIC_SAFETY_2023', 'TRAFFIC_SAFETY_2022', 'TRAFFIC_SAFETY_2021', 'TRAFFIC_SAFETY_2024', 'TRAFFIC_SAFETY_2025', 'TRAFFIC_SAFETY_2026', 'TRAFFIC_SAFETY_2027')")
# gdf_asap_l = load_improvement(
#    "ARCGIS_FL_ASAP_L", "Annual Safety Improvement Program (ASAP)", where="ProjectStatus = 10 And WorkType IN ('CPDO-HS-SPMGT', 'CPDO-MM-SAFETY', 'COO-PFCS-IMP', 'COO-HSIP-IMP', 'COO-PAS_IMP') And ProjectIdentifier IN ('TRAFFIC_SAFETY_2021', 'TRAFFIC_SAFETY_2023', 'TRAFFIC_SAFETY_2022', 'TRAFFIC_SAFETY_2024', 'TRAFFIC_SAFETY_2027', 'TRAFFIC_SAFETY_2025', 'TRAFFIC_SAFETY_2026')")
# gdf_asap = pd.concat([gdf_asap_p, gdf_asap_l], ignore_index=True)

gdf_asap = gdf_asap_p

# 3. Read zone GeoJSONs and standardize ID fields
anc_path = f"{BASE_PATH}/anc_2023.geojson"
smd_path = f"{BASE_PATH}/smd_2023.geojson"
ward_path = f"{BASE_PATH}/Wards_from_2022.geojson"

gdf_anc = gpd.read_file(anc_path).to_crs(CRS)
gdf_anc["ANC"] = gdf_anc["ANC"].astype(str)

gdf_smd = gpd.read_file(smd_path).to_crs(CRS)
gdf_smd["SMD"] = gdf_smd["SMD"].astype(str)

gdf_ward = gpd.read_file(ward_path).to_crs(CRS)
gdf_ward["WARD"] = gdf_ward["WARD_ID"].astype(str)

# 4. Combine all improvements into one GeoDataFrame
gdf_all = pd.concat([gdf_lpi, gdf_rrfb, gdf_ce, gdf_asap], ignore_index=True)
gdf_all = gpd.GeoDataFrame(gdf_all, geometry="SHAPE", crs=CRS)

# 5. Spatial‐join improvements → ANC, SMD, Ward
#    We use predicate='within' so each point falls into the containing polygon.

# 5a. Tag ANC
gdf_all = (
    gpd.sjoin(
        gdf_all,
        gdf_anc[["ANC", "geometry"]],
        how="left",
        predicate="intersects"
    )
    .drop(columns=["index_right"])
)

# 5b. Tag SMD
gdf_all = (
    gpd.sjoin(
        gdf_all,
        gdf_smd[["SMD", "geometry"]],
        how="left",
        predicate="intersects"
    )
    .drop(columns=["index_right"])
)

# 5c. Tag Ward
gdf_all = (
    gpd.sjoin(
        gdf_all,
        gdf_ward[["WARD", "geometry"]],
        how="left",
        predicate="intersects"
    )
    .drop(columns=["index_right"])
)

# 6. Drop geometry, count per combination
df = pd.DataFrame(gdf_all.drop(columns="SHAPE"))
counts = (
    df
    .groupby(["improvement", "WARD", "ANC", "SMD"])
    .size()
    .reset_index(name="Count")
)

# 7. (Optional) Fill missing combinations with zero
#    If you want zeros for every Ward×ANC×SMD×improvement, uncomment:

imps = counts["improvement"].unique()
wards = counts["WARD"].unique()
ancs = counts["ANC"].unique()
smds = counts["SMD"].unique()
mi = pd.MultiIndex.from_product([imps, wards, ancs, smds],
                                names=["improvement", "WARD", "ANC", "SMD"])
counts = (counts
          .set_index(["improvement", "WARD", "ANC", "SMD"])
          .reindex(mi, fill_value=0)
          .reset_index()
          )

counts["Count"] = counts["Count"].astype(float)

# 8. Write single Parquet
table = pa.Table.from_pandas(counts)
pq.write_table(table, "interventions.parquet")
