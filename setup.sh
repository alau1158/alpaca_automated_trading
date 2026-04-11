#!/bin/bash
# Setup script for Swing Trading Analyzer

echo "Setting up Swing Trading Stock Analyzer..."
echo "=========================================="

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

echo "Python version:"
python3 --version

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing required packages..."
pip install -r requirements.txt

echo ""
echo "=========================================="
echo "Setup complete!"
echo "To run the analyzer:"
echo "  source venv/bin/activate"
echo "  python swing_trading_analyzer.py"
echo "=========================================="