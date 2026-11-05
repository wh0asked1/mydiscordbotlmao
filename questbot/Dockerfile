FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Hardcode the install right into the build
RUN pip install --no-cache-dir \
    "discord.py>=2.4.0" \
    "aiohttp>=3.10.0" \
    "aiosqlite>=0.20.0" \
    "python-dotenv>=1.0.1"

# Copy the rest of the flat files
COPY . .

CMD ["python", "bot.py"]
