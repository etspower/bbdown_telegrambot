FROM python:3.10-slim

# 1. Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# 2. Add BBDown binary (independent linux-x64 compressed package)
# 3. Unzip and grant executable permissions
# 4. Move to PATH
RUN wget https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_linux-x64.zip -O bbdown.zip \
    && unzip bbdown.zip \
    && chmod +x BBDown \
    && mv BBDown /usr/local/bin/BBDown \
    && rm bbdown.zip

# Set up app directory
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot/ ./bot/

# Command to run the bot
CMD ["python3", "-m", "bot.main"]
