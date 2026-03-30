import asyncio
import logging
import re
import os
import shutil
from io import BytesIO
from pathlib import Path

from aiohttp import web #hugging face spaces保活

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, ADMIN_ID, BBDOWN_PATH, DATA_DIR, API_URL, is_admin
from handlers import router as handlers_router
from scheduler import check_subscriptions
from database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Optionally configure Local API server if not default
session = None
if API_URL and API_URL != "https://api.telegram.org":
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(API_URL)
    )

bot = Bot(token=BOT_TOKEN, session=session)
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
    
    # 为每次登录创建独立的临时目录，避免多 Admin 并发登录时文件冲突
    import uuid
    login_tmp_dir = os.path.join(DATA_DIR, f"tmp_login_{message.from_user.id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(login_tmp_dir, exist_ok=True)
    
    cmd = [BBDOWN_PATH, "login"]
    logger.info(f"Attempting to run BBDown with path: '{BBDOWN_PATH}'")
    logger.info(f"Command list: {cmd}")
    logger.info(f"Login tmp dir: {login_tmp_dir}")
    try:
        if not os.path.exists(BBDOWN_PATH):
            logger.error(f"FATAL: The file {BBDOWN_PATH} does not exist at the absolute path.")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=login_tmp_dir  # 使用独立临时目录，qrcode.png 生成在此
        )
    except Exception as e:
        await status_msg.edit_text(f"Failed to start BBDown: {e}")
        _cleanup_login_dir(login_tmp_dir)
        return

    # qrcode.png 生成在独立临时目录中，不会与其他登录会话冲突
    qr_file_path = os.path.join(login_tmp_dir, "qrcode.png")
    qr_sent = False
    
    try:
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

        await process.wait()

        # 登录成功后才复制凭证文件
        credentials_src = os.path.join(login_tmp_dir, "BBDown.data")
        if os.path.exists(credentials_src):
            import shutil
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
        # 只负责清理临时目录
        _cleanup_login_dir(login_tmp_dir)


def _cleanup_login_dir(path: str):
    """清理登录临时目录"""
    try:
        import shutil
        if os.path.exists(path):
            shutil.rmtree(path)
            logger.info(f"Cleaned up login tmp dir: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup login tmp dir {path}: {e}")

# --- 新增的假服务代码开始 ---
async def health_check(request):
    return web.Response(text="BBDown Bot is running successfully on Hugging Face!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 7860)
    await site.start()
    logger.info("Dummy web server started on port 7860 for Hugging Face.")
# --- 新增的假服务代码结束 ---

async def main():
    # ── Startup config validation ──
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not set! The bot cannot start. Set BOT_TOKEN in .env or environment.")
        return
    if ADMIN_ID == 0:
        logger.warning(
            "WARNING: ADMIN_ID is 0 (default). The bot will reject ALL user commands. "
            "Set ADMIN_ID in .env to your Telegram user ID."
        )

    # Cleanup stale downloads from previous run
    downloads_dir = Path(DATA_DIR) / "downloads"
    if downloads_dir.exists():
        shutil.rmtree(downloads_dir, ignore_errors=True)
        logger.info("Cleaned up stale download directories on startup.")

    logger.info("Initializing database...")
    await init_db()
    
    logger.info("Starting scheduler...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, 'interval', minutes=30, args=[bot])
    scheduler.start()
    
    logger.info("Setting bot commands menu...")
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="settings", description="✨ 机器人控制面板 (推荐)"),
        BotCommand(command="login", description="🔗 扫描二维码登录 B站 (必须)"),
        BotCommand(command="url", description="📥 输入 B站视频链接下载"),
        BotCommand(command="help", description="📖 查看使用帮助与说明")
    ]
    await bot.set_my_commands(commands)
    
    logger.info("Starting bot...")
    await start_dummy_server()  # <--- 新增这一行，先启动假服务
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
