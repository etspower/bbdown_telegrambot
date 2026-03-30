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
            # 翻页检查：最多检查前 max_pages 页
            # 翻页终止只由 raw_count（API 原始返回条数）决定
            # 已下载视频只 skip 不中断，避免关键词过滤后交错排列导致漏检
            max_pages = 2

            for page in range(1, max_pages + 1):
                # raw_count = API pre-filter count; videos = keyword-filtered
                raw_count, videos = await get_up_videos(sub.uid, pn=page, ps=5, keywords=sub.keyword)
                if raw_count == 0:
                    break

                for video in videos:
                    bvid = video['bvid']
                    title = video['title']

                    # 已下载/已放弃的视频只跳过，不中断翻页
                    if await is_bvid_downloaded(bvid):
                        continue

                    logger.info(f"[WBI API] New video for {sub.uid}: {title} ({bvid})")
                    await _upsert_new_video(sub.uid, bvid, title)
                    await process_auto_download(bot, sub.chat_id, sub.uid, bvid, title, sub.up_name)
                    await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Error checking sub {sub.uid}: {e}")

        # P1: throttle between subscriptions to avoid burst requests
        await asyncio.sleep(2)


def _sort_downloaded_files(files):
    """按文件类型排序：视频 > 音频 > 其他，确保发送顺序可控。"""
    def _key(f):
        ext = f.suffix.lower()
        if ext in VIDEO_EXT:
            return 0
        if ext in AUDIO_EXT:
            return 1
        return 2
    return sorted(files, key=_key)


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

    if result.return_code == 0:
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
            await msg.edit_text("Download succeeded but file not found.")

    # 失败路径：增加重试计数
    await retry_and_cleanup(bot, chat_id, uid, bvid, title, dl_dir, is_timeout=False)


async def retry_and_cleanup(bot, chat_id, uid, bvid, title, dl_dir, is_timeout: bool):
    """统一的重试计数 + 清理逻辑。"""
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
    elif is_timeout:
        await bot.send_message(chat_id, f"⏰ 视频 **{title}** 下载超时，将于下次轮询重试。（{retry_count}/{MAX_RETRY}）")
    # 清理
    shutil.rmtree(dl_dir, ignore_errors=True)
