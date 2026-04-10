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

from bot.config import BOT_TOKEN, ADMIN_ID, DATA_DIR, API_URL, is_admin
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
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)
logger.info(f"📝 日志系统初始化完成，日志文件: {LOG_FILE}")

# ── 工具函数：将项目根加入 sys.path ───────────────────────────────────────
def _ensure_project_in_path():
    root = str(Path(__file__).parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


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

    # 如果配置的路径不存在，尝试自动查找 / 重新安装
    if not os.path.exists(bbdown_path):
        _ensure_project_in_path()
        try:
            from start_api import ensure_bbdown_installed
            resolved = ensure_bbdown_installed()
        except ImportError:
            resolved = shutil.which("BBDown") or shutil.which("bbdown")

        if not resolved:
            await status_msg.edit_text(
                "❌ BBDown 未找到且自动安装失败！\n"
                "请手动安装：\n"
                "```bash\n"
                "wget https://github.com/nilaoda/BBDown/releases/latest/download/BBDown_linux-x64.zip \\\ \n"
                "  -O /tmp/bb.zip && unzip /tmp/bb.zip -d /tmp/bb\n"
                "sudo mv /tmp/bb/BBDown /usr/local/bin/BBDown && sudo chmod +x /usr/local/bin/BBDown\n"
                "```"
            )
            _cleanup_login_dir(login_tmp_dir)
            return
        bbdown_path = resolved
        config.BBDOWN_PATH = resolved
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
                            await message.answer_photo(photo, caption="Please scan this QR code with the Bilibili App (TV login).")
                            await status_msg.edit_text("Waiting for scan confirmation...")
                            qr_sent = True
                        except Exception as ex:
                            logger.error(f"EXCEPTION in answer_photo: {ex}", exc_info=True)
                            await status_msg.edit_text(f"Error sending QR photo: {ex}")

                if ("成功" in decoded_line and "qrcode.png" not in decoded_line) or "SESSDATA=" in decoded_line:
                    try:
                        await message.answer("✅ **Login Success!** Credentials saved.", parse_mode="Markdown")
                    except:
                        await message.answer("✅ Login Success! Credentials saved.")
                elif "失效" in decoded_line or "失败" in decoded_line or "过期" in decoded_line:
                    try:
                        await message.answer("❌ **Login Failed/Expired.** Please try `/login` again.", parse_mode="Markdown")
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
            await status_msg.edit_text("Login successful!" if qr_sent else "BBDown exited but you may already be logged in.")
        else:
            await status_msg.edit_text("Login failed! No credentials file found.")

    except Exception as e:
        logger.exception("Login process error")
        await status_msg.edit_text(f"❌ 登录过程发生错误：{e}")
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
        return
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 7860)
    await site.start()
    logger.info("Dummy web server started on port 7860 for Hugging Face.")


async def main():
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not set!")
        return
    if ADMIN_ID == 0:
        logger.warning("WARNING: ADMIN_ID is 0. The bot will reject ALL user commands.")

    _ensure_project_in_path()

    # ── 自动安装 BBDown ──
    logger.info("🔍 检查 BBDown 安装情况...")
    try:
        from start_api import ensure_bbdown_installed
        import bot.config as config
        bbdown_path = ensure_bbdown_installed()
        if bbdown_path:
            config.BBDOWN_PATH = bbdown_path
            logger.info(f"✅ BBDown 路径已设置：{bbdown_path}")
        else:
            logger.critical(
                "❌ BBDown 未找到且自动安装失败！\n"
                "请手动安装后再启动，或在 .env 中设置 BBDOWN_PATH=正确路径。"
            )
            sys.exit(1)
    except ImportError as e:
        logger.warning(f"无法导入 start_api.py，跳过 BBDown 自动安装：{e}")

    # ── 自动启动本地 telegram-bot-api ──
    if API_URL and ("localhost" in API_URL or "127.0.0.1" in API_URL):
        logger.info("检测到本地 API_URL，尝试通过 Docker 启动 telegram-bot-api...")
        try:
            from start_api import ensure_api_running
            if not ensure_api_running():
                logger.critical(
                    "❌ 无法启动 telegram-bot-api，Bot 无法连接。\n"
                    "请确保 Docker 已安装且可用，或将 .env 中 API_URL 清空以使用官方服务器。"
                )
                sys.exit(1)
        except ImportError as e:
            logger.critical(f"❌ 无法导入 start_api.py：{e}")
            sys.exit(1)

    # 清理上次残留的下载目录
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
