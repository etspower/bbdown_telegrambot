# BBDown Telegram Bot - Dockerfile
# Multi-stage: build deps, then minimal runtime image
FROM python:3.12-slim

SHELL ["/bin/bash", "-c"]

# Install system deps + ffmpeg + BBDown in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        wget \
        unzip \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    # BBDown (latest release)
    && wget -q https://github.com/nilaoda/BBDown/releases/latest/download/BBDown_1.6.4_20241207_linux-x64.zip -O /tmp/bbdown.zip \
    && unzip -o /tmp/bbdown.zip -d /tmp/bbdown \
    && mv /tmp/bbdown/BBDown /usr/local/bin/BBDown \
    && chmod +x /usr/local/bin/BBDown \
    && rm -rf /tmp/bbdown.zip /tmp/bbdown \
    && BBDown --version

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot/ ./bot/

# DATA_DIR defaults to /app/data automatically (config.py resolves from project root)
# No ENV DATA_DIR needed — relative paths in config.py now resolve correctly.

EXPOSE 8081

# Run as non-root user for security
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python3", "-m", "bot.main"]
