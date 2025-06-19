import os
import logging
import pandas as pd
import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from arcgis.gis import GIS
from arcgis.features import FeatureLayer

# Increase pandas display options for debugging
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 150)

# Retrieve featurelayer URLs from environment variables
featurelayer_lpi = os.environ.get('ARCGIS_FL_LPI')
featurelayer_rrfb = os.environ.get('ARCGIS_FL_RRFB')
featurelayer_ce = os.environ.get('ARCGIS_FL_CE')
featurelayer_sls = os.environ.get('ARCGIS_FL_SLS')

# -----------------------------
# 1. Leading Pedestrian Intervals (LPI)
# -----------------------------
feature_layer_lpi = FeatureLayer(featurelayer_lpi)
response_lpi = feature_layer_lpi.query(
    where="1=1", out_fields="*", return_geometry=True)
df_lpi = response_lpi.sdf
df_lpi['LPI'] = 1  # Assign a count of 1 per record
df_lpi = df_lpi[['LPI', 'SHAPE']]
gdf_lpi = gpd.GeoDataFrame(df_lpi, geometry='SHAPE', crs="EPSG:26985")

# -----------------------------
# 2. Rectangular Rapid Flashing Beacons (RRFB)
# -----------------------------
feature_layer_rrfb = FeatureLayer(featurelayer_rrfb)
response_rrfb = feature_layer_rrfb.query(
    where="Status = 'Existing'", out_fields="*", return_geometry=True)
df_rrfb = response_rrfb.sdf
df_rrfb['RRFB'] = 1  # Assign a count of 1 per record
df_rrfb = df_rrfb[['RRFB', 'SHAPE']]
gdf_rrfb = gpd.GeoDataFrame(df_rrfb, geometry='SHAPE', crs="EPSG:26985")

# -----------------------------
# 3. Curb Extensions (CE)
# -----------------------------
feature_layer_ce = FeatureLayer(featurelayer_ce)
response_ce = feature_layer_ce.query(
    where="HASSAFETYIMP IN ('Tactical','Permanent')", out_fields="*", return_geometry=True)
df_ce = response_ce.sdf
df_ce['CE'] = 1  # Assign a count of 1 per record
df_ce = df_ce[['CE', 'SHAPE']]
gdf_ce = gpd.GeoDataFrame(df_ce, geometry='SHAPE', crs="EPSG:26985")

# -----------------------------
# 4. 20 MPH Speed Limit Signs (SLS)
# -----------------------------
feature_layer_sls = FeatureLayer(featurelayer_sls)
where_clause = (
    "SIGNSTATUS = 1 AND SIGNNUMBER = 20 AND "
    "SIGNCODE IN ('R2-1', 'o-ns-018', 'r-ns-037', 'R-NS-071', 'R-NS-110', 'R-NS-104', "
    "'r-ns-173', 'r2-12', 'S5-1', 'W13-7') AND "
    "SignDescription LIKE '%Speed Limit%'"
)
response_sls = feature_layer_sls.query(
    where=where_clause, out_fields="*", return_geometry=True)
df_sls = response_sls.sdf
df_sls['SLS'] = 1  # Assign a count of 1 per record
df_sls = df_sls[['SLS', 'SHAPE']]
gdf_sls = gpd.GeoDataFrame(df_sls, geometry='SHAPE', crs="EPSG:26985")

# -----------------------------
# 5. Read Hexagon Grid Polygons
# -----------------------------
hex_path = '/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files/crash-hexgrid.geojson'
hex_grid = gpd.read_file(hex_path)
hex_grid = hex_grid.to_crs(26985)
# Add 'HEX_' prefix to each grid_id
hex_grid['grid_id'] = hex_grid['grid_id'].apply(lambda x: f'HEX_{x}')

# -----------------------------
# 6. Spatial Join & Aggregation
# -----------------------------

# 6a. LPI: Join hex_grid to gdf_lpi and aggregate LPI counts.
hex_lpi = gpd.sjoin(hex_grid, gdf_lpi, how='left', predicate='contains')
hex_lpi = hex_lpi.groupby('grid_id')['LPI'].sum()
hex_grid['LPI'] = hex_grid['grid_id'].map(hex_lpi).fillna(0)

# 6b. RRFB: Join hex_grid to gdf_rrfb and aggregate RRFB counts.
hex_rrfb = gpd.sjoin(hex_grid, gdf_rrfb, how='left', predicate='contains')
hex_rrfb = hex_rrfb.groupby('grid_id')['RRFB'].sum()
hex_grid['RRFB'] = hex_grid['grid_id'].map(hex_rrfb).fillna(0)

# 6c. SLS: Join hex_grid to gdf_sls and aggregate 20 MPH Speed Limit Sign counts.
hex_sls = gpd.sjoin(hex_grid, gdf_sls, how='left', predicate='contains')
hex_sls = hex_sls.groupby('grid_id')['SLS'].sum()
hex_grid['SLS'] = hex_grid['grid_id'].map(hex_sls).fillna(0)

# 6d. CE: Join hex_grid to gdf_ce and aggregate Curb Extension counts.
hex_ce = gpd.sjoin(hex_grid, gdf_ce, how='left', predicate='contains')
hex_ce = hex_ce.groupby('grid_id')['CE'].sum()
hex_grid['CE'] = hex_grid['grid_id'].map(hex_ce).fillna(0)

# -----------------------------
# 7. Output
# -----------------------------
# Remove the geometry column for final output
hex_grid = pd.DataFrame(hex_grid.drop(columns=['geometry']))
# Save the updated hex_grid as a parquet file
parquet_schema = pa.Table.from_pandas(df=hex_grid).schema
table = pa.Table.from_pandas(hex_grid, parquet_schema)

output_file = 'crash-hexgrid.parquet'
pq.write_table(table, output_file)

# ----------------------------------------------------------------
# Process Each Spatial File Separately
# ----------------------------------------------------------------
# The following sections create one aggregated DataFrame per file.

# ----------------------------
# Process ANC (anc_2023.geojson)
# Native ID field: "ANC"
# ----------------------------
anc_path = '/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files/anc_2023.geojson'
anc_id_field = 'ANC'

gdf_anc = gpd.read_file(anc_path)
gdf_anc = gdf_anc.to_crs(26985)
gdf_anc[anc_id_field] = gdf_anc[anc_id_field].astype(str)

# Aggregate LPI
join_lpi = gpd.sjoin(gdf_anc, gdf_lpi, how='left', predicate='contains')
agg_lpi = join_lpi.groupby(anc_id_field)['LPI'].sum()
gdf_anc['LPI'] = gdf_anc[anc_id_field].map(agg_lpi).fillna(0)

# Aggregate RRFB
join_rrfb = gpd.sjoin(gdf_anc, gdf_rrfb, how='left', predicate='contains')
agg_rrfb = join_rrfb.groupby(anc_id_field)['RRFB'].sum()
gdf_anc['RRFB'] = gdf_anc[anc_id_field].map(agg_rrfb).fillna(0)

# Aggregate SLS
join_sls = gpd.sjoin(gdf_anc, gdf_sls, how='left', predicate='contains')
agg_sls = join_sls.groupby(anc_id_field)['SLS'].sum()
gdf_anc['SLS'] = gdf_anc[anc_id_field].map(agg_sls).fillna(0)

# Aggregate CE
join_ce = gpd.sjoin(gdf_anc, gdf_ce, how='left', predicate='contains')
agg_ce = join_ce.groupby(anc_id_field)['CE'].sum()
gdf_anc['CE'] = gdf_anc[anc_id_field].map(agg_ce).fillna(0)

anc_df = pd.DataFrame(gdf_anc.drop(columns=['geometry']))
anc_df[['LPI', 'RRFB', 'SLS', 'CE']] = anc_df[[
    'LPI', 'RRFB', 'SLS', 'CE']].astype(float)

# Save as parquet file
parquet_schema = pa.Table.from_pandas(df=anc_df).schema
table = pa.Table.from_pandas(anc_df, parquet_schema)

output_file = 'anc_2023.parquet'
pq.write_table(table, output_file)

# ----------------------------
# Process SMD (smd_2023.geojson)
# Native ID field: "SMD"
# ----------------------------
smd_path = '/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files/smd_2023.geojson'
smd_id_field = 'SMD'

gdf_smd = gpd.read_file(smd_path)
gdf_smd = gdf_smd.to_crs(26985)
gdf_smd[smd_id_field] = gdf_smd[smd_id_field].astype(str)

# Aggregate LPI
join_lpi = gpd.sjoin(gdf_smd, gdf_lpi, how='left', predicate='contains')
agg_lpi = join_lpi.groupby(smd_id_field)['LPI'].sum()
gdf_smd['LPI'] = gdf_smd[smd_id_field].map(agg_lpi).fillna(0)

# Aggregate RRFB
join_rrfb = gpd.sjoin(gdf_smd, gdf_rrfb, how='left', predicate='contains')
agg_rrfb = join_rrfb.groupby(smd_id_field)['RRFB'].sum()
gdf_smd['RRFB'] = gdf_smd[smd_id_field].map(agg_rrfb).fillna(0)

# Aggregate SLS
join_sls = gpd.sjoin(gdf_smd, gdf_sls, how='left', predicate='contains')
agg_sls = join_sls.groupby(smd_id_field)['SLS'].sum()
gdf_smd['SLS'] = gdf_smd[smd_id_field].map(agg_sls).fillna(0)

# Aggregate CE
join_ce = gpd.sjoin(gdf_smd, gdf_ce, how='left', predicate='contains')
agg_ce = join_ce.groupby(smd_id_field)['CE'].sum()
gdf_smd['CE'] = gdf_smd[smd_id_field].map(agg_ce).fillna(0)

smd_df = pd.DataFrame(gdf_smd.drop(columns=['geometry']))
smd_df[['LPI', 'RRFB', 'SLS', 'CE']] = smd_df[[
    'LPI', 'RRFB', 'SLS', 'CE']].astype(float)

# Save as parquet file
parquet_schema = pa.Table.from_pandas(df=smd_df).schema
table = pa.Table.from_pandas(smd_df, parquet_schema)

output_file = 'smd_2023.parquet'
pq.write_table(table, output_file)

# ----------------------------
# Process Wards (Wards_from_2022.geojson)
# Native ID field: "WARD_ID"
# ----------------------------
ward_path = '/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files/Wards_from_2022.geojson'
ward_id_field = 'WARD_ID'

gdf_ward = gpd.read_file(ward_path)
gdf_ward = gdf_ward.to_crs(26985)
gdf_ward[ward_id_field] = gdf_ward[ward_id_field].astype(str)

# Aggregate LPI
join_lpi = gpd.sjoin(gdf_ward, gdf_lpi, how='left', predicate='contains')
agg_lpi = join_lpi.groupby(ward_id_field)['LPI'].sum()
gdf_ward['LPI'] = gdf_ward[ward_id_field].map(agg_lpi).fillna(0)

# Aggregate RRFB
join_rrfb = gpd.sjoin(gdf_ward, gdf_rrfb, how='left', predicate='contains')
agg_rrfb = join_rrfb.groupby(ward_id_field)['RRFB'].sum()
gdf_ward['RRFB'] = gdf_ward[ward_id_field].map(agg_rrfb).fillna(0)

# Aggregate SLS
join_sls = gpd.sjoin(gdf_ward, gdf_sls, how='left', predicate='contains')
agg_sls = join_sls.groupby(ward_id_field)['SLS'].sum()
gdf_ward['SLS'] = gdf_ward[ward_id_field].map(agg_sls).fillna(0)

# Aggregate CE
join_ce = gpd.sjoin(gdf_ward, gdf_ce, how='left', predicate='contains')
agg_ce = join_ce.groupby(ward_id_field)['CE'].sum()
gdf_ward['CE'] = gdf_ward[ward_id_field].map(agg_ce).fillna(0)

ward_df = pd.DataFrame(gdf_ward.drop(columns=['geometry']))
ward_df = ward_df[['WARD', 'NAME', 'WARD_ID', 'LPI', 'RRFB', 'SLS', 'CE']]
ward_df[['WARD', 'LPI', 'RRFB', 'SLS', 'CE']] = ward_df[[
    'WARD', 'LPI', 'RRFB', 'SLS', 'CE']].astype(float)

# Save as parquet file
parquet_schema = pa.Table.from_pandas(df=ward_df).schema
table = pa.Table.from_pandas(ward_df, parquet_schema)

output_file = 'wards_2022.parquet'
pq.write_table(table, output_file)
