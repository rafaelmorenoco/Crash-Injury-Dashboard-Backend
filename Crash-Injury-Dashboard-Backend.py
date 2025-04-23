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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fetch_all_features(url, where="1=1", outFields="*", outSR="4326", f="json"):
    """
    Fetches all features from the provided ESRI REST API URL handling pagination.
    
    Parameters:
        url (str): The API endpoint.
        where (str): SQL-like where clause for filtering. Default is to retrieve all records.
        outFields (str): Fields to be returned. Default is '*' for all fields.
        outSR (str): Spatial reference of the output. Default is '4326'.
        f (str): Format of the returned data. Default is 'json'.
    
    Returns:
        DataFrame: A pandas DataFrame containing all the feature attributes.
    """
    # Initialize parameters for the request
    params = {
        "where": where,
        "outFields": outFields,
        "outSR": outSR,
        "f": f,
        "resultOffset": 0
    }
    
    all_attributes = []
    
    logger.info(f"Fetching data from {url}")
    
    while True:
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise exception for HTTP errors
            data = response.json()
            
            features = data.get("features", [])
            if not features:
                break  # Exit loop if no more features returned
            
            # Extract and append the feature attributes
            batch_attributes = [feature.get("attributes", {}) for feature in features]
            all_attributes.extend(batch_attributes)
            
            # Determine the maximum number of records returned per call, if available
            max_record_count = data.get("maxRecordCount", len(features))
            
            # If fewer features than the max count were returned, we've reached the end of the results
            if len(features) < max_record_count:
                break
            
            # Update the offset for the next call
            params["resultOffset"] += len(features)
            
            logger.info(f"Fetched {len(batch_attributes)} features (total: {len(all_attributes)})")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching data: {e}")
            break
    
    logger.info(f"Completed fetching {len(all_attributes)} total features")
    return pd.DataFrame(all_attributes)

def determine_severity(row):
    """Determine crash severity based on injury flags"""
    if row['MINORINJURY'] == 'Y':
        return 'MINORINJURY'
    elif row['MAJORINJURY'] == 'Y':
        return 'MAJORINJURY'
    else:
        return 'NOINJURY'

def process_crash_point_data():
    """Process crash point and details data from DC GIS"""
    logger.info("Processing crash point and crash details data")
    
    # URLs for the two tables
    crashpt_url = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/24/query"
    crashdetails_url = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/25/query"
    
    # Retrieve DataFrames for both tables
    df_crashpt = fetch_all_features(crashpt_url)
    df_crashdetails = fetch_all_features(crashdetails_url)
    
    # Merge the dataframes on CRIMEID using a right join
    df_cp_cd = pd.merge(df_crashpt, df_crashdetails, on='CRIMEID', how='right')
    
    # Select and rename columns
    df_cp_cd = df_cp_cd[['OBJECTID_y', 'CRIMEID', 'CCN_y', 'REPORTDATE',
                          'PERSONID', 'PERSONTYPE', 'AGE', 'FATAL',
                          'MAJORINJURY', 'MINORINJURY', 'VEHICLEID',
                          'INVEHICLETYPE', 'TICKETISSUED', 'LICENSEPLATESTATE',
                          'IMPAIRED', 'SPEEDING', 'ROUTEID', 'STREETSEGID',
                          'ROADWAYSEGID','ADDRESS', 'LATITUDE', 'LONGITUDE',
                          'EVENTID', 'BLOCKKEY', 'SUBBLOCKKEY', 'CORRIDORID']]
    
    df_cp_cd = df_cp_cd.rename(columns={
        'OBJECTID_y': 'OBJECTID', 
        'CCN_y': 'CCN', 
        'PERSONTYPE': 'MODE'
    })
    
    # Convert timestamps to datetime with proper timezone conversion
    df_cp_cd['REPORTDATE'] = (
        pd.to_datetime(df_cp_cd['REPORTDATE'], unit='ms')
          .dt.tz_localize('UTC')
          .dt.tz_convert('America/New_York')
    )

    # Set the LAST_RECORD column to the maximum REPORTDATE
    df_cp_cd['LAST_RECORD'] = df_cp_cd['REPORTDATE'].max()

    # ------------------ New Logging Block ------------------
    # Log how many days per year (from 2018 onward) had no crash records.
    # We first define "recorded" crash days by normalizing REPORTDATE to remove the time component.
    now = pd.Timestamp.now(tz="America/New_York").normalize()
    for year in range(2018, now.year + 1):
        # Define start and end dates for the year. For the current year, use today's date.
        start_date = pd.Timestamp(year=year, month=1, day=1, tz="America/New_York")
        if year == now.year:
            end_date = now
        else:
            end_date = pd.Timestamp(year=year, month=12, day=31, tz="America/New_York")
        
        # Generate all days in the given range
        all_days = pd.date_range(start_date, end_date, freq="D")
        total_days = len(all_days)
        
        # Get unique days with crash records for this year by normalizing the REPORTDATE timestamps.
        recorded_days = df_cp_cd[df_cp_cd['REPORTDATE'].dt.year == year]['REPORTDATE'].dt.normalize().unique()
        # Convert to a set of date objects for easier comparison.
        recorded_days_set = {ts.date() for ts in recorded_days}
        days_with_records = len(recorded_days_set)
        days_without_records = total_days - days_with_records
        
        logger.info(f"Year {year}: {days_without_records} days without crash records.")
    # ---------------- End Logging Block ---------------------
    
    # Apply severity determination
    df_cp_cd['SEVERITY'] = df_cp_cd.apply(determine_severity, axis=1)
    
    # Filter to only injuries
    df_cp_cd = df_cp_cd[df_cp_cd['SEVERITY'] != 'NOINJURY']
    
    # Clean up data
    df_cp_cd['SEVERITY'] = df_cp_cd['SEVERITY'].replace({
        'MAJORINJURY': 'Major', 
        'MINORINJURY': 'Minor'
    })
    df_cp_cd['COUNT'] = 1
    
    # Create a cutoff for the last 30 days and fail if no recent records
    cutoff_date = pd.Timestamp.now(tz="America/New_York") - pd.Timedelta(days=30)
    recent_records = df_cp_cd[df_cp_cd['REPORTDATE'] >= cutoff_date]
    if recent_records.empty:
        logger.error("No crash records in the last 30 days.")
        # Raising an exception will cause the GitHub Action to fail.
        raise Exception("No crash records in the last 30 days. Failing GitHub Action.")
    
    logger.info(f"Processed {len(df_cp_cd)} injury crash records")
    return df_cp_cd
    
def process_fatality_data():
    """Process fatality data from ArcGIS"""
    logger.info("Processing fatality data from ArcGIS")
    
    try:
        # Get credentials from environment variables
        client_id = os.environ.get('ARCGIS_CLIENT_ID')
        client_secret = os.environ.get('ARCGIS_CLIENT_SECRET')
        feature_layer_id = os.environ.get('ARCGIS_FEATURE_LAYER_ID')
        
        if not all([client_id, client_secret, feature_layer_id]):
            logger.error("Missing ArcGIS credentials in environment variables")
            return pd.DataFrame()
        
        # Connect to ArcGIS
        gis = GIS("https://dcgis.maps.arcgis.com", client_id=client_id, client_secret=client_secret)
        feature_layer_item = gis.content.get(feature_layer_id)
        feature_layer = feature_layer_item.layers[0]  # Access the first layer in the item
        
        # Query all features
        features = feature_layer.query(where="1=1", out_fields="*")
        df_f = features.sdf
        
        # Sort and convert to GeoDataFrame
        df_fs = df_f.sort_values(by='datetime', ascending=False)
        gdf_f = gpd.GeoDataFrame(df_fs, geometry='SHAPE', crs=4326)
        
        # Extract coordinates
        gdf_f['LATITUDE'] = gdf_f['SHAPE'].y
        gdf_f['LONGITUDE'] = gdf_f['SHAPE'].x
        
        # Clean up data
        gdf_f['vehicle_type'] = gdf_f['vehicle_type'].replace({
            'pedestrian': 'Pedestrian',
            'driver': 'Driver',
            'motorcycle': 'Motorcyclist*',
            'passenger': 'Passenger',
            'bicyclist': 'Bicyclist',
            'sco': 'Scooter*',
            'unknown': 'Unknown'
        })
        
        gdf_f['SEVERITY'] = 'Fatal'
        
        # Rename columns for consistency
        gdf_f = gdf_f.rename(columns={
            'objectid': 'OBJECTID',
            'ccn': 'CCN',
            'datetime': 'REPORTDATE',
            'vehicle_type': 'MODE',
            'address_location': 'ADDRESS',
            'age_years': 'AGE',
            'crash_type': 'StrinkingVehicle',
            'site_visit': 'SiteVisitStatus',
            'second_striking_vehicleobject': 'SecondStrikingVehicleObject',
            'factors_discussed_at_site': 'FactorsDiscussedAtSiteVisit',
            'actions_planned_completed': 'ActionsPlannedAndCompleted',
            'actions_under_consideration': 'ActionsUnderConsideration'
        })
        
        # Select columns
        gdf_f = gdf_f[['OBJECTID', 'CCN', 'MODE', 'SEVERITY', 'REPORTDATE', 'ADDRESS', 'AGE',
                       'StrinkingVehicle', 'SecondStrikingVehicleObject', 'SiteVisitStatus',
                       'FactorsDiscussedAtSiteVisit', 'ActionsPlannedAndCompleted',
                       'ActionsUnderConsideration', 'LATITUDE', 'LONGITUDE']]

        gdf_f['AGE'] = gdf_f['AGE'].astype(float)
        gdf_f['COUNT'] = 1
        gdf_f['REPORTDATE'] = (
        pd.to_datetime(gdf_f['REPORTDATE'], unit='ms')
          .dt.tz_localize('UTC')
          .dt.tz_convert('America/New_York')
        )

        # Set the LAST_RECORD column to the maximum REPORTDATE
        gdf_f['LAST_RECORD'] = gdf_f['REPORTDATE'].max()
        
        logger.info(f"Processed {len(gdf_f)} fatality records")
        return gdf_f
        
    except Exception as e:
        logger.error(f"Error processing fatality data: {e}")
        return pd.DataFrame()

def combine_and_process_data(injury_data, fatality_data):
    """Combine injury and fatality data and perform spatial joins"""
    logger.info("Combining and processing data")
    
    # Standardize datetime formats
    injury_data['REPORTDATE'] = injury_data['REPORTDATE'].dt.tz_localize(None).astype('datetime64[ns]')
    injury_data['LAST_RECORD'] = injury_data['LAST_RECORD'].dt.tz_localize(None).astype('datetime64[ns]')
    fatality_data['REPORTDATE'] = fatality_data['REPORTDATE'].dt.tz_localize(None).astype('datetime64[ns]')
    fatality_data['LAST_RECORD'] = fatality_data['LAST_RECORD'].dt.tz_localize(None).astype('datetime64[ns]')
    
    # Merge the dataframes
    combined_df = pd.merge(
        fatality_data, injury_data, 
        how='outer', 
        on=['OBJECTID', 'CCN', 'MODE', 'SEVERITY', 'REPORTDATE', 'AGE', 'LATITUDE', 'LONGITUDE', 'COUNT', 'ADDRESS','LAST_RECORD']
    )
    
    # Get the workflow trigger type from environment variable
    # GitHub Actions sets GITHUB_EVENT_NAME automatically
    github_event_name = os.environ.get('GITHUB_EVENT_NAME', '')
    logger.info(f"GitHub event type: {github_event_name}")
    
    # Only check timestamp if running from scheduled job
    if github_event_name == 'schedule':
        last_record_timestamp = combined_df['LAST_RECORD'].max()
        logger.info(f"Current LAST_RECORD timestamp: {last_record_timestamp}")
        
        # Check if previous timestamp file exists
        last_timestamp_file = 'last_record_timestamp.txt'
        if os.path.exists(last_timestamp_file):
            with open(last_timestamp_file, 'r') as f:
                prev_timestamp_str = f.read().strip()
                if prev_timestamp_str:
                    prev_timestamp = pd.Timestamp(prev_timestamp_str)
                    logger.info(f"Previous LAST_RECORD timestamp: {prev_timestamp}")
                    
                    # Compare timestamps
                    if prev_timestamp == last_record_timestamp:
                        logger.error("No new data since last run - LAST_RECORD timestamp is the same")
                        raise Exception("LAST_RECORD timestamp unchanged since previous run. Failing GitHub Action.")
        
        # Save current timestamp for next run
        with open(last_timestamp_file, 'w') as f:
            f.write(str(last_record_timestamp))
    else:
        logger.info("Manual run detected - skipping timestamp comparison")
    
    # Remove rows with missing location data
    combined_df = combined_df.dropna(subset=['LATITUDE'])
    
    # Convert to GeoDataFrame
    gdf = gpd.GeoDataFrame(
        combined_df, 
        geometry=gpd.points_from_xy(combined_df.LONGITUDE, combined_df.LATITUDE, crs=4326)
    )
    
    logger.info(f"Combined data has {len(gdf)} records")
    
    # Perform spatial joins
    try:
        # Read hexagon grid polygons
        hex_path = 'Spatial-Files/crash-hexgrid.geojson'
        logger.info(f"Reading hex grid from {hex_path}")
        hex_grid = gpd.read_file(hex_path)
        hex_grid = hex_grid.to_crs(4326)
        
        # Add 'HEX_' prefix to grid_id
        hex_grid['grid_id'] = hex_grid['grid_id'].apply(lambda x: f'HEX_{x}')
        
        # Join spatially hexgrid to crashes
        logger.info("Performing spatial join with hex grid")
        gdf_hex = gpd.sjoin(gdf, hex_grid, how='left')
        gdf_hex = gdf_hex.drop(columns=['index_right'])
        
        # Read ANC polygons
        anc_path = 'Spatial-Files/anc_2023.geojson'
        logger.info(f"Reading ANC polygons from {anc_path}")
        anc = gpd.read_file(anc_path)
        anc = anc.to_crs(4326)
        anc = anc[['ANC', 'geometry']]
        
        # Join spatially ANC to crashes
        logger.info("Performing spatial join with ANC boundaries")
        gdf_hex_anc = gpd.sjoin(gdf_hex, anc, how='left')
        gdf_hex_anc = gdf_hex_anc.drop(columns=['index_right'])
        
        # Read SMD polygons
        smd_path = 'Spatial-Files/Single_Member_District_from_2023.geojson'
        logger.info(f"Reading SMD polygons from {smd_path}")
        smd = gpd.read_file(smd_path)
        smd = smd.to_crs(4326)
        smd = smd[['SMD_ID', 'geometry']]
        
        # Join spatially SMD to crashes
        logger.info("Performing spatial join with SMD boundaries")
        gdf_hex_anc_smd = gpd.sjoin(gdf_hex_anc, smd, how='left')
        gdf_hex_anc_smd = gdf_hex_anc_smd.drop(columns=['index_right'])
        
        # Join spatially WARD to crashes
        ward_path = 'Spatial-Files/Wards_from_2022.geojson'
        logger.info(f"Reading WARD polygons from {ward_path}")
        wards = gpd.read_file(ward_path)
        wards = wards.to_crs(4326)
        wards = wards[['WARD_ID', 'geometry']]
        
        logger.info("Performing spatial join with WARD boundaries")
        gdf_hex_anc_smd = gpd.sjoin(gdf_hex_anc_smd, wards, how='left')
        gdf_hex_anc_smd = gdf_hex_anc_smd.drop(columns=['index_right'])
        # -----------------------------------------
        
        # Rename columns for consistency
        gdf_hex_anc_smd = gdf_hex_anc_smd.rename(columns={
            'grid_id': 'GRID_ID',
            'SMD_ID': 'SMD',
            'WARD_ID': 'WARD'
        })
        
        # Drop the geometry column to create a plain DataFrame result
        gdf_hex_anc_smd = gdf_hex_anc_smd.drop(columns=['geometry'])
        
        # Convert back to DataFrame
        crash_hex = pd.DataFrame(gdf_hex_anc_smd)
        
        logger.info("Spatial joins completed successfully")
        return crash_hex
        
    except Exception as e:
        logger.error(f"Error in spatial processing: {e}")
        # Return the original data if spatial processing fails
        return pd.DataFrame(combined_df.drop(columns=['geometry']) if 'geometry' in combined_df.columns else combined_df)

def finalize_data(crash_data):
    """Perform final data cleaning and save as parquet"""
    # Final cleanup
    crash_data = crash_data.dropna(subset=['MODE'])
    crash_data['OBJECTID'] = crash_data['OBJECTID'].astype(str)
    crash_data = crash_data.sort_values(by='REPORTDATE', ascending=False)
    
    # Assign system timestamp to a new column
    crash_data['LAST_UPDATE'] = pd.Timestamp.now(tz='America/New_York')
    crash_data['LAST_UPDATE'] = crash_data['LAST_UPDATE'].dt.tz_localize(None).astype('datetime64[ns]')
    
    logger.info(f"Final dataset has {len(crash_data)} records")
    
    # Create Arrow table and save to parquet
    try:
        parquet_schema = pa.Table.from_pandas(df=crash_data).schema
        table = pa.Table.from_pandas(crash_data, parquet_schema)
        
        output_file = 'crashes.parquet'
        pq.write_table(table, output_file)
        logger.info(f"Data successfully saved to {output_file}")
        
    except Exception as e:
        logger.error(f"Error saving parquet file: {e}")

# Suppress Fiona's warnings
logging.getLogger('fiona').setLevel(logging.CRITICAL)

def main():
    """Main function to orchestrate the data processing pipeline"""
    logger.info("Starting crash data processing")
    
    try:
        # Process injury data
        injury_data = process_crash_point_data()
        
        # Process fatality data
        fatality_data = process_fatality_data()
        
        # Combine and process data with spatial joins
        combined_data = combine_and_process_data(injury_data, fatality_data)
        
        # Finalize and save data
        finalize_data(combined_data)
        
        logger.info("Data processing completed successfully")
        
    except Exception as e:
        logger.error(f"Error in data processing pipeline: {e}")

if __name__ == "__main__":
    main()
