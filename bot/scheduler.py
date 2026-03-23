import asyncio
import logging
from aiogram import Bot
from pathlib import Path
import shutil
from aiogram.types import FSInputFile

from database import (
    get_all_subscriptions, 
    is_bvid_downloaded, 
    mark_bvid_downloaded,
    increment_retry_count,
    MAX_RETRY,
    upsert_up_video_url,
    update_video_title,
)
from bilibili_api import get_up_videos
from config import BBDOWN_PATH, DATA_DIR
from subprocess_executor import (
    SubprocessExecutor, DEFAULT_DOWNLOAD_TIMEOUT, create_progress_bar
)

logger = logging.getLogger(__name__)

# 文件类型常量（与 handlers.py 保持同步）
VIDEO_EXT = {'.mp4', '.mkv', '.flv'}
AUDIO_EXT = {'.mp3', '.m4a', '.aac'}


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
            # 翻页检查：最多检查前 2 页（共 10 条），遇到已下载的视频则停止
            # 列表按时间排序，新视频在前，下载过的视频在后面
            max_pages = 2
            new_video_found = True  # 控制是否继续翻页
            
            for page in range(1, max_pages + 1):
                if not new_video_found:
                    break
                
                videos = await get_up_videos(sub.uid, pn=page, ps=5, keywords=sub.keyword)
                if not videos:
                    break

                for video in videos:
                    bvid = video['bvid']
                    title = video['title']

                    # 遇到已下载的视频（列表按时间倒序，旧视频在后）
                    # 说明已到达旧内容边界，停止翻页
                    if await is_bvid_downloaded(bvid):
                        new_video_found = False
                        break

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

    executor = SubprocessExecutor(timeout=DEFAULT_DOWNLOAD_TIMEOUT)
    last_update = asyncio.get_running_loop().time()
    last_pct = 0.0

    try:
        async for progress in executor.run_with_progress(
            [BBDOWN_PATH, video_url, "--work-dir", str(dl_dir)],
            DATA_DIR
        ):
            now = asyncio.get_running_loop().time()
            if (progress.percentage - last_pct) >= 20.0 or (now - last_update) >= 15.0:
                bar = create_progress_bar(progress.percentage)
                try:
                    await msg.edit_text(f"Auto-downloading: **{title}**\n`{bar}`")
                except Exception:
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
        await msg.edit_text(
            f"❌ **自动下载超时，已强制终止任务 (超时 {DEFAULT_DOWNLOAD_TIMEOUT//60} 分钟)**。"
        )
        shutil.rmtree(dl_dir, ignore_errors=True)
        return

    if result.return_code == 0:
        downloaded_files = [f for f in dl_dir.glob("*") if f.is_file()]
        if downloaded_files:
            try:
                await msg.edit_text("Uploading file...")
                for f in downloaded_files:
                    fobj = FSInputFile(str(f))
                    if f.suffix.lower() in VIDEO_EXT:
                        await bot.send_video(chat_id, fobj, caption=title)
                    elif f.suffix.lower() in AUDIO_EXT:
                        await bot.send_audio(chat_id, fobj, caption=title)
                    else:
                        await bot.send_document(chat_id, fobj, caption=title)
                await msg.delete()
                await mark_bvid_downloaded(uid, bvid)
            except Exception as e:
                await msg.edit_text(f"Upload failed: {e}")
        else:
            await msg.edit_text("Download succeeded but file not found.")
    else:
        await msg.edit_text(f"Download failed with exit code: {result.return_code}")

    # 重试计数：失败时增加计数，达到上限后标记为放弃不再推送
    retry_count = await increment_retry_count(uid, bvid)
    if retry_count >= MAX_RETRY:
        logger.warning(f"[{bvid}] Auto-download failed {retry_count} times, marking as abandoned.")
        await mark_bvid_downloaded(uid, bvid)
        try:
            await bot.send_message(
                chat_id,
                f"⚠️ 视频 **{title}** 推送失败 {retry_count} 次（超过 {MAX_RETRY} 次上限），已跳过。",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    # Cleanup
    shutil.rmtree(dl_dir, ignore_errors=True)
