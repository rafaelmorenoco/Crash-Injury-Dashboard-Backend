# Load the necessary libraries
import os
import logging
import pandas as pd
import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from arcgis.gis import GIS
from arcgis.features import FeatureLayer

# Get credentials from environment variables
client_id = os.environ.get('ARCGIS_CLIENT_ID')
client_secret = os.environ.get('ARCGIS_CLIENT_SECRET')
feature_layer_ts = os.environ.get('ARCGIS_FEATURE_LAYER_TrafficSignals_LPI')

# Connect to ArcGIS
gis = GIS("https://dcgis.maps.arcgis.com",
          client_id=client_id, client_secret=client_secret)
feature_layer_item = gis.content.get(feature_layer_ts)
# Access the first layer in the item
feature_layer = feature_layer_item.layers[0]

# Query all features
features = feature_layer.query(where="1=1", out_fields="*")
ts_lpi = features.sdf