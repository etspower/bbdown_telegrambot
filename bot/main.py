import asyncio
import logging
import re
import os
from io import BytesIO

import qrcode
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
    
    # We use BBDown login command and capture its stdout to find the QR link
    # BBDown saves its login data to its working directory. We set cwd=DATA_DIR
    # so `.data` is stored persistently in the data volume.
    cmd = [BBDOWN_PATH, "login"]
    logger.info(f"Attempting to run BBDown with path: '{BBDOWN_PATH}'")
    logger.info(f"Command list: {cmd}")
    logger.info(f"Current Working Directory: {DATA_DIR}")
    try:
        if not os.path.exists(BBDOWN_PATH):
            logger.error(f"FATAL: The file {BBDOWN_PATH} does not exist at the absolute path.")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=DATA_DIR
        )
    except Exception as e:
        await status_msg.edit_text(f"Failed to start BBDown: {e}")
        return

    qr_sent = False
    qr_file_path = os.path.join(DATA_DIR, "qrcode.png")
    
    # Remove any existing qrcode.png from previous sessions
    if os.path.exists(qr_file_path):
        try:
            os.remove(qr_file_path)
        except:
            pass

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
            # BBDown has created qrcode.png in DATA_DIR
            await asyncio.sleep(1) # Give it a second to finish writing
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
                logger.warning(f"Saw qrcode.png in output, but file does not exist locally at {qr_file_path}.")
                
        # Provide real-time feedback on scan status
        if ("成功" in decoded_line and "qrcode.png" not in decoded_line) or "SESSDATA=" in decoded_line:
            # e.g., "登录成功: Username" or printing SESSDATA
            # Avoid sending SESSDATA token to the chat directly for security
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
    
    # Clean up QR code file
    if os.path.exists(qr_file_path):
        try:
            os.remove(qr_file_path)
        except:
            pass
    
    if process.returncode == 0:
        if qr_sent:
            await status_msg.edit_text("Login successful!")
        else:
            await status_msg.edit_text("BBDown exited but no new QR code found. You may already be logged in.")
    else:
        await status_msg.edit_text(f"Login failed! BBDown exited with code {process.returncode}.")

async def main():
    logger.info("Initializing database...")
    await init_db()
    
    logger.info("Starting scheduler...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, 'interval', minutes=30, args=[bot])
    scheduler.start()
    
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
