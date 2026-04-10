# BBDown Telegram Bot
FROM python:3.12-slim

WORKDIR /app

# Step 1: Install system dependencies + BBDown
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        wget \
        unzip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && wget -q https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_linux-x64.zip \
        -O /tmp/bbdown.zip \
    && unzip -o /tmp/bbdown.zip -d /tmp/bbdown \
    && mv /tmp/bbdown/BBDown /usr/local/bin/BBDown \
    && chmod +x /usr/local/bin/BBDown \
    && rm -rf /tmp/bbdown.zip /tmp/bbdown \
    && BBDown --version

# Step 2: Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 3: Bot source
COPY bot/ ./bot/

# Run as non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python3", "-m", "bot.main"]
