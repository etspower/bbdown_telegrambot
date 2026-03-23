import asyncio
import logging
import httpx
from aiogram import Bot
from pathlib import Path
import os
from aiogram.types import FSInputFile

from database import (
    get_all_subscriptions, 
    is_bvid_downloaded, 
    mark_bvid_downloaded,
    upsert_up_video_url,
    update_video_title,
)
from handlers import get_video_info
from config import BBDOWN_PATH, DATA_DIR
from subprocess_executor import (
    SubprocessExecutor, DEFAULT_DOWNLOAD_TIMEOUT, create_progress_bar
)

logger = logging.getLogger(__name__)


async def _upsert_new_video(uid: str, bvid: str, title: str):
    """将 API 发现的新视频写入本地缓存，保持 UpVideo 表与实时数据同步。"""
    try:
        url = f"https://www.bilibili.com/video/{bvid}"
        await upsert_up_video_url(uid, bvid, url)
        if title:
            await update_video_title(bvid, title)
    except Exception as e:
        logger.warning(f"Failed to upsert new video {bvid} to local cache: {e}")

async def check_subscriptions(bot: Bot):
    logger.info("Running subscription check...")
    subs = await get_all_subscriptions()
    if not subs:
        return

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    async with httpx.AsyncClient(headers=headers) as client:
        for sub in subs:
            try:
                # 始终通过 WBI API 获取最新视频，本地缓存仅用于展示，不用于判断新视频
                url = f"https://api.bilibili.com/x/space/wbi/arc/search?mid={sub.uid}&ps=5&tid=0&pn=1"
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

                    # Apply keyword filter
                    if sub.keyword:
                        filter_keys = [k.strip().lower() for k in sub.keyword.replace('，', ',').split(',') if k.strip()]
                        if filter_keys and not any(k in title.lower() for k in filter_keys):
                            continue

                    logger.info(f"[WBI API] New video for {sub.uid}: {title} ({bvid})")
                    # 新视频入库，保持本地缓存与实时数据同步（第三阶段生态打通）
                    await _upsert_new_video(sub.uid, bvid, title)
                    await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error checking sub {sub.uid}: {e}")


async def process_auto_download(bot: Bot, chat_id: int, uid: str, bvid: str, title: str, up_name: str = None):
    """自动下载任务 - 使用统一的 SubprocessExecutor"""
    video_url = f"https://www.bilibili.com/video/{bvid}"
    up_display = f" ({up_name})" if up_name else ""
    msg = await bot.send_message(chat_id, f"Auto-download triggered for new video by **UID: {uid}**{up_display}:\n**{title}**\nStarting download...", parse_mode="Markdown")
    
    dl_dir = Path(DATA_DIR) / "downloads" / "auto" / bvid
    dl_dir.mkdir(parents=True, exist_ok=True)
    
    # 使用统一的 SubprocessExecutor
    executor = SubprocessExecutor(timeout=DEFAULT_DOWNLOAD_TIMEOUT)
    
    last_update = asyncio.get_event_loop().time()
    last_pct = 0.0
    
    try:
        async for progress in executor.run_with_progress(
            [BBDOWN_PATH, video_url, "--work-dir", str(dl_dir)],
            DATA_DIR
        ):
            now = asyncio.get_event_loop().time()
            if (progress.percentage - last_pct) >= 20.0 or (now - last_update) >= 15.0:
                bar = create_progress_bar(progress.percentage)
                try:
                    await msg.edit_text(f"Auto-downloading: **{title}**\n`{bar}`")
                except:
                    pass
                last_pct = progress.percentage
                last_update = now
        
        result = await executor.wait()
        
    except Exception as e:
        logger.error(f"Error during auto-download: {e}")
        await executor.kill()
        await msg.edit_text(f"Auto-download error: {e}")
        return
    
    if result.timed_out:
        await msg.edit_text(f"❌ **自动下载超时，已强制终止任务 (超时 {DEFAULT_DOWNLOAD_TIMEOUT//60} 分钟)**。")
        for f in dl_dir.glob("*"):
            try: os.remove(f)
            except Exception as e: logger.error(f"Cleanup error: {e}")
        return
    
    if result.return_code == 0:
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
        await msg.edit_text(f"Download failed with exit code: {result.return_code}")

    # Cleanup
    for f in dl_dir.glob("*"):
        try:
            os.remove(f)
        except Exception as e:
            logger.error(f"Cleanup error auto-dir: {e}")
