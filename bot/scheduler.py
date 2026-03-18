import asyncio
import logging
import httpx
from aiogram import Bot

from database import (
    get_all_subscriptions, 
    is_bvid_downloaded, 
    mark_bvid_downloaded
)

# Re-use your download logic here
from handlers import get_video_info, create_progress_bar
from config import BBDOWN_PATH, DATA_DIR
from pathlib import Path
import os
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

async def check_subscriptions(bot: Bot):
    logger.info("Running subscription check...")
    subs = await get_all_subscriptions()
    if not subs:
        return

    # Use bilibili API to get user's recent videos
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    async with httpx.AsyncClient(headers=headers) as client:
        for sub in subs:
            # Bilibili API to get videos by UP
            url = f"https://api.bilibili.com/x/space/wbi/arc/search?mid={sub.uid}&ps=5&tid=0&pn=1"
            try:
                # Note: Bilibili WBI signing might block raw API calls without proper headers/cookies.
                # For a robust approach, you'd need a WBI signing algorithm. Let's use RSS as fallback if API fails
                # or just use the generic API.
                resp = await client.get(url, timeout=10.0)
                data = resp.json()
                
                if data.get('code') != 0:
                    logger.error(f"Failed to fetch videos for {sub.uid}: {data.get('message')}")
                    continue
                    
                vlist = data['data']['list']['vlist']
                
                for video in vlist:
                    bvid = video['bvid']
                    title = video['title']
                    
                    if await is_bvid_downloaded(bvid):
                        continue
                        
                    # Check keyword
                    if sub.keyword:
                        filter_keys = [k.strip().lower() for k in sub.keyword.replace('，', ',').split(',') if k.strip()]
                        if filter_keys and not any(k in title.lower() for k in filter_keys):
                            continue
                        
                    logger.info(f"New video found for {sub.uid}: {title} ({bvid})")
                    await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                    await asyncio.sleep(5)  # Avoid rate limiting
                    
            except Exception as e:
                logger.error(f"Error checking sub {sub.uid}: {e}")

async def process_auto_download(bot: Bot, chat_id: int, uid: str, bvid: str, title: str, up_name: str = None):
    video_url = f"https://www.bilibili.com/video/{bvid}"
    up_display = f" ({up_name})" if up_name else ""
    msg = await bot.send_message(chat_id, f"Auto-download triggered for new video by **UID: {uid}**{up_display}:\n**{title}**\nStarting download...", parse_mode="Markdown")
    
    dl_dir = Path(DATA_DIR) / "downloads" / "auto" / bvid
    dl_dir.mkdir(parents=True, exist_ok=True)
    
    # We download highest quality Video+Audio
    cmd = [BBDOWN_PATH, video_url, "--work-dir", str(dl_dir)]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=DATA_DIR
        )
    except Exception as e:
        await msg.edit_text(f"Failed to start auto-download: {e}")
        return

    # For auto download we might not want to throttle message edits since it runs in BG
    # To avoid flood, we'll only update every 15 seconds
    last_update = asyncio.get_event_loop().time()
    last_pct = 0.0
    
    while True:
        line = await process.stdout.readline()
        if not line:
            break
            
        decoded = line.decode('utf-8', errors='ignore').strip()
        import re
        from handlers import PROGRESS_PATTERN
        
        match = PROGRESS_PATTERN.search(decoded)
        if match:
            try:
                pct = float(match.group(1))
                now = asyncio.get_event_loop().time()
                if (pct - last_pct) >= 20.0 or (now - last_update) >= 15.0:
                    bar = create_progress_bar(pct)
                    try:
                        await msg.edit_text(f"Auto-downloading: **{title}**\n`{bar}`")
                    except:
                        pass
                    last_pct = pct
                    last_update = now
            except ValueError:
                pass
                
    await process.wait()
    
    if process.returncode == 0:
        downloaded_files = list(dl_dir.glob("*"))
        if downloaded_files:
            target_file = max(downloaded_files, key=lambda p: p.stat().st_size)
            try:
                await msg.edit_text("Uploading file...")
                file = FSInputFile(str(target_file))
                await bot.send_video(chat_id, file, caption=title)
                await msg.delete()
                await mark_bvid_downloaded(uid, bvid)
            except Exception as e:
                await msg.edit_text(f"Upload failed: {e}")
        else:
            await msg.edit_text("Download succeeded but file not found.")
    else:
        await msg.edit_text(f"Download failed with exit code: {process.returncode}")

    # Cleanup
    for f in dl_dir.glob("*"):
        try:
            os.remove(f)
        except Exception as e:
            logger.error(f"Cleanup error auto-dir: {e}")
