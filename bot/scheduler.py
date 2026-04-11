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
from bot.rss_fetcher import get_up_videos_rss, RSSFetchError
from bot.config import get_bbdown_path, DATA_DIR, VIDEO_EXT, AUDIO_EXT
from bot.subprocess_executor import (
    SubprocessExecutor, DEFAULT_DOWNLOAD_TIMEOUT, create_progress_bar
)
from bot.utils import sort_downloaded_files, escape_markdown

logger = logging.getLogger(__name__)


async def _upsert_new_video(uid: str, bvid: str, title: str):
    """将 RSS 发现的新视频写入本地缓存，保持 UpVideo 表与实时数据同步。"""
    try:
        url = f"https://www.bilibili.com/video/{bvid}"
        await upsert_up_video_url(uid, bvid, url)
        if title:
            await update_video_title(bvid, title)
    except Exception as e:
        logger.warning(f"Failed to upsert new video {bvid} to local cache: {e}")


async def check_subscriptions(bot: Bot):
    """定时轮询订阅列表，通过 RSSHub 检查是否有新视频。

    不再使用 B 站 WBI API（风控频繁），改为通过 RSSHub 获取最新投稿。
    RSS 常规包含最新 20 条内容，轮询间隔 30 分钟时夹岐足够。
    """
    logger.info("Running subscription check via RSSHub...")
    subs = await get_all_subscriptions()
    if not subs:
        return

    for sub in subs:
        try:
            raw_count, videos = await get_up_videos_rss(
                sub.uid,
                keywords=sub.keyword,
            )

            if raw_count == 0:
                logger.info(f"[RSSHub] UP {sub.uid}: no videos in RSS feed")
                await asyncio.sleep(2)
                continue

            for video in videos:
                bvid = video['bvid']
                title = video['title']

                if await is_bvid_downloaded(bvid) or await is_bvid_downloading(bvid):
                    continue

                await mark_bvid_downloading(sub.uid, bvid)

                logger.info(f"[RSSHub] New video for UP {sub.uid}: {title} ({bvid})")
                await _upsert_new_video(sub.uid, bvid, title)
                await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                await asyncio.sleep(5)

        except RSSFetchError as e:
            # 风控 / 连接 / 404 等具体原因，直接展示给用户
            logger.warning(f"[RSSHub] Fetch failed for UP {sub.uid}: {e}")
            try:
                await bot.send_message(
                    sub.chat_id,
                    f"⚠️ **订阅轮询失败** (UID: `{sub.uid}`\n{e.user_message}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        except TelegramRetryAfter as e:
            logger.warning(f"Telegram rate limit hit, sleeping {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error(f"Unexpected error checking sub {sub.uid}: {e}")

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
        f"Auto-download triggered for new video by **UID: {uid}**{up_display}:\n**{escape_markdown(title)}**\nStarting download...",
        parse_mode="Markdown"
    )

    dl_dir = Path(DATA_DIR) / "downloads" / "auto" / bvid
    dl_dir.mkdir(parents=True, exist_ok=True)

    executor = SubprocessExecutor(timeout=DEFAULT_DOWNLOAD_TIMEOUT)
    last_update = asyncio.get_running_loop().time()
    last_pct = 0.0

    try:
        async for progress in executor.run_with_progress(
            [get_bbdown_path(), video_url, "--work-dir", str(dl_dir)],
            DATA_DIR
        ):
            now = asyncio.get_running_loop().time()
            if (progress.percentage - last_pct) >= 20.0 or (now - last_update) >= 15.0:
                bar = create_progress_bar(progress.percentage)
                try:
                    await msg.edit_text(f"Auto-downloading: **{escape_markdown(title)}**\n`{bar}`")
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

    downloaded_files = [
        f for f in dl_dir.rglob("*")
        if f.is_file() and f.suffix.lower() not in ['.jpg', '.png']
    ]
    
    if downloaded_files:
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
            return
        except Exception as e:
            await msg.edit_text(f"Upload failed: {e}")
    else:
        if result.return_code == 0:
            await msg.edit_text("Download succeeded but file not found.")
        else:
            await msg.edit_text(f"Download failed with exit code {result.return_code}.")

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
    shutil.rmtree(dl_dir, ignore_errors=True)
