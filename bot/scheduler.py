import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from pathlib import Path
import shutil
from aiogram.types import FSInputFile

from bot.database import (
    get_all_subscriptions, 
    is_bvid_downloaded, 
    is_bvid_downloading,
    mark_bvid_downloaded,
    mark_bvid_downloading,
    mark_bvid_abandoned,
    increment_retry_count,
    MAX_RETRY,
    upsert_up_video_url,
    update_video_title,
)
from bot.bilibili_api import get_up_videos
from bot.config import BBDOWN_PATH, DATA_DIR, VIDEO_EXT, AUDIO_EXT, SCHEDULER_MAX_PAGES
from bot.subprocess_executor import (
    SubprocessExecutor, DEFAULT_DOWNLOAD_TIMEOUT, create_progress_bar
)
from bot.utils import sort_downloaded_files

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
            # Pagination controlled solely by raw_count from API
            for page in range(1, SCHEDULER_MAX_PAGES + 1):
                raw_count, videos = await get_up_videos(sub.uid, pn=page, ps=5, keywords=sub.keyword)
                if raw_count == 0:
                    break

                for video in videos:
                    bvid = video['bvid']
                    title = video['title']

                    # 跳过已完成、已放弃或正在下载的视频
                    if await is_bvid_downloaded(bvid) or await is_bvid_downloading(bvid):
                        continue

                    # 先标记为 DOWNLOADING，防止重复触发
                    await mark_bvid_downloading(sub.uid, bvid)

                    logger.info(f"[WBI API] New video for {sub.uid}: {title} ({bvid})")
                    await _upsert_new_video(sub.uid, bvid, title)
                    await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                    await asyncio.sleep(5)

        except TelegramRetryAfter as e:
            logger.warning(f"Telegram rate limit hit, sleeping {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error(f"Error checking sub {sub.uid}: {e}")

        # Throttle between subscriptions to avoid burst requests
        await asyncio.sleep(2)


def _sort_downloaded_files(files):
    """按文件类型排序：视频 > 音频 > 其他，确保发送顺序可控。"""
    return sort_downloaded_files(files)


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
        await retry_and_cleanup(bot, chat_id, uid, bvid, title, dl_dir, is_timeout=True)
        return

    # 对齐手动下载逻辑：有文件就尝试上传，不只看 return_code
    downloaded_files = [
        f for f in dl_dir.rglob("*")
        if f.is_file() and f.suffix.lower() not in ['.jpg', '.png']
    ]
    
    if downloaded_files:
        # 按类型排序：视频 → 音频 → 其他
        downloaded_files = _sort_downloaded_files(downloaded_files)
        try:
            await msg.edit_text("Uploading file...")
            for f in downloaded_files:
                fobj = FSInputFile(str(f))
                ext = f.suffix.lower()
                if ext in VIDEO_EXT:
                    await bot.send_video(chat_id, fobj, caption=title)
                elif ext in AUDIO_EXT:
                    await bot.send_audio(chat_id, fobj, caption=title)
                else:
                    await bot.send_document(chat_id, fobj, caption=title)
            await msg.delete()
            await mark_bvid_downloaded(uid, bvid)
            shutil.rmtree(dl_dir, ignore_errors=True)
            return  # 成功路径直接返回，不进入重试计数逻辑
        except Exception as e:
            await msg.edit_text(f"Upload failed: {e}")
    else:
        # 没有文件，根据 return_code 判断错误类型
        if result.return_code == 0:
            await msg.edit_text("Download succeeded but file not found.")
        else:
            await msg.edit_text(f"Download failed with exit code {result.return_code}.")

    # 失败路径：增加重试计数
    await retry_and_cleanup(bot, chat_id, uid, bvid, title, dl_dir, is_timeout=False)


async def retry_and_cleanup(bot, chat_id, uid, bvid, title, dl_dir, is_timeout: bool):
    """Unified retry counting + cleanup logic."""
    retry_count = await increment_retry_count(uid, bvid)
    if retry_count >= MAX_RETRY:
        logger.warning(f"[{bvid}] Auto-download failed {retry_count} times, marking as abandoned.")
        await mark_bvid_abandoned(uid, bvid)
        try:
            await bot.send_message(
                chat_id,
                f"⚠️ 视频 **{title}** 推送失败 {retry_count} 次（超过 {MAX_RETRY} 次上限），已跳过。",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    elif is_timeout:
        await bot.send_message(chat_id, f"⏰ 视频 **{title}** 下载超时，将于下次轮询重试。（{retry_count}/{MAX_RETRY}）")
    # Cleanup
    shutil.rmtree(dl_dir, ignore_errors=True)
