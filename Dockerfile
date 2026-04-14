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
    && mv /tmp/bbdown/BBDown /usr/local/bin/BBDown.real \
    && chmod +x /usr/local/bin/BBDown.real \
    # Wrapper: 强制 HOME 为 /app/data/.bbdown_home，但不改变 cwd（保持 subprocess 传入的 cwd 有效），
    # 这样 BBDown login 时 qrcode.png 写到 subprocess 的 cwd 里，cmd_login 可以直接找到
    && printf '#!/bin/sh\nmkdir -p /app/data/.bbdown_home\nexport HOME=/app/data/.bbdown_home\nexec /usr/local/bin/BBDown.real "$@"\n' > /usr/local/bin/BBDown \
    && chmod +x /usr/local/bin/BBDown \
    && rm -rf /tmp/bbdown.zip /tmp/bbdown

# Step 2: Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 3: Bot source
COPY bot/ ./bot/

# Run as non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python3", "-m", "bot.main"]
