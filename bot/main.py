import asyncio
import logging
import os
import shutil
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

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

from bot.config import BOT_TOKEN, ADMIN_ID, DATA_DIR, API_URL, is_admin, get_bbdown_path
from bot.handlers import router as handlers_router
from bot.scheduler import check_subscriptions
from bot.database import init_db

# ── 日志系统初始化 ────────────────────────────────────────────────────────
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
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)
logger.info(f"📝 日志系统初始化完成，日志文件: {LOG_FILE}")

# 清理 Bot 重启后遗留的孤儿临时下载目录
downloads_dir = Path(DATA_DIR) / "downloads"
if downloads_dir.exists():
    orphan_count = 0
    for item in downloads_dir.iterdir():
        if item.is_dir():
            try:
                shutil.rmtree(item)
                orphan_count += 1
            except Exception:
                pass
    if orphan_count:
        logger.info(f"🧹 清理了 {orphan_count} 个孤儿下载目录")


def _ensure_project_in_path():
    root = str(Path(__file__).parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


# ── Bot 初始化 ────────────────────────────────────────────────────────────
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
    bbdown_path = get_bbdown_path()

    login_tmp_dir = os.path.join(DATA_DIR, f"tmp_login_{message.from_user.id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(login_tmp_dir, exist_ok=True)

    if not os.path.exists(bbdown_path):
        _ensure_project_in_path()
        try:
            from start_api import ensure_bbdown_installed
            resolved = ensure_bbdown_installed()
        except ImportError:
            resolved = shutil.which("BBDown") or shutil.which("bbdown")

        if not resolved:
            await status_msg.edit_text(
                "❌ BBDown 未找到且自动安装失败！\n请参考项目 README 手动安装。"
            )
            _cleanup_login_dir(login_tmp_dir)
            return
        bbdown_path = resolved
        logger.info(f"BBDown resolved: {bbdown_path}")

    try:
        process = await asyncio.create_subprocess_exec(
            bbdown_path, "login",
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
    login_success = False

    try:
        async def read_output():
            nonlocal qr_sent, login_success
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
                            await message.answer_photo(photo, caption="Please scan this QR code with the Bilibili App (TV login).")
                            await status_msg.edit_text("Waiting for scan confirmation...")
                            qr_sent = True
                        except Exception as ex:
                            logger.error(f"EXCEPTION in answer_photo: {ex}", exc_info=True)
                            await status_msg.edit_text(f"Error sending QR photo: {ex}")

                if ("成功" in decoded_line and "qrcode.png" not in decoded_line) or "SESSDATA=" in decoded_line:
                    login_success = True
                    try:
                        await message.answer("✅ **Login Success!** Credentials saved.", parse_mode="Markdown")
                    except Exception:
                        await message.answer("✅ Login Success! Credentials saved.")
                elif "失效" in decoded_line or "失败" in decoded_line or "过期" in decoded_line:
                    try:
                        await message.answer("❌ **Login Failed/Expired.** Please try `/login` again.", parse_mode="Markdown")
                    except Exception:
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
            await status_msg.edit_text("Login successful!" if qr_sent else "BBDown exited but you may already be logged in.")
            login_success = True
        else:
            await status_msg.edit_text("Login failed! No credentials file found.")
            login_success = False

        # ── 登录成功后：同步 Cookie 到 rsshub 并拉起容器 ──────────────────
        if login_success:
            await _post_login_start_rsshub(message)

    except Exception as e:
        logger.exception("Login process error")
        await status_msg.edit_text(f"❌ 登录过程发生错误：{e}")
    finally:
        _cleanup_login_dir(login_tmp_dir)


async def _post_login_start_rsshub(message: types.Message):
    """登录成功后同步 Cookie 并拉起 RSSHub 容器，向用户反馈结果。"""
    # 仅调试模式（非全容器化）才需要手动拉起
    if not _is_debug_mode():
        logger.info("Full container mode, skip manual rsshub start")
        return

    notify = await message.answer("🔄 正在同步 B 站凭证到 RSSHub 并启动容器...")
    try:
        from bot.rsshub_manager import ensure_rsshub_running
        success, msg = await ensure_rsshub_running()
        await notify.edit_text(f"RSSHub: {msg}")
    except Exception as e:
        logger.exception("_post_login_start_rsshub error")
        await notify.edit_text(f"⚠️ RSSHub 启动时发生异常：{e}")


def _is_debug_mode() -> bool:
    """
    判断当前是否为「调试模式」（Python 直接运行，非全容器化）。

    判断依据：RSSHUB_BASE_URL 包含 localhost 或 127.0.0.1。
    全容器化时 rsshub 通过内网 http://rsshub:1200 访问，不含 localhost。
    """
    rsshub_url = os.getenv("RSSHUB_BASE_URL", "")
    return "localhost" in rsshub_url or "127.0.0.1" in rsshub_url


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
        return
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 7860)
    await site.start()
    logger.info("Dummy web server started on port 7860 for Hugging Face.")


async def _startup_rsshub_check():
    """
    启动时检测 B 站登录状态与 RSSHub 容器状态，按情况自动处理：

    - 已登录 + rsshub 未运行  → 自动拉起，发消息通知
    - 已登录 + rsshub 已运行  → 同步最新 Cookie（重启生效），静默
    - 未登录                  → 只记录日志，等用户 /login 后再处理
    """
    if not _is_debug_mode():
        logger.info("Full container mode: rsshub managed by docker compose, skip startup check")
        return

    from bot.rsshub_manager import is_logged_in, ensure_rsshub_running, _is_rsshub_container_running

    if not is_logged_in():
        logger.info("B站未登录，跳过 RSSHub 启动检测。请发送 /login 完成登录。")
        return

    logger.info("B站已登录，检测 RSSHub 容器状态...")
    running = await _is_rsshub_container_running()
    if running:
        logger.info("RSSHub 容器已在运行，同步最新 Cookie...")
        from bot.rsshub_manager import sync_cookie_to_rsshub
        ok = await sync_cookie_to_rsshub()
        if ok:
            logger.info("Cookie 同步完成（容器已运行，无需重启）")
    else:
        logger.info("RSSHub 容器未运行，自动拉起...")
        success, msg = await ensure_rsshub_running()
        logger.info(f"RSSHub 启动结果: {msg}")
        # 通知管理员
        try:
            await bot.send_message(ADMIN_ID, f"🤖 启动检测\nRSSHub: {msg}")
        except Exception as e:
            logger.warning(f"发送 RSSHub 启动通知失败: {e}")


async def main():
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not set!")
        return
    if ADMIN_ID == 0:
        logger.warning("WARNING: ADMIN_ID is 0. The bot will reject ALL user commands.")

    _ensure_project_in_path()

    try:
        from start_api import ensure_bbdown_installed, ensure_ffmpeg_installed
        import bot.config as config

        logger.info("🔍 检查 ffmpeg 安装情况...")
        if not ensure_ffmpeg_installed():
            logger.warning(
                "⚠️  ffmpeg 未找到且自动安装失败！视频合并功能可能不可用。\n"
                "   请手动安装： sudo apt-get install -y ffmpeg"
            )
        logger.info("🔍 检查 BBDown 安装情况...")
        bbdown_path = ensure_bbdown_installed()
        if bbdown_path:
            logger.info(f"✅ BBDown 路径：{bbdown_path}")
        else:
            logger.critical(
                "❌ BBDown 未找到且自动安装失败！\n"
                "请手动安装后再启动。"
            )
            sys.exit(1)
    except ImportError as e:
        logger.warning(f"无法导入 start_api.py，跳过自动安装：{e}")

    # ── 自动启动本地 telegram-bot-api ──
    if API_URL and ("localhost" in API_URL or "127.0.0.1" in API_URL):
        logger.info("检测到本地 API_URL，尝试通过 Docker 启动 telegram-bot-api...")
        try:
            from start_api import ensure_api_running
            if not ensure_api_running():
                logger.critical(
                    "❌ 无法启动 telegram-bot-api，Bot 无法连接。\n"
                    "请确保 Docker 已安装且可用，或将 .env 中 API_URL 清空。"
                )
                sys.exit(1)
        except ImportError as e:
            logger.critical(f"❌ 无法导入 start_api.py：{e}")
            sys.exit(1)

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
        BotCommand(command="login",    description="🔗 扫描二维码登录 B站 (必须)"),
        BotCommand(command="url",      description="📥 输入 B站视频链接下载"),
        BotCommand(command="help",     description="📖 查看使用帮助与说明")
    ]
    await bot.set_my_commands(commands)

    logger.info("Starting scheduler...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, 'interval', minutes=30, args=[bot])
    scheduler.start()

    # ── 启动时检测登录状态与 RSSHub 容器 ──
    asyncio.create_task(_startup_rsshub_check())

    logger.info("Starting bot...")
    if os.getenv("SPACE_ID") and AIOHTTP_AVAILABLE:
        await start_dummy_server()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot session closed.")


if __name__ == "__main__":
    asyncio.run(main())
