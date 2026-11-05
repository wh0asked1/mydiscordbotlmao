#!/bin/bash
set -e
echo "Installing dependencies..."
python -m pip install --no-cache-dir -r requirements.txt
echo "Starting bot..."
exec python bot.py
