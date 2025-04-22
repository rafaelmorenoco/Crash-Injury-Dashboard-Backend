# DC Vision Zero Traffic Fatalities and Injury Crashes Dashboard Backend

This repository contains the backend data processing pipeline for the DC Vision Zero Traffic Fatalities and Injury Crashes Dashboard. It automatically fetches, processes, and combines crash data from multiple sources to create a comprehensive dataset for the dashboard frontend.

## Overview

This system automatically:
1. Fetches crash data with injuries from DC Open Data REST APIs
2. Retrieves fatality data from ArcGIS feature services 
3. Combines and processes the data with spatial joins to add geographic context
4. Outputs a clean, analysis-ready Parquet file
5. Automatically updates the frontend repository with the latest data

The entire pipeline is automated with GitHub Actions to run daily, ensuring the dashboard displays the most current data available.

## Data Sources

The pipeline integrates data from multiple sources:

- **Crash Point & Details**: From DC Open Data (Public Safety dataset)
  - Endpoint: https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/24/query
  - Endpoint: https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/25/query

- **Fatality Data**: From ArcGIS feature layer (requires authentication)

- **Spatial Context**: Added through spatial joins with:
  - Hexagon grid for standardized spatial analysis
  - Advisory Neighborhood Commission (ANC) boundaries
  - Single Member District (SMD) boundaries

## Repository Structure

- `Crash-Injury-Dashboard-Backend.py` - Main processing script
- `Spatial-Files/` - Directory containing geospatial boundary files:
  - `crash-hexgrid.geojson` - Hexagon grid for spatial analysis
  - `Advisory_Neighborhood_Commissions_from_2023.geojson` - ANC boundaries
  - `Single_Member_District_from_2023.geojson` - SMD boundaries
- `.github/workflows/run-script.yml` - GitHub Actions workflow definition
- `README.md` - This documentation file

## Setup and Configuration

### Prerequisites

- Python 3.10.12
- Dependencies listed in the workflow file:
  - numpy
  - gssapi
  - arcgis
  - pandas
  - pyarrow
  - requests
  - fiona
  - geopandas

### Authentication

The script requires authentication for ArcGIS services. Configure the following secrets in your GitHub repository:

- `ARCGIS_CLIENT_ID`: Your ArcGIS client ID
- `ARCGIS_CLIENT_SECRET`: Your ArcGIS client secret
- `ARCGIS_FEATURE_LAYER_ID`: The feature layer ID for fatality data
- `FRONTEND_REPO_PAT`: Personal Access Token with write access to the frontend repository

### GitHub Actions Workflow

The workflow is configured to:
- Run daily at 7:30 AM EST (11:30 UTC)
- Allow manual triggering when needed
- Install all required dependencies
- Run the data processing script
- Copy the output `crashes.parquet` file to the frontend repository
- Commit and push the updated data file to the frontend repository

## Running Locally

To run the script locally:

1. Clone the repository
2. Install the required dependencies:
   ```bash
   pip install numpy==1.24.2 gssapi==1.9.0 arcgis==2.2.0 pandas==2.0.3 pyarrow==13.0.0 requests==2.26.0 fiona==1.9.4 geopandas==0.13.2
   ```
3. Set up environment variables for authentication:
   ```bash
   export ARCGIS_CLIENT_ID="your_client_id"
   export ARCGIS_CLIENT_SECRET="your_client_secret"
   export ARCGIS_FEATURE_LAYER_ID="your_feature_layer_id"
   ```
4. Run the script:
   ```bash
   python Crash-Injury-Dashboard-Backend.py
   ```

## Output Data

The script produces a `crashes.parquet` file containing all crash data with:
- Basic crash information (time, location, severity)
- Geographic context (ward, ANC, SMD, hex grid ID)
- Additional attributes for analysis

This file is automatically published to the frontend repository for use in the dashboard.

## Troubleshooting

Check the GitHub Actions logs for detailed information about the script execution and any errors that may occur. Common issues include:

- **API access errors**: Verify your ArcGIS credentials are correctly set up in GitHub Secrets
- **Data format changes**: If source data formats change, the script may need updating
- **Missing spatial files**: Ensure all required GeoJSON files are present in the Spatial-Files directory

## Contributing

Contributions to improve the data pipeline are welcome. Please submit a pull request with proposed changes.
