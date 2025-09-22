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

# Constants
BASE_PATH = "/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files"

# Read hin polygons GeoJSON
hin_path = f"{BASE_PATH}/hin-polygon-clean.geojson"
gdf_hin = gpd.read_file(hin_path)

gdf_hin = gdf_hin.drop(columns=['geometry'])

# Convert back to DataFrame
df_hin = pd.DataFrame(gdf_hin)

# Create HIN_TIER column based on TIER values


def determine_hin_tier(row):
    """Determine HIN tier based on TIER columns"""
    if row['TIER_1'] == 1:
        return '1'
    elif row['TIER_2'] == 1:
        return '2'
    elif row['TIER_3'] == 1:
        return '3'
    else:
        return None


# Apply the tier determination function
df_hin['HIN_TIER'] = df_hin.apply(determine_hin_tier, axis=1)

df_hin = df_hin[['GIS_ID', 'HIN_TIER', 'ROUTENAME']]

parquet_schema = pa.Table.from_pandas(df=df_hin).schema
table = pa.Table.from_pandas(df_hin, parquet_schema)

output_file = 'hin_polygon.parquet'
pq.write_table(table, output_file)
