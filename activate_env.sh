#!/bin/bash
# Activation script for DC Vision Zero Dashboard Backend

echo "🚀 Activating DC Vision Zero Dashboard Backend environment..."

# Activate virtual environment
source /workspaces/venv/bin/activate

# Set environment variables
export PYTHONPATH="/workspaces/Crash-Injury-Dashboard-Backend:$PYTHONPATH"

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.template .env
    echo "⚠️  Please edit .env file with your actual credentials!"
fi

# Create necessary directories
mkdir -p Spatial-Files
mkdir -p email

echo "✅ Environment activated!"
echo "📍 Current Python: $(which python)"
echo "📍 Current pip: $(which pip)"

# Test basic imports
echo "🧪 Testing basic imports..."
python -c "import sys; print(f'Python version: {sys.version}')" || echo "❌ Python test failed"
python -c "import pandas; print('✅ Pandas works!')" || echo "❌ Pandas not available"
python -c "import requests; print('✅ Requests works!')" || echo "❌ Requests not available"
python -c "from dotenv import load_dotenv; print('✅ dotenv works!')" || echo "❌ dotenv not available"

echo ""
echo "🔗 Useful commands:"
echo "- Test basic functionality: python -c 'import pandas, requests; print(\"Basic packages work!\")'"
echo "- Start Jupyter Lab: jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root"
echo "- Edit .env file: code .env"
echo ""
echo "📋 Next steps:"
echo "1. Edit the .env file with your ArcGIS credentials"
echo "2. Add your spatial files to the Spatial-Files/ directory"
echo "3. Test your data pipeline with the available packages"