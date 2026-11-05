#!/bin/bash
echo "Installing dependencies at runtime..."
pip install --no-cache-dir discord.py aiohttp aiosqlite python-dotenv
echo "Starting bot..."
python bot.py
