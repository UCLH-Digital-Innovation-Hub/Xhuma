#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Define the path to your Python script
PYTHON_SCRIPT="combine_ssl.py"

# Function to check for Python or Python3 and set the appropriate command
find_python() {
  if command -v python3 &> /dev/null
  then
    PYTHON_CMD="python3"
  elif command -v python &> /dev/null
  then
    PYTHON_CMD="python"
  else
    echo "Python is not installed. Please install Python before proceeding."
    exit 1
  fi
}

# Check if Python or Python3 is installed
find_python

# Run the Python script using the determined Python command
echo "Running Python script with $PYTHON_CMD..."
$PYTHON_CMD $PYTHON_SCRIPT

# Check if the Python script ran successfully
if [ $? -eq 0 ]; then
  echo "Python script completed successfully."

  # Navigate to the directory containing your docker-compose.yml
  echo "Starting Docker Compose..."

  # Start Docker services using docker-compose
  docker compose up -d
else
  echo "Python script failed."
  exit 1
fi

# Optional: Monitor Docker logs (uncomment if needed)
# docker-compose logs -f
