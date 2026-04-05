FROM python:3.11-slim

# 1. Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. Download and install BBDown (from zip archive)
RUN wget -q https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_linux-x64.zip -O /tmp/bbdown.zip \
    && unzip /tmp/bbdown.zip -d /tmp/bbdown \
    && mv /tmp/bbdown/BBDown /usr/local/bin/BBDown \
    && chmod +x /usr/local/bin/BBDown \
    && rm -rf /tmp/bbdown.zip /tmp/bbdown

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
