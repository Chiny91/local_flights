#!/bin/zsh

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Navigate to the script directory
cd "$SCRIPT_DIR"

# Check if python3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 could not be found."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Install requirements if requirements.txt exists
# We use explicit path to venv pip to ensure we install into the virtual environment
if [ -f "requirements.txt" ]; then
    # Quietly install requirements to avoid cluttering startup unless there's an error
    echo "Checking/Installing requirements..."
    ./venv/bin/pip install -r requirements.txt
fi

# Run the python script
# Using "$@" to pass any arguments (like --interval) to the script
# Use explicit path to venv python
./venv/bin/python3 flight_tracker.py "$@"
