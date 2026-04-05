FROM python:3.11-slim

# 1. Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. Download and install BBDown
RUN wget -q https://github.com/nilaoda/BBDown/releases/latest/download/BBDown -O /usr/local/bin/BBDown \
    && chmod +x /usr/local/bin/BBDown

# Verify BBDown installation
RUN BBDown --version

# Set up app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot/ ./bot/

# Create data directory for persistent storage
RUN mkdir -p /app/data

# Environment defaults
ENV BBDOWN_PATH=/usr/local/bin/BBDown
ENV DATA_DIR=/app/data

# Command to run the bot
CMD ["python3", "-m", "bot.main"]
