#!/bin/bash
set -e


echo "Starting Uvicorn..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 80
