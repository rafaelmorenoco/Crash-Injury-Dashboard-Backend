#!/bin/bash

echo "🚀 Setting up DC Vision Zero Dashboard Backend in Codespaces..."

# Update system packages
echo "📦 Updating system packages..."
sudo apt-get update

# Install system dependencies for geospatial libraries
echo "🗺️ Installing geospatial system dependencies..."
sudo apt-get install -y \
    libgdal-dev \
    gdal-bin \
    libproj-dev \
    proj-data \
    proj-bin \
    libgeos-dev \
    libspatialindex-dev \
    libkrb5-dev \
    build-essential

# Set GDAL environment variables
export CPLUS_INCLUDE_PATH=/usr/include/gdal
export C_INCLUDE_PATH=/usr/include/gdal

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file from template if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.template .env
    echo "⚠️  Please edit .env file with your actual credentials!"
fi

# Create necessary directories
echo "📁 Creating necessary directories..."
mkdir -p Spatial-Files
mkdir -p email

# Set up Git configuration
echo "🔧 Setting up Git configuration..."
git config --global user.name "$(git config user.name)"
git config --global user.email "$(git config user.email)"

# Install Jupyter extensions
echo "📓 Installing Jupyter extensions..."
jupyter labextension install @jupyter-widgets/jupyterlab-manager

echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Edit the .env file with your ArcGIS credentials"
echo "2. Add your spatial files to the Spatial-Files/ directory"
echo "3. Run 'python Crash-Injury-Dashboard-Backend.py' to test the pipeline"
echo "4. Use 'jupyter lab --ip=0.0.0.0 --port=8888 --no-browser' to start Jupyter"
echo ""
echo "🔗 Useful commands:"
echo "- Test the main script: python Crash-Injury-Dashboard-Backend.py"
echo "- Start Jupyter Lab: jupyter lab --ip=0.0.0.0 --port=8888 --no-browser"
echo "- Format code: black ."
echo "- Lint code: flake8 ."
