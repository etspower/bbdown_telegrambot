#!/usr/bin/env python3
"""
start_api.py
启动 telegram-bot-api 本地服务器（Docker 容器方式）。
同时提供 BBDown 自动安装功能。
直接运行：  python3 start_api.py
也可由 main.py 导入调用：  from start_api import ensure_api_running, ensure_bbdown_installed
"""

import json
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
BBDOWN_INSTALL_PATH  = Path("/usr/local/bin/BBDown")
BBDOWN_FALLBACK_PATH = _project_root / "tools" / "BBDown"

# BBDown GitHub API
_BBDOWN_API_URL = "https://api.github.com/repos/nilaoda/BBDown/releases/latest"


def _platform_keyword() -> str:
    """返回用于匹配 asset 名称的关键字，如 'linux-x64'。"""
    machine = platform.machine().lower()
    system  = platform.system().lower()

    if system == "linux":
        return "linux-arm64" if machine in ("aarch64", "arm64") else "linux-x64"
    if system == "darwin":
        return "osx-arm64" if machine in ("aarch64", "arm64") else "osx-x64"
    if system == "windows":
        return "win-x64"
    return "linux-x64"


def _get_bbdown_download_url() -> str | None:
    """
    请求 GitHub API 获取最新 BBDown Release，
    根据当前平台匹配对应 asset 的 browser_download_url。
    """
    keyword = _platform_keyword()
    print(f"[start_api] 🔍 查询 BBDown 最新版本（平台：{keyword}）...", flush=True)
    try:
        req = urllib.request.Request(
            _BBDOWN_API_URL,
            headers={"User-Agent": "bbdown-telegrambot/1.0", "Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[start_api] ❌ 无法请求 GitHub API：{e}")
        return None

    assets = data.get("assets", [])
    tag    = data.get("tag_name", "unknown")
    print(f"[start_api] 📦 最新版本：{tag}，共 {len(assets)} 个资源")

    for asset in assets:
        name: str = asset.get("name", "")
        if keyword in name and name.endswith(".zip"):
            url = asset["browser_download_url"]
            print(f"[start_api] 📥 匹配到：{name}")
            return url

    print(f"[start_api] ❌ 未找到匹配 '{keyword}' 的资源")
    return None


def find_bbdown() -> str | None:
    """
    按优先级查找 BBDown：
    1. /usr/local/bin/BBDown
    2. 项目目录/tools/BBDown
    3. 系统 PATH
    """
    for p in (BBDOWN_INSTALL_PATH, BBDOWN_FALLBACK_PATH):
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which("BBDown") or shutil.which("bbdown")


def ensure_bbdown_installed() -> str | None:
    """
    确保 BBDown 已安装。
    如果未找到，自动从 GitHub 下载最新 Release 并安装。
    返回可执行文件的绝对路径，失败返回 None。
    """
    existing = find_bbdown()
    if existing:
        print(f"[start_api] ✅ BBDown 已存在：{existing}")
        return existing

    url = _get_bbdown_download_url()
    if not url:
        return None

    tmp_zip = Path("/tmp/bbdown_dl.zip")
    tmp_dir = Path("/tmp/bbdown_extract")
    # 清理可能残留的临时文件
    tmp_zip.unlink(missing_ok=True)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    print(f"[start_api] ⬇️  正在下载 ...", flush=True)
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

    # 在解压目录中查找可执行文件（名为 BBDown 或不含扩展名）
    extracted = None
    for f in tmp_dir.iterdir():
        if f.name.lower() in ("bbdown", "bbdown.exe") or (f.is_file() and not f.suffix):
            extracted = f
            break
    if extracted is None:
        candidates = [f for f in tmp_dir.iterdir() if f.is_file()]
        extracted  = candidates[0] if candidates else None
    if extracted is None:
        print("[start_api] ❌ 解压内容为空")
        return None

    # 尝试安装到 /usr/local/bin，没有权限则降级到 tools/
    install_path = BBDOWN_INSTALL_PATH
    try:
        shutil.copy2(extracted, install_path)
    except PermissionError:
        install_path = BBDOWN_FALLBACK_PATH
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, install_path)
        print(f"[start_api] ⚠️  无 /usr/local/bin 写入权限，安装到 {install_path}")

    install_path.chmod(install_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[start_api] ✅ BBDown 安装到 {install_path}")

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
    如果端口已监听，直接返回 True。
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

    print(f"\n[start_api] ❌ 启动超时（30s），请检查： docker logs {CONTAINER_NAME}")
    return False


if __name__ == "__main__":
    bbdown = ensure_bbdown_installed()
    if not bbdown:
        print("❌ BBDown 安装失败")
        sys.exit(1)
    ok = ensure_api_running()
    sys.exit(0 if ok else 1)
