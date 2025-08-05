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
CRS = "EPSG:26985"
BASE_PATH = "/workspaces/Crash-Injury-Dashboard-Backend/Spatial-Files"

# Read roads GeoJSON
roads_path = f"{BASE_PATH}/Roads.geojson"
gdf_roads = gpd.read_file(roads_path).to_crs(CRS)

# Read hin GeoJSON
hin_path = f"{BASE_PATH}/High_Injury_Network.geojson"
gdf_hin = gpd.read_file(hin_path).to_crs(CRS)

# Spatial‐join roads → hin
gdf_roads_hin = (
    gpd.sjoin(
        gdf_roads,
        gdf_hin,
        how="inner",
        predicate="intersects"
    )
    .drop(columns=["index_right"])
)

'''
# Plotting the roads with HIN polygons

import matplotlib.pyplot as plt

# 1. Create figure and axis
fig, ax = plt.subplots(figsize=(10, 10))

# 2. Plot roads in black, 1-pixel line width
gdf_roads_hin.plot(
    ax=ax,
    color="black",
    linewidth=1
)

# 3. Remove axis ticks and labels for a cleaner map
ax.set_axis_off()

# 4. Show
plt.show()
'''

# Write resulting GeoJSON
gdf_roads_hin = gdf_roads_hin.to_crs("EPSG:4326")
gdf_roads_hin.to_file("hin-polygons.geojson", driver="GeoJSON")
