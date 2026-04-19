#!/bin/bash
# Setup script for Alpaca Auto-Trading Bot

echo "Setting up Alpaca Auto-Trading Bot..."
echo "=================================="

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.9 or higher."
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

# Create .env from template if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp env_template .env
    echo "Please edit .env with your API keys"
fi

echo ""
echo "=================================="
echo "Setup complete!"
echo ""
echo "To run the trading bot:"
echo "  source venv/bin/activate"
echo "  python3 alpaca_trading_bot.py"
echo ""
echo "To configure live trading, edit config.py or .env"
echo "=================================="