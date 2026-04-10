#!/usr/bin/env python3
"""
start_api.py
启动 telegram-bot-api 本地服务器（Docker 容器方式）。
同时提供 BBDown 自动安装功能。
直接运行：  python3 start_api.py
也可由 main.py 导入调用：  from start_api import ensure_api_running, ensure_bbdown_installed
"""

import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from dotenv import load_dotenv

_project_root = Path(__file__).parent
load_dotenv(_project_root / ".env", override=True)

API_ID   = os.getenv("TELEGRAM_API_ID", "").strip('"').strip("'")
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip('"').strip("'")
API_URL  = os.getenv("API_URL", "").strip('"').strip("'")
DATA_DIR = os.getenv("DATA_DIR", str(_project_root / "data")).strip('"').strip("'")

CONTAINER_NAME = "telegram-bot-api"
IMAGE          = "aiogram/telegram-bot-api:latest"
PORT           = 8081

# BBDown 安装路径（优先级）
BBDOWN_INSTALL_PATH = Path("/usr/local/bin/BBDown")
BBDOWN_FALLBACK_PATH = _project_root / "tools" / "BBDown"

# BBDown GitHub Release 下载地址
_BBDOWN_RELEASE_BASE = "https://github.com/nilaoda/BBDown/releases/latest/download"


def _detect_bbdown_asset() -> str:
    """根据当前操作系统和架构选择对应的 BBDown 压缩包名。"""
    machine = platform.machine().lower()
    system = platform.system().lower()

    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "BBDown_linux-x64.zip"
        elif machine in ("aarch64", "arm64"):
            return "BBDown_linux-arm64.zip"
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "BBDown_osx-arm64.zip"
        return "BBDown_osx-x64.zip"
    elif system == "windows":
        return "BBDown_win-x64.zip"

    # 默认 linux-x64
    return "BBDown_linux-x64.zip"


def find_bbdown() -> str | None:
    """
    按优先级查找 BBDown 可执行文件：
    1. /usr/local/bin/BBDown
    2. 项目目录/tools/BBDown
    3. PATH 中的 BBDown
    """
    candidates = [
        BBDOWN_INSTALL_PATH,
        BBDOWN_FALLBACK_PATH,
    ]
    for p in candidates:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)

    # 在 PATH 中查找
    found = shutil.which("BBDown") or shutil.which("bbdown")
    return found


def ensure_bbdown_installed() -> str | None:
    """
    确保 BBDown 已安装。
    如果未找到，自动从 GitHub 下载并安装。
    返回 BBDown 可执行文件的绝对路径，失败返回 None。
    """
    existing = find_bbdown()
    if existing:
        print(f"[start_api] ✅ BBDown 已存在：{existing}")
        return existing

    asset = _detect_bbdown_asset()
    url = f"{_BBDOWN_RELEASE_BASE}/{asset}"
    tmp_zip = Path("/tmp/bbdown_dl.zip")
    tmp_dir = Path("/tmp/bbdown_extract")

    print(f"[start_api] 📥 BBDown 未找到，正在下载 {asset} ...", flush=True)
    try:
        urllib.request.urlretrieve(url, tmp_zip)
    except Exception as e:
        print(f"[start_api] ❌ 下载失败：{e}")
        return None

    tmp_dir.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(tmp_dir)
    except Exception as e:
        print(f"[start_api] ❌ 解压失败：{e}")
        return None

    extracted = tmp_dir / "BBDown"
    if not extracted.exists():
        # 查找压缩包内第一个文件
        files = list(tmp_dir.iterdir())
        if not files:
            print("[start_api] ❌ 解压内容为空")
            return None
        extracted = files[0]

    # 尝试安装到 /usr/local/bin，失败则安装到 tools/
    install_path = BBDOWN_INSTALL_PATH
    try:
        shutil.copy2(extracted, install_path)
        install_path.chmod(install_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"[start_api] ✅ BBDown 安装到 {install_path}")
    except PermissionError:
        # 没有写入 /usr/local/bin 的权限，改用 tools/
        install_path = BBDOWN_FALLBACK_PATH
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, install_path)
        install_path.chmod(install_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"[start_api] ✅ BBDown 安装到 {install_path}（无 /usr/local/bin 权限）")

    # 清理临时文件
    tmp_zip.unlink(missing_ok=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return str(install_path)


# ── telegram-bot-api 部分 ──────────────────────────────────────────────

def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def ensure_api_running() -> bool:
    """
    确保 telegram-bot-api 容器正在运行。
    如果端口已监听或容器已运行，直接返回 True。
    """
    if _port_open(PORT):
        print(f"[start_api] ✅ 端口 {PORT} 已在监听，跳过启动。")
        return True

    if not API_ID or not API_HASH:
        print(
            "[start_api] ❌ 缺少 TELEGRAM_API_ID 或 TELEGRAM_API_HASH！\n"
            "            请在 .env 中设置：\n"
            "              TELEGRAM_API_ID=你的api_id\n"
            "              TELEGRAM_API_HASH=你的api_hash\n"
            "            从 https://my.telegram.org 获取"
        )
        return False

    existing = subprocess.run(
        ["docker", "ps", "-a", "-q", "-f", f"name={CONTAINER_NAME}"],
        capture_output=True, text=True
    )
    if existing.stdout.strip():
        print(f"[start_api] 🔄 删除已停止的旧容器 {CONTAINER_NAME}...")
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], check=True)

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

    print("[start_api] 等待服务就绪（最多 30s）...", flush=True)
    for i in range(30):
        time.sleep(1)
        if _port_open(PORT):
            print(f"[start_api] ✅ telegram-bot-api 启动成功（{i + 1}s）")
            return True
        print(f"  等待中... {i + 1}s", end="\r", flush=True)

    print(f"\n[start_api] ❌ 启动超时（30s），请检查日志： docker logs {CONTAINER_NAME}")
    return False


if __name__ == "__main__":
    # 单独运行时：同时确保 BBDown 和 telegram-bot-api 就绪
    bbdown = ensure_bbdown_installed()
    if not bbdown:
        print("❌ BBDown 安装失败")
        sys.exit(1)

    ok = ensure_api_running()
    sys.exit(0 if ok else 1)
