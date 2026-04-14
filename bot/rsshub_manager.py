"""
RSSHub 容器管理与 Cookie 同步模块

职责：
1. sync_cookie_to_rsshub()  — 从 BBDown.data 提取登录凭证，写入 rsshub.env
2. ensure_rsshub_running()  — 检测 rsshub 容器状态，未运行时自动拉起
3. is_logged_in()           — 检测 BBDown.data 是否存在且含有 SESSDATA

调用时机：
- main.py 启动时：已登录 → 直接拉起 rsshub
- cmd_login 登录成功后：同步 Cookie → 拉起 rsshub → 发送反馈
"""

import asyncio
import logging
import os
import re
from pathlib import Path

from bot.config import DATA_DIR

logger = logging.getLogger(__name__)

# RSSHub Cookie 环境文件路径
# 写到项目根目录（compose 里所有容器共享此目录）
# 注意：不要写到 ./data/bot（只有 bbdown-bot 能访问）
_PROJECT_ROOT = Path(__file__).parent.parent
RSSHUB_ENV_FILE = _PROJECT_ROOT / "rsshub.env"

# docker compose 命令（支持新旧两种写法）
_COMPOSE_CMD: list[str] | None = None


async def _get_compose_cmd() -> list[str]:
    """自动检测可用的 docker compose / docker-compose 命令。"""
    global _COMPOSE_CMD
    if _COMPOSE_CMD is not None:
        return _COMPOSE_CMD
    for cmd in (["docker", "compose"], ["docker-compose"]):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, "version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                _COMPOSE_CMD = cmd
                logger.info(f"docker compose cmd: {' '.join(cmd)}")
                return _COMPOSE_CMD
        except FileNotFoundError:
            continue
    _COMPOSE_CMD = ["docker", "compose"]  # fallback
    return _COMPOSE_CMD


def is_logged_in() -> bool:
    """检测 BBDown.data 是否存在且包含 SESSDATA（即已完成 B 站登录）。"""
    data_file = Path(DATA_DIR) / "BBDown.data"
    if not data_file.exists():
        return False
    try:
        content = data_file.read_bytes().decode("utf-8", errors="ignore")
        return bool(re.search(r"SESSDATA=[^;&\s]+", content))
    except Exception:
        return False


async def sync_cookie_to_rsshub() -> bool:
    """
    从 BBDown.data 提取登录凭证，写入 rsshub.env。

    rsshub.env 格式（被 docker-compose.yml env_file 引用）：
        BILIBILI_COOKIE_<UID>=SESSDATA=xxx;buvid3=xxx;DedeUserID=xxx

    Returns:
        True  — 成功写入（含有效 SESSDATA）
        False — 未找到凭证或写入失败
    """
    data_file = Path(DATA_DIR) / "BBDown.data"
    if not data_file.exists():
        logger.warning("sync_cookie_to_rsshub: BBDown.data not found")
        return False

    try:
        content = data_file.read_bytes().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"sync_cookie_to_rsshub: read BBDown.data failed: {e}")
        return False

    # 提取 SESSDATA
    m_sess = re.search(r"SESSDATA=([^;&\s]+)", content)
    if not m_sess:
        logger.warning("sync_cookie_to_rsshub: no SESSDATA in BBDown.data")
        return False
    sessdata = m_sess.group(1)

    # 提取 DedeUserID（自己账号 UID）
    m_uid = re.search(r"DedeUserID=([^;&\s]+)", content)
    uid = m_uid.group(1) if m_uid else "0"

    # 提取 buvid3（若不存在则读持久化文件）
    m_buv = re.search(r"buvid3=([^;&\s]+)", content)
    if m_buv:
        buvid3 = m_buv.group(1)
    else:
        buvid3_file = Path(DATA_DIR) / ".buvid3"
        buvid3 = buvid3_file.read_text().strip() if buvid3_file.exists() else ""

    # 提取 bili_jct（CSRF token，部分接口需要）
    m_jct = re.search(r"bili_jct=([^;&\s]+)", content)
    bili_jct = m_jct.group(1) if m_jct else ""

    # 组装 cookie 字符串
    parts = [f"SESSDATA={sessdata}", f"DedeUserID={uid}"]
    if buvid3:
        parts.append(f"buvid3={buvid3}")
    if bili_jct:
        parts.append(f"bili_jct={bili_jct}")
    cookie_str = ";".join(parts)

    env_line = f"BILIBILI_COOKIE_{uid}={cookie_str}\n"

    try:
        RSSHUB_ENV_FILE.write_text(env_line, encoding="utf-8")
        logger.info(f"rsshub.env written: BILIBILI_COOKIE_{uid} (SESSDATA={sessdata[:8]}...)")
        return True
    except Exception as e:
        logger.error(f"sync_cookie_to_rsshub: write rsshub.env failed: {e}")
        return False


async def _is_rsshub_container_running() -> bool:
    """通过 docker inspect 检测 bbdown-rsshub 容器是否处于 running 状态。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect",
            "--format", "{{.State.Running}}",
            "bbdown-rsshub",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "true"
    except Exception:
        return False


async def ensure_rsshub_running() -> tuple[bool, str]:
    """
    确保 rsshub 容器正在运行。

    执行顺序：
    1. 同步 Cookie → rsshub.env
    2. docker compose up -d rsshub

    Returns:
        (success: bool, message: str)
    """
    # 1. 先同步 Cookie
    cookie_ok = await sync_cookie_to_rsshub()
    if not cookie_ok:
        msg = "⚠️ rsshub 已尝试启动，但未找到 B 站登录凭证，订阅功能可能无法正常工作。"
        logger.warning(msg)
        # 仍然继续尝试启动容器（可能之前已有 rsshub.env）

    # 2. 检测是否已在运行
    if await _is_rsshub_container_running():
        logger.info("rsshub container already running, skip start")
        if cookie_ok:
            # Cookie 已更新，重启使其生效
            compose_cmd = await _get_compose_cmd()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *compose_cmd, "restart", "rsshub",
                    cwd=str(_PROJECT_ROOT),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=30)
                logger.info("rsshub restarted to apply new cookie")
            except Exception as e:
                logger.warning(f"rsshub restart failed: {e}")
        return True, "✅ RSSHub 已在运行"

    # 3. 拉起容器
    logger.info("Starting rsshub container via docker compose...")
    compose_cmd = await _get_compose_cmd()
    try:
        proc = await asyncio.create_subprocess_exec(
            *compose_cmd, "up", "-d", "rsshub",
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "❌ RSSHub 启动超时（60s），请检查 Docker 状态"

        if proc.returncode != 0:
            err = stderr.decode(errors="ignore").strip()[-300:]
            logger.error(f"rsshub start failed: {err}")
            return False, f"❌ RSSHub 启动失败：{err}"

        # 等待容器进入 running 状态（最多 20 秒）
        for _ in range(10):
            await asyncio.sleep(2)
            if await _is_rsshub_container_running():
                logger.info("rsshub container started successfully")
                return True, "✅ RSSHub 已成功启动"

        return False, "⚠️ RSSHub 容器已创建但未进入 running 状态，请稍后用 docker ps 检查"

    except FileNotFoundError:
        return False, "❌ 未找到 docker 命令，请确认 Docker 已安装"
    except Exception as e:
        logger.exception("ensure_rsshub_running unexpected error")
        return False, f"❌ 启动 RSSHub 时发生异常：{e}"
