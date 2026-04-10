#!/usr/bin/env python3
"""
start_api.py
启动 telegram-bot-api 本地服务器（Docker 容器方式）。
直接运行：  python3 start_api.py
也可以由 main.py 导入调用：  from start_api import ensure_api_running
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env
_project_root = Path(__file__).parent
load_dotenv(_project_root / ".env", override=True)

API_ID   = os.getenv("TELEGRAM_API_ID", "").strip('"').strip("'")
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip('"').strip("'")
API_URL  = os.getenv("API_URL", "").strip('"').strip("'")
DATA_DIR = os.getenv("DATA_DIR", str(_project_root / "data")).strip('"').strip("'")

CONTAINER_NAME = "telegram-bot-api"
IMAGE          = "aiogram/telegram-bot-api:latest"
PORT           = 8081


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _container_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        capture_output=True, text=True
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_api_running() -> bool:
    """
    确保 telegram-bot-api 容器正在运行。
    如果端口已监听或容器已运行，直接返回 True。
    否则启动新容器。
    """
    # 1. 端口已在监听 -> 无论什么方式运行的，直接放行
    if _port_open(PORT):
        print(f"[start_api] ✅ 端口 {PORT} 已在监听，跳过启动。")
        return True

    # 2. 检查凭证
    if not API_ID or not API_HASH:
        print(
            "[start_api] ❌ 缺少 TELEGRAM_API_ID 或 TELEGRAM_API_HASH！\n"
            "            请在 .env 中设置：\n"
            "              TELEGRAM_API_ID=你的api_id\n"
            "              TELEGRAM_API_HASH=你的api_hash\n"
            "            从 https://my.telegram.org 获取"
        )
        return False

    # 3. 容器已存在但未运行 -> 删除并重建
    existing = subprocess.run(
        ["docker", "ps", "-a", "-q", "-f", f"name={CONTAINER_NAME}"],
        capture_output=True, text=True
    )
    if existing.stdout.strip():
        print(f"[start_api] 🔄 删除已停止的旧容器 {CONTAINER_NAME}...")
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], check=True)

    # 4. 启动新容器
    tg_data_dir = Path(DATA_DIR) / "telegram-api"
    tg_data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "always",
        "-p", f"{PORT}:{PORT}",
        "-v", f"{tg_data_dir.resolve()}:/var/lib/telegram-bot-api",
        "-e", f"TELEGRAM_API_ID={API_ID}",
        "-e", f"TELEGRAM_API_HASH={API_HASH}",
        IMAGE,
    ]

    print(f"[start_api] 🚀 正在启动 {IMAGE} ...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[start_api] ❌ docker run 失败：{result.stderr.strip()}")
        return False

    print(f"[start_api] 等待服务就绪（最多 30s）...", flush=True)
    for i in range(30):
        time.sleep(1)
        if _port_open(PORT):
            print(f"[start_api] ✅ telegram-bot-api 启动成功（{i + 1}s）")
            return True
        print(f"  等待中... {i + 1}s", end="\r", flush=True)

    print(f"\n[start_api] ❌ 启动超时（30s），请检查日志： docker logs {CONTAINER_NAME}")
    return False


if __name__ == "__main__":
    ok = ensure_api_running()
    sys.exit(0 if ok else 1)
