# Load the necessary libraries
import os
import time
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

# Crash-level "other-injury" columns. No count column exists for
# "other", so presence is inferred only when an "other" party was injured.
OTHER_COLS = ['MAJORINJURIESOTHER', 'MINORINJURIESOTHER',
              'UNKNOWNINJURIESOTHER', 'FATALOTHER']

# Crash-level impaired-party counts (from the crash point table, layer 24).
# These are per-crash aggregates, so they repeat across every person row in a crash.
IMPAIRED_COLS = ['PEDESTRIANSIMPAIRED',
                 'BICYCLISTSIMPAIRED', 'DRIVERSIMPAIRED']

# Fatality striking-party relabeling. Fixed Object -> None (not a party, so the
# crash collapses to a single-X). Motorcycle / Scooter are broken out with a *.
FATAL_PARTY_RELABEL = {
    'Pedestrian': 'pedestrian',
    'Motor Vehicle': 'motor vehicle',
    'Bicycle': 'bicycle',
    'Motorcycle': 'motorcycle*',                                # fatal-only
    'Scooter / Personal Mobility Device': 'standing scooter*',  # fatal-only
    'Unknown': 'unknown',
    'Fixed Object': None,                                       # not a party
}

# Canonical ordering for two-party labels: motorized -> vulnerable.
FATAL_PARTY_RANK = {
    'motor vehicle': 1,
    'motorcycle*': 2,
    'standing scooter*': 3,
    'bicycle': 4,
    'pedestrian': 5,
    'unknown': 6,
}


def _count_label(n, noun):
    """Label for a single-mode crash, e.g. 'single motor vehicle',
    'motor vehicle - motor vehicle', 'multiple motor vehicles'."""
    if n == 1:
        return f'single {noun}'
    if n == 2:
        return f'{noun} - {noun}'
    return f'multiple {noun}s'          # n > 2


def classify_crash_type(row):
    """Classify an injury crash from crash-level counts.

    Vulnerable road users take priority over vehicle count; vehicle/bicycle
    counts only matter for single-mode crashes. 3+ distinct modes collapse to
    'multi-party'. Requires TOTAL_VEHICLES / TOTAL_PEDESTRIANS / TOTAL_BICYCLES
    and OTHER_COLS to be NaN-filled with 0 beforehand.
    """
    veh = int(row['TOTAL_VEHICLES'])
    bikes = int(row['TOTAL_BICYCLES'])
    peds = row['TOTAL_PEDESTRIANS'] > 0
    other = any(row[c] > 0 for c in OTHER_COLS)   # injury-only, no count

    v, b = veh > 0, bikes > 0
    present = sum([v, other, b, peds])

    if present == 0:
        return 'unclassified'

    # Single-mode crashes keep a count where the data supports one
    if present == 1:
        if v:
            return _count_label(veh, 'motor vehicle')
        if b:
            return _count_label(bikes, 'bicycle')
        if peds:
            return 'pedestrian only'
        if other:
            return 'other'                # no count column for 'other'

    if present >= 3:
        return 'multi-party'

    # present == 2: name the pair, ordered motorized -> vulnerable
    parts = []
    if v:
        parts.append('motor vehicle')
    if other:
        parts.append('other')
    if b:
        parts.append('bicycle')
    if peds:
        parts.append('pedestrian')
    return ' - '.join(parts)


def classify_fatal_crash_type(row):
    """Classify a fatality from StrinkingVehicle + SecondStrikingVehicleObject.

    The two fields are treated as the (up to) two colliding parties; the victim
    is assumed to be one of them. Fixed Object is dropped (single-X). Unknown is
    kept as a visible second party in pairs, but all-unknown rows -> unclassified.
    """
    parties = []
    for v in (row.get('StrinkingVehicle'), row.get('SecondStrikingVehicleObject')):
        if pd.isna(v):
            continue
        if v not in FATAL_PARTY_RELABEL:
            # Surfaces schema drift (a new code the relabel map doesn't know)
            logger.warning(f"Unmapped party value in fatality data: {v!r}")
        mapped = FATAL_PARTY_RELABEL.get(v)   # Fixed Object / unmapped -> None
        if mapped is not None:
            parties.append(mapped)

    if not parties or all(p == 'unknown' for p in parties):
        return 'unclassified'

    if len(parties) == 1:
        p = parties[0]
        return 'pedestrian only' if p == 'pedestrian' else f'single {p}'

    parties.sort(key=lambda p: FATAL_PARTY_RANK.get(p, 99))
    return ' - '.join(parties)


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
    max_retries = 3

    logger.info(f"Fetching data from {url}")

    while True:
        # Retry loop for each paginated request
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()  # Raise exception for HTTP errors
                data = response.json()
                break  # Success, exit retry loop
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    wait_time = 30 * attempt  # 30s, 60s, 90s
                    logger.warning(
                        f"Attempt {attempt}/{max_retries} failed at offset {params['resultOffset']}: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"All {max_retries} attempts failed at offset {params['resultOffset']}. "
                        f"Last error: {e}. Returning {len(all_attributes)} features collected so far."
                    )
                    return pd.DataFrame(all_attributes)

        features = data.get("features", [])
        if not features:
            break  # Exit loop if no more features returned

        # Extract and append the feature attributes
        batch_attributes = [feature.get("attributes", {})
                            for feature in features]
        all_attributes.extend(batch_attributes)

        # Determine the maximum number of records returned per call, if available
        max_record_count = data.get("maxRecordCount", len(features))

        # If fewer features than the max count were returned, we've reached the end of the results
        if len(features) < max_record_count:
            break

        # Update the offset for the next call
        params["resultOffset"] += len(features)

        logger.info(
            f"Fetched {len(batch_attributes)} features (total: {len(all_attributes)})")

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

    # Build TYPE_OF_CRASH + involvement flags from the crash-level counts
    count_cols = ['TOTAL_VEHICLES', 'TOTAL_PEDESTRIANS', 'TOTAL_BICYCLES']
    df_cp_cd[count_cols + OTHER_COLS] = df_cp_cd[count_cols +
                                                 OTHER_COLS].fillna(0)

    # Carry crash-level impaired counts through as nullable integers
    df_cp_cd[IMPAIRED_COLS] = df_cp_cd[IMPAIRED_COLS].fillna(0).astype('Int64')

    df_cp_cd['TYPE_OF_CRASH'] = df_cp_cd.apply(classify_crash_type, axis=1)
    df_cp_cd['INVOLVES_MOTOR_VEHICLE'] = df_cp_cd['TOTAL_VEHICLES'] > 0
    df_cp_cd['INVOLVES_BICYCLE'] = df_cp_cd['TOTAL_BICYCLES'] > 0
    df_cp_cd['INVOLVES_PEDESTRIAN'] = df_cp_cd['TOTAL_PEDESTRIANS'] > 0
    df_cp_cd['INVOLVES_OTHER'] = df_cp_cd[OTHER_COLS].gt(0).any(axis=1)

    # Select and rename columns (raw TOTAL_* / *OTHER columns are intentionally
    # dropped here; only TYPE_OF_CRASH and the flags are carried forward).
    df_cp_cd = df_cp_cd[['OBJECTID_y', 'CRIMEID', 'CCN_y', 'REPORTDATE',
                         'PERSONID', 'PERSONTYPE', 'AGE', 'FATAL',
                         'MAJORINJURY', 'MINORINJURY', 'VEHICLEID',
                         'INVEHICLETYPE', 'TICKETISSUED', 'LICENSEPLATESTATE',
                         'IMPAIRED', 'SPEEDING', 'ROUTEID', 'STREETSEGID',
                         'ROADWAYSEGID', 'ADDRESS', 'LATITUDE', 'LONGITUDE',
                         'EVENTID', 'BLOCKKEY', 'SUBBLOCKKEY', 'CORRIDORID',
                         'PEDESTRIANSIMPAIRED', 'BICYCLISTSIMPAIRED',
                         'DRIVERSIMPAIRED',
                         'TYPE_OF_CRASH', 'INVOLVES_MOTOR_VEHICLE',
                         'INVOLVES_BICYCLE', 'INVOLVES_PEDESTRIAN',
                         'INVOLVES_OTHER']]

    df_cp_cd = df_cp_cd.rename(columns={
        'OBJECTID_y': 'OBJECTID',
        'CCN_y': 'CCN',
        'PERSONTYPE': 'MODE'
    })

    # Modify the AGE column: if AGE is NULL or AGE < 1 or AGE > 120, set it to 120
    df_cp_cd['AGE'] = df_cp_cd['AGE'].apply(
        lambda age: 120 if pd.isnull(age) or age < 1 or age > 120 else age)

    # Convert timestamps to datetime with proper timezone conversion
    df_cp_cd['REPORTDATE'] = (
        pd.to_datetime(df_cp_cd['REPORTDATE'], unit='ms')
        .dt.tz_localize('UTC')
        .dt.tz_convert('America/New_York')
    )

    # Remove future records beyond tomorrow at midnight
    tomorrow_midnight = (
        pd.Timestamp.now(tz="America/New_York")
        .normalize() + pd.Timedelta(days=1)
    )
    df_cp_cd = df_cp_cd[df_cp_cd['REPORTDATE'] <= tomorrow_midnight]

    # Now compute LAST_RECORD
    df_cp_cd['LAST_RECORD'] = df_cp_cd['REPORTDATE'].max()

    # ------------------ New Logging Block ------------------
    # Log how many days per year (from 2018 onward) had no crash records.
    # We first define "recorded" crash days by normalizing REPORTDATE to remove the time component.
    now = pd.Timestamp.now(tz="America/New_York").normalize()
    for year in range(2018, now.year + 1):
        # Define start and end dates for the year. For the current year, use today's date.
        start_date = pd.Timestamp(
            year=year, month=1, day=1, tz="America/New_York")
        if year == now.year:
            end_date = now
        else:
            end_date = pd.Timestamp(
                year=year, month=12, day=31, tz="America/New_York")

        # Generate all days in the given range
        all_days = pd.date_range(start_date, end_date, freq="D")
        total_days = len(all_days)

        # Get unique days with crash records for this year by normalizing the REPORTDATE timestamps.
        recorded_days = df_cp_cd[df_cp_cd['REPORTDATE'].dt.year ==
                                 year]['REPORTDATE'].dt.normalize().unique()
        # Convert to a set of date objects for easier comparison.
        recorded_days_set = {ts.date() for ts in recorded_days}
        days_with_records = len(recorded_days_set)
        days_without_records = total_days - days_with_records

        logger.info(
            f"Year {year}: {days_without_records} days without crash records.")
    # ---------------- End Logging Block ---------------------

    # Apply severity determination
    df_cp_cd['SEVERITY'] = df_cp_cd.apply(determine_severity, axis=1)

    # ----- Override SEVERITY values with data from TarasInjuries.parquet -----
    try:
        # Load the parquet file; it is assumed to have 'PERSONID' and 'SEVERITY'
        df_taras = pd.read_parquet("TarasInjuries.parquet")[
            ["PERSONID", "SEVERITY"]]
    except Exception as e:
        logger.error("Error loading TarasInjuries.parquet: " + str(e))
        raise e

    # Merge the TarasInjuries data onto df_cp_cd based on PERSONID.
    # For rows with a matching PERSONID, the SEVERITY value from the parquet file will be added in 'SEVERITY_tar'
    df_cp_cd = pd.merge(df_cp_cd, df_taras, on="PERSONID",
                        how="left", suffixes=("", "_tar"))

    # Override SEVERITY with the value from TarasInjuries where available.
    df_cp_cd['SEVERITY'] = df_cp_cd['SEVERITY_tar'].combine_first(
        df_cp_cd['SEVERITY'])
    # Remove the extra temporary column
    df_cp_cd.drop(columns=['SEVERITY_tar'], inplace=True)
    # --------------------------------------------------------------------------

    # Filter to only injuries
    df_cp_cd = df_cp_cd[df_cp_cd['SEVERITY'] != 'NOINJURY']

    # Clean up data
    df_cp_cd['SEVERITY'] = df_cp_cd['SEVERITY'].replace({
        'MAJORINJURY': 'Major',
        'MINORINJURY': 'Minor'
    })
    df_cp_cd['COUNT'] = 1

    # Create a cutoff for the last 30 days and fail if no recent records
    cutoff_date = pd.Timestamp.now(
        tz="America/New_York") - pd.Timedelta(days=30)
    recent_records = df_cp_cd[df_cp_cd['REPORTDATE'] >= cutoff_date]
    if recent_records.empty:
        logger.error("No crash records in the last 30 days.")
        # Raising an exception will cause the GitHub Action to fail.
        raise Exception(
            "No crash records in the last 30 days. Failing GitHub Action.")

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
        gis = GIS("https://dcgis.maps.arcgis.com",
                  client_id=client_id, client_secret=client_secret)
        feature_layer_item = gis.content.get(feature_layer_id)
        # Access the first layer in the item
        feature_layer = feature_layer_item.layers[0]

        # Query all features
        features = feature_layer.query(where="1=1", out_fields="*")
        df_f = features.sdf

        # Sort and convert to GeoDataFrame
        df_fs = df_f.sort_values(by='datetime', ascending=False)
        df_fs['death_case_id'] = df_fs.apply(
            lambda row: f"D{int(row.death_case):02d}-{int(row.death_case_number):02d}"
            if pd.notnull(row.death_case) and pd.notnull(row.death_case_number)
            else None,
            axis=1
        )
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
            'sco': 'Scooterist*',
            'unknown': 'Other'
        })

        gdf_f['SEVERITY'] = 'Fatal'

        # Rename columns for consistency
        gdf_f = gdf_f.rename(columns={
            'objectid': 'OBJECTID',
            'death_case_id': 'DeathCaseID',
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
            'actions_under_consideration': 'ActionsUnderConsideration',
            'suspected_impaired': 'SuspectedImpaired',
            'suspected_speeding': 'SuspectedSpeeding',
            'hit_and_run': 'HitAndRun'
        })

        # Select columns
        gdf_f = gdf_f[['OBJECTID', 'DeathCaseID', 'CCN', 'MODE', 'SEVERITY', 'REPORTDATE', 'ADDRESS',
                       'AGE', 'StrinkingVehicle', 'SecondStrikingVehicleObject', 'SiteVisitStatus',
                       'FactorsDiscussedAtSiteVisit', 'ActionsPlannedAndCompleted', 'ActionsUnderConsideration',
                       'SuspectedImpaired', 'SuspectedSpeeding', 'HitAndRun', 'LATITUDE', 'LONGITUDE']]

        # Define the mapping dictionary
        mapping_ssvo = {
            'pedestrian_ped_': 'Pedestrian',
            'motor_vehicle_mvt_': 'Motor Vehicle',
            'fixed_object_fo_': 'Fixed Object',
            'motorcyclist_mo_': 'Motorcycle',
            'bicyclist_bic_': 'Bicycle',
            'unknown': 'Unknown',
            'sco_pmd': 'Scooter / Personal Mobility Device'
        }

        # Replace the values in the 'SecondStrikingVehicleObject' column
        gdf_f['SecondStrikingVehicleObject'] = gdf_f['SecondStrikingVehicleObject'].replace(
            mapping_ssvo)

        # Define the mapping dictionary
        mapping_sv = {
            'pedestrian_ped': 'Pedestrian',
            'motor_vehicle_mvt': 'Motor Vehicle',
            'motorcyclist_mo_': 'Motorcycle',  # This one remains the same
            'bicyclist_bic': 'Bicycle',
            'unknown': 'Unknown',
            'sco_pmd': 'Scooter / Personal Mobility Device'
        }

        # Replace the values in the 'StrinkingVehicle' column
        gdf_f['StrinkingVehicle'] = gdf_f['StrinkingVehicle'].replace(
            mapping_sv)

        # Build TYPE_OF_CRASH + involvement flags from the two striking-party fields
        gdf_f['TYPE_OF_CRASH'] = gdf_f.apply(classify_fatal_crash_type, axis=1)
        gdf_f['INVOLVES_MOTOR_VEHICLE'] = gdf_f['TYPE_OF_CRASH'].str.contains(
            'motor vehicle', na=False)
        gdf_f['INVOLVES_BICYCLE'] = gdf_f['TYPE_OF_CRASH'].str.contains(
            'bicycle', na=False)
        gdf_f['INVOLVES_PEDESTRIAN'] = gdf_f['TYPE_OF_CRASH'].str.contains(
            'pedestrian', na=False)
        gdf_f['INVOLVES_OTHER'] = gdf_f['TYPE_OF_CRASH'].str.contains(
            r'motorcycle\*|standing scooter\*', na=False, regex=True)

        gdf_f['AGE'] = gdf_f['AGE'].astype(float)
        # Modify the AGE column: if AGE is NULL or AGE < 1 or AGE > 120, set it to 120
        gdf_f['AGE'] = gdf_f['AGE'].apply(
            lambda age: 120 if pd.isnull(age) or age < 1 or age > 120 else age)
        gdf_f['COUNT'] = 1

        # Convert timestamps to datetime with proper timezone conversion
        gdf_f['REPORTDATE'] = (
            pd.to_datetime(gdf_f['REPORTDATE'], unit='ms')
            .dt.tz_localize('UTC')
            .dt.tz_convert('America/New_York')
        )

        # Remove future records beyond tomorrow at midnight
        tomorrow_midnight = (
            pd.Timestamp.now(tz="America/New_York")
            .normalize() + pd.Timedelta(days=1)
        )
        gdf_f = gdf_f[gdf_f['REPORTDATE'] <= tomorrow_midnight]

        # Compute LAST_RECORD
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
    injury_data['REPORTDATE'] = injury_data['REPORTDATE'].dt.tz_localize(
        None).astype('datetime64[ns]')
    injury_data['LAST_RECORD'] = injury_data['LAST_RECORD'].dt.tz_localize(
        None).astype('datetime64[ns]')
    fatality_data['REPORTDATE'] = fatality_data['REPORTDATE'].dt.tz_localize(
        None).astype('datetime64[ns]')
    fatality_data['LAST_RECORD'] = fatality_data['LAST_RECORD'].dt.tz_localize(
        None).astype('datetime64[ns]')

    # Merge the dataframes.
    # TYPE_OF_CRASH and the INVOLVES_* flags are added to the join keys so they
    # remain single columns (injury and fatality rows never match on the full
    # key set, so each source simply carries its own values; without this they
    # would be split into _x / _y).
    combined_df = pd.merge(
        fatality_data, injury_data,
        how='outer',
        on=['OBJECTID', 'CCN', 'MODE', 'SEVERITY', 'REPORTDATE',
            'AGE', 'LATITUDE', 'LONGITUDE', 'COUNT', 'ADDRESS', 'LAST_RECORD',
            'TYPE_OF_CRASH', 'INVOLVES_MOTOR_VEHICLE', 'INVOLVES_BICYCLE',
            'INVOLVES_PEDESTRIAN', 'INVOLVES_OTHER']
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
                    logger.info(
                        f"Previous LAST_RECORD timestamp: {prev_timestamp}")

                    # Compare timestamps
                    if prev_timestamp == last_record_timestamp:
                        logger.error(
                            "No new data since last run - LAST_RECORD timestamp is the same")
                        raise Exception(
                            "LAST_RECORD timestamp unchanged since previous run. Failing GitHub Action.")

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
        geometry=gpd.points_from_xy(
            combined_df.LONGITUDE, combined_df.LATITUDE, crs=4326)
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
        smd_path = 'Spatial-Files/smd_2023.geojson'
        logger.info(f"Reading SMD polygons from {smd_path}")
        smd = gpd.read_file(smd_path)
        smd = smd.to_crs(4326)
        smd = smd[['SMD', 'geometry']]

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

        # Join spatially HIN polygons to crashes
        hin_path = 'Spatial-Files/hin-polygon-clean.geojson'
        logger.info(f"Reading HIN polygons from {hin_path}")
        hin = gpd.read_file(hin_path)
        hin = hin.to_crs(4326)

        # Select only the required columns
        hin = hin[['ROUTENAME', 'TIER_1', 'TIER_2',
                   'TIER_3', 'GIS_ID', 'geometry']]

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
        hin['HIN_TIER'] = hin.apply(determine_hin_tier, axis=1)

        # Select final columns for the join (drop individual TIER columns)
        hin = hin[['ROUTENAME', 'HIN_TIER', 'GIS_ID', 'geometry']]

        logger.info("Performing spatial join with HIN boundaries")
        # Long-form join: a crash on overlapping corridors yields multiple rows
        joined = gpd.sjoin(gdf_hex_anc_smd, hin, how='left')

        # Keep up to max_matches corridors per crash, expanded into A/B/C columns.
        # This vectorized rank-and-pivot replaces the per-group apply (much
        # faster) and produces the identical ROUTENAME_A/B/C, HIN_TIER_A/B/C,
        # GIS_ID_A/B/C layout, so crashes.parquet keeps the same structure.
        max_matches = 3

        m = (joined.dropna(subset=['ROUTENAME', 'HIN_TIER', 'GIS_ID'])
                   .drop_duplicates(['OBJECTID', 'ROUTENAME', 'HIN_TIER', 'GIS_ID'])
             # deterministic A/B/C order: highest tier first, then name
                   .sort_values(['OBJECTID', 'HIN_TIER', 'ROUTENAME']))
        m['rk'] = m.groupby('OBJECTID').cumcount()
        m = m[m['rk'] < max_matches]

        hin_wide = m.pivot(index='OBJECTID', columns='rk',
                           values=['ROUTENAME', 'HIN_TIER', 'GIS_ID'])
        hin_wide.columns = [f'{field}_{chr(65 + rk)}'
                            for field, rk in hin_wide.columns]
        hin_order = [f'{field}_{chr(65 + i)}' for i in range(max_matches)
                     for field in ['ROUTENAME', 'HIN_TIER', 'GIS_ID']]
        hin_wide = hin_wide.reindex(columns=hin_order).reset_index()

        # Merge HIN columns back onto the one-row-per-crash frame
        gdf_hex_anc_smd_hin = gdf_hex_anc_smd.merge(
            hin_wide, on='OBJECTID', how='left')

        # -------- Nearest intersection within 100 ft --------
        # Assign each crash to its single closest intersection point (the center
        # of the 100 ft buffer). Point-to-point distance in a feet CRS (EPSG
        # 2248, NAD83 Maryland ftUS), so 100 means 100 ft per DDOT documentation.
        # One intersection per crash, no double counting, no overlap columns.
        # The parquet comes from the monthly dedup refresh; geometry is built
        # from LATITUDE/LONGITUDE here, the same way crashes are built.
        intx_path = 'Spatial-Files/Intersection_Points_unique.parquet'
        logger.info(f"Reading unique intersections from {intx_path}")
        intx_df = pd.read_parquet(intx_path)
        intx = gpd.GeoDataFrame(
            intx_df,
            geometry=gpd.points_from_xy(
                intx_df.LONGITUDE, intx_df.LATITUDE, crs=4326)
        ).to_crs(2248)[['INTERSECTIONKEY', 'canonical_name', 'geometry']]

        logger.info("Assigning nearest intersection within 100 ft")
        near = gpd.sjoin_nearest(
            gdf_hex_anc_smd[['OBJECTID', 'geometry']].to_crs(2248),
            intx, how='left', max_distance=100, distance_col='DIST_TO_INTX_FT')
        # exact-distance ties can yield >1 row per crash; keep the single closest
        near = (near.sort_values('DIST_TO_INTX_FT')
                    .drop_duplicates('OBJECTID')[
                        ['OBJECTID', 'INTERSECTIONKEY',
                         'canonical_name', 'DIST_TO_INTX_FT']]
                    .rename(columns={'canonical_name': 'INTERSECTION_NAME'}))

        gdf_hex_anc_smd_hin = gdf_hex_anc_smd_hin.merge(
            near, on='OBJECTID', how='left')
        # --------------------------------------------------

        # Rename columns for consistency
        gdf_hex_anc_smd_hin = gdf_hex_anc_smd_hin.rename(columns={
            'grid_id': 'GRID_ID',
            'WARD_ID': 'WARD'
        })

        # Drop the geometry column to create a plain DataFrame result
        gdf_hex_anc_smd_hin = gdf_hex_anc_smd_hin.drop(columns=['geometry'])

        # Convert back to DataFrame
        crash_hex = pd.DataFrame(gdf_hex_anc_smd_hin)

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
    crash_data['LAST_UPDATE'] = crash_data['LAST_UPDATE'].dt.tz_localize(
        None).astype('datetime64[ns]')

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
        # Re-raise so a failed parquet write fails the job instead of exiting 0
        # and letting the upload step run against a missing/partial file.
        raise


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
        # Re-raise so the "Run Backend Script" step exits non-zero. A failed step
        # skips the later upload step, so a "no new data" / failed run can never
        # delete the good crashes.parquet from the release.
        raise


if __name__ == "__main__":
    main()
