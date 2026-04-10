import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

# aiohttp 仅用于 Hugging Face Spaces 保活，作为可选依赖
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    web = None
    AIOHTTP_AVAILABLE = False

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import BOT_TOKEN, ADMIN_ID, BBDOWN_PATH, DATA_DIR, API_URL, is_admin
from bot.handlers import router as handlers_router
from bot.scheduler import check_subscriptions
from bot.database import init_db

# ── 日志系统初始化 ──────────────────────────────────────────────────────────
LOG_DIR = Path(DATA_DIR) / "logs"
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError as e:
    print(f"❌ Error: Cannot create log directory '{LOG_DIR}': {e}")
    raise

LOG_FILE = LOG_DIR / "bot.log"

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
root_logger.addHandler(console_handler)

file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8"
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)
logger.info(f"📝 日志系统初始化完成，日志文件: {LOG_FILE}")

# ── Telegram Bot API 本地服务器管理 ──────────────────────────────────────────

_tg_api_process: subprocess.Popen | None = None


def _is_local_api_url(url: str) -> bool:
    """判断 API_URL 是否指向本地服务器。"""
    return "localhost" in url or "127.0.0.1" in url


def _find_tg_api_binary() -> str | None:
    """在常见位置查找 telegram-bot-api 二进制文件。"""
    candidates = [
        shutil.which("telegram-bot-api"),
        "/usr/local/bin/telegram-bot-api",
        str(Path(DATA_DIR).parent / "telegram-bot-api"),
        str(Path(DATA_DIR).parent / "tools" / "telegram-bot-api"),
    ]
    for path in candidates:
        if path and Path(path).exists() and os.access(path, os.X_OK):
            return path
    return None


def _start_tg_api_server() -> bool:
    """
    启动 telegram-bot-api 本地服务器。
    返回 True 表示成功启动或已在运行，False 表示无法启动。
    """
    global _tg_api_process

    import socket
    from urllib.parse import urlparse

    parsed = urlparse(API_URL)
    port = parsed.port or 8081

    # 检查端口是否已在监听（服务已运行）
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        already_running = s.connect_ex(("127.0.0.1", port)) == 0

    if already_running:
        logger.info(f"✅ telegram-bot-api 已在端口 {port} 运行，跳过启动。")
        return True

    binary = _find_tg_api_binary()
    if not binary:
        logger.error(
            "❌ 未找到 telegram-bot-api 二进制文件！\n"
            "请按以下步骤安装：\n"
            "  wget https://github.com/tdlib/telegram-bot-api/releases/latest/"
            "download/telegram-bot-api-aarch64-linux-gnu -O /usr/local/bin/telegram-bot-api\n"
            "  chmod +x /usr/local/bin/telegram-bot-api\n"
            "（ARM64 服务器请使用 aarch64 版本，x86_64 请使用 amd64 版本）"
        )
        return False

    api_id = os.getenv("TELEGRAM_API_ID", "").strip('"').strip("'")
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip('"').strip("'")

    if not api_id or not api_hash:
        logger.error(
            "❌ 缺少 TELEGRAM_API_ID 或 TELEGRAM_API_HASH！\n"
            "请在 .env 文件中设置：\n"
            "  TELEGRAM_API_ID=你的api_id\n"
            "  TELEGRAM_API_HASH=你的api_hash\n"
            "从 https://my.telegram.org 获取"
        )
        return False

    tg_data_dir = Path(DATA_DIR) / "telegram-api"
    tg_data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        binary,
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        f"--dir={tg_data_dir}",
        f"--port={port}",
        "--local",
    ]

    log_file = LOG_DIR / "telegram-api.log"
    log_fd = open(log_file, "a", encoding="utf-8")

    logger.info(f"🚀 正在启动 telegram-bot-api（端口 {port}）...")
    _tg_api_process = subprocess.Popen(
        cmd,
        stdout=log_fd,
        stderr=log_fd,
        start_new_session=True,  # 与主进程解绑，避免 Ctrl+C 一起杀掉
    )

    # 等待服务就绪（最多 15 秒）
    for i in range(15):
        time.sleep(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                logger.info(f"✅ telegram-bot-api 启动成功（{i + 1}s）")
                return True
        logger.debug(f"等待 telegram-bot-api 就绪... {i + 1}s")

    logger.error("❌ telegram-bot-api 启动超时（15s），请检查日志：" + str(log_file))
    _tg_api_process.terminate()
    _tg_api_process = None
    return False


def _stop_tg_api_server():
    """关闭由本进程启动的 telegram-bot-api。"""
    global _tg_api_process
    if _tg_api_process and _tg_api_process.poll() is None:
        logger.info("🛑 正在关闭 telegram-bot-api...")
        _tg_api_process.terminate()
        try:
            _tg_api_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tg_api_process.kill()
        logger.info("✅ telegram-bot-api 已关闭。")
        _tg_api_process = None


# ── Bot 初始化 ──────────────────────────────────────────────────────────────

if API_URL and API_URL != "https://api.telegram.org":
    session = AiohttpSession(api=TelegramAPIServer.from_base(API_URL))
    bot = Bot(token=BOT_TOKEN, session=session)
    logger.info(f"Using custom Telegram API URL: {API_URL}")
else:
    bot = Bot(token=BOT_TOKEN)
    logger.info("Using default Telegram API URL: https://api.telegram.org")
dp = Dispatcher()
dp.include_router(handlers_router)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("You are not authorized to use this bot.")
        return
    await message.answer("Hello! Send me a Bilibili link or use /login to authenticate BBDown.")


@dp.message(Command("login"))
async def cmd_login(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    status_msg = await message.answer("Initializing BBDown login...")

    import bot.config as config
    bbdown_path = config.BBDOWN_PATH

    login_tmp_dir = os.path.join(DATA_DIR, f"tmp_login_{message.from_user.id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(login_tmp_dir, exist_ok=True)

    cmd = [bbdown_path, "login"]
    logger.info(f"Attempting to run BBDown with path: '{bbdown_path}'")
    logger.info(f"Command list: {cmd}")
    logger.info(f"Login tmp dir: {login_tmp_dir}")

    if not os.path.exists(bbdown_path):
        bbdown_resolved = shutil.which("BBDown") or shutil.which("bbdown")
        if not bbdown_resolved:
            await status_msg.edit_text(
                f"❌ BBDown not found!\n\n"
                f"Please install BBDown:\n"
                f"```bash\n"
                f"mkdir -p ~/bbdown_telegrambot/tools\n"
                f"cd ~/bbdown_telegrambot/tools\n"
                f"wget https://github.com/nilaoda/BBDown/releases/latest/download/BBDown\n"
                f"chmod +x BBDown\n"
                f"```\n\n"
                f"Then set in .env:\n"
                f"BBDOWN_PATH=tools/BBDown\n\n"
                f"Or use Docker which includes BBDown pre-installed."
            )
            _cleanup_login_dir(login_tmp_dir)
            return
        bbdown_path = bbdown_resolved
        config.BBDOWN_PATH = bbdown_resolved
        logger.info(f"BBDown resolved from PATH: {bbdown_path}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=login_tmp_dir
        )
    except Exception as e:
        await status_msg.edit_text(f"Failed to start BBDown: {e}")
        _cleanup_login_dir(login_tmp_dir)
        return

    qr_file_path = os.path.join(login_tmp_dir, "qrcode.png")
    qr_sent = False

    try:
        async def read_output():
            nonlocal qr_sent
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                try:
                    decoded_line = line.decode('utf-8').strip()
                except UnicodeDecodeError:
                    decoded_line = line.decode('gbk', errors='ignore').strip()

                logger.info(f"[BBDown] {decoded_line}")

                if not qr_sent and "qrcode.png" in decoded_line:
                    await asyncio.sleep(1)
                    if os.path.exists(qr_file_path):
                        try:
                            from aiogram.types import FSInputFile
                            photo = FSInputFile(qr_file_path)
                            await message.answer_photo(
                                photo,
                                caption="Please scan this QR code with the Bilibili App (TV login)."
                            )
                            await status_msg.edit_text("Waiting for scan confirmation...")
                            qr_sent = True
                            logger.info("QR code photo sent successfully to Telegram.")
                        except Exception as ex:
                            logger.error(f"EXCEPTION in answer_photo: {ex}", exc_info=True)
                            await status_msg.edit_text(f"Error sending QR photo: {ex}")
                    else:
                        logger.warning(f"Saw qrcode.png in output, but file does not exist at {qr_file_path}.")

                if ("成功" in decoded_line and "qrcode.png" not in decoded_line) or "SESSDATA=" in decoded_line:
                    try:
                        await message.answer(f"✅ **Login Success!** Credentials saved.", parse_mode="Markdown")
                    except:
                        await message.answer(f"✅ Login Success! Credentials saved.")
                elif "失效" in decoded_line or "失败" in decoded_line or "过期" in decoded_line:
                    try:
                        await message.answer(f"❌ **Login Failed/Expired.** Please try `/login` again.", parse_mode="Markdown")
                    except:
                        await message.answer(f"❌ Login Failed: {decoded_line}")

        try:
            await asyncio.wait_for(read_output(), timeout=180)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("❌ 登录超时（3分钟），请重新发送 /login")
            _cleanup_login_dir(login_tmp_dir)
            return

        await process.wait()

        credentials_src = os.path.join(login_tmp_dir, "BBDown.data")
        if os.path.exists(credentials_src):
            dest = os.path.join(DATA_DIR, "BBDown.data")
            shutil.copy2(credentials_src, dest)
            logger.info(f"Credentials saved to {dest}")
            if qr_sent:
                await status_msg.edit_text("Login successful!")
            else:
                await status_msg.edit_text("BBDown exited but you may already be logged in.")
        else:
            await status_msg.edit_text(f"Login failed! No credentials file found.")

    except Exception as e:
        logger.exception("Login process error")
        await status_msg.edit_text(f"❌ 登录过程发生错误：{e}")
        _cleanup_login_dir(login_tmp_dir)
        return

    finally:
        _cleanup_login_dir(login_tmp_dir)


def _cleanup_login_dir(path: str):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
            logger.info(f"Cleaned up login tmp dir: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup login tmp dir {path}: {e}")


async def health_check(request):
    return web.Response(text="BBDown Bot is running successfully on Hugging Face!")


async def start_dummy_server():
    if not AIOHTTP_AVAILABLE:
        logger.warning("aiohttp 未安装，跳过 Hugging Face Spaces 保活服务器启动。")
        return

    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 7860)
    await site.start()
    logger.info("Dummy web server started on port 7860 for Hugging Face.")


async def main():
    # ── Startup config validation ──
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not set! The bot cannot start.")
        return
    if ADMIN_ID == 0:
        logger.warning(
            "WARNING: ADMIN_ID is 0 (default). The bot will reject ALL user commands. "
            "Set ADMIN_ID in .env to your Telegram user ID."
        )

    # ── 自动启动本地 telegram-bot-api ──
    if _is_local_api_url(API_URL):
        logger.info("检测到本地 API_URL，尝试启动 telegram-bot-api 本地服务器...")
        if not _start_tg_api_server():
            logger.critical(
                "❌ 无法启动 telegram-bot-api 本地服务器，Bot 无法连接。\n"
                "请安装 telegram-bot-api 二进制文件，或将 .env 中 API_URL 改为空以使用官方服务器。"
            )
            sys.exit(1)

    # Cleanup stale downloads
    downloads_dir = Path(DATA_DIR) / "downloads"
    if downloads_dir.exists():
        shutil.rmtree(downloads_dir, ignore_errors=True)
        logger.info("Cleaned up stale download directories on startup.")

    logger.info("Initializing database...")
    await init_db()

    logger.info("Setting bot commands menu...")
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="settings", description="✨ 机器人控制面板 (推荐)"),
        BotCommand(command="login", description="🔗 扫描二维码登录 B站 (必须)"),
        BotCommand(command="url", description="📥 输入 B站视频链接下载"),
        BotCommand(command="help", description="📖 查看使用帮助与说明")
    ]
    await bot.set_my_commands(commands)

    logger.info("Starting scheduler...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, 'interval', minutes=30, args=[bot])
    scheduler.start()

    logger.info("Starting bot...")
    if os.getenv("SPACE_ID") and AIOHTTP_AVAILABLE:
        await start_dummy_server()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot session closed.")
        _stop_tg_api_server()


if __name__ == "__main__":
    asyncio.run(main())
