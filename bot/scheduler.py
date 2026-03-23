import asyncio
import logging
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
from bilibili_api import get_up_videos
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
    """定时轮询订阅列表，检查是否有新视频。
    
    直接调用 get_up_videos()，它内部处理了 WBI 签名、Cookie 和关键词过滤。
    """
    logger.info("Running subscription check...")
    subs = await get_all_subscriptions()
    if not subs:
        return

    for sub in subs:
        try:
            # 直接调用封装好的高可用 API，内部处理 WBI 签名 + Cookie + 关键词过滤
            videos = await get_up_videos(sub.uid, pn=1, ps=5, keywords=sub.keyword)

            for video in videos:
                bvid = video['bvid']
                title = video['title']

                if await is_bvid_downloaded(bvid):
                    continue

                logger.info(f"[WBI API] New video for {sub.uid}: {title} ({bvid})")
                # 新视频入库，保持本地缓存与实时数据同步
                await _upsert_new_video(sub.uid, bvid, title)
                await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Error checking sub {sub.uid}: {e}")


async def process_auto_download(bot: Bot, chat_id: int, uid: str, bvid: str, title: str, up_name: str = None):
    """自动下载任务 - 使用统一的 SubprocessExecutor"""
    video_url = f"https://www.bilibili.com/video/{bvid}"
    up_display = f" ({up_name})" if up_name else ""
    msg = await bot.send_message(
        chat_id,
        f"Auto-download triggered for new video by **UID: {uid}**{up_display}:\n**{title}**\nStarting download...",
        parse_mode="Markdown"
    )
    
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
