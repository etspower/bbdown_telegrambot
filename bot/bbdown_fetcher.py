"""
bbdown_fetcher.py
Wraps BBDown CLI calls for:
  1. Fetching all video URLs from a UP master's space page (-po -p ALL)
  2. Parsing a single video's metadata (--only-show-info)

所有 BBDown 调用都通过 subprocess_executor 统一执行。
"""

import asyncio
import logging
import re
from typing import Callable, Awaitable, Optional

from config import DATA_DIR
from database import (
    upsert_up_video_url,
    get_unparsed_videos,
    update_video_title,
)
from subprocess_executor import run_bbdown, run_bbdown_simple, DEFAULT_SCAN_TIMEOUT, DEFAULT_INFO_TIMEOUT

logger = logging.getLogger(__name__)

# ────────── BBDown 输出解析常量 ──────────
# 集中管理所有 BBDown 输出格式字符串，便于上游版本变更时一处修改
BBDOWN_TITLE_PREFIX = "视频标题:"
BBDOWN_PAGES_PATTERN = re.compile(r'(\d+)\s*个分P')
BBDOWN_PART_PATTERN = re.compile(r"-\s*P(\d+):\s*\[([^\]]+)\]\s*\[(.*)\]\s*\[([^\]]+)\]")

# Matches Bilibili video URLs in BBDown output
_VIDEO_URL_RE = re.compile(
    r"(https?://(?:www\.)?bilibili\.com/video/(?:av\d+|BV[\w]+))"
)
# Extracts BV/av id from a URL
_BVID_RE = re.compile(r"/video/((?:BV[\w]+|av\d+))")


def _extract_bvid(url: str) -> Optional[str]:
    m = _BVID_RE.search(url)
    return m.group(1) if m else None


async def fetch_all_video_urls(
    uid: str,
    status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> int:
    """
    Run BBDown to enumerate all video URLs on a UP master's space page.
    Inserts new BVIDs into up_videos table.

    Returns the count of newly inserted video URLs.
    """
    space_url = f"https://space.bilibili.com/{uid}"
    args = [
        "-po",           # print-only (no download)
        "-p", "ALL",     # all pages
        "--delay-per-page", "5",
        space_url,
    ]

    if status_callback:
        await status_callback(f"🔍 正在使用 BBDown 扫描 UID `{uid}` 的全部投稿视频，请耐心等候…")

    result = await run_bbdown_simple(args, DATA_DIR, timeout=DEFAULT_SCAN_TIMEOUT)

    urls_found = _VIDEO_URL_RE.findall(result.output)
    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for u in urls_found:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    new_count = 0
    for url in unique_urls:
        bvid = _extract_bvid(url)
        if bvid:
            inserted = await upsert_up_video_url(uid, bvid, url)
            if inserted:
                new_count += 1

    if status_callback:
        total = len(unique_urls)
        await status_callback(
            f"✅ 扫描完毕！共发现 **{total}** 个视频 URL，其中 **{new_count}** 个为新增。"
        )

    return new_count


async def parse_one_video(bvid: str, url: str) -> Optional[str]:
    """
    Run BBDown --only-show-info on a single video URL to extract its title.
    Returns the title string, or None on failure.
    """
    args = [url, "--only-show-info"]
    result = await run_bbdown_simple(args, DATA_DIR, timeout=DEFAULT_INFO_TIMEOUT)

    title = None
    for line in result.output.splitlines():
        if BBDOWN_TITLE_PREFIX in line:
            title = line.split(BBDOWN_TITLE_PREFIX, 1)[1].strip()
            break

    if not title:
        # 解析失败时记录原始输出，便于排查上游 BBDown 格式变更
        logger.debug(f"Failed to parse title for {bvid}. BBDown output:\n{result.output[:500]}")

    if title:
        await update_video_title(bvid, title)
    return title


async def parse_pending_videos(
    uid: str,
    status_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    batch_limit: int = 200,
) -> int:
    """
    Parse all unparsed videos for a given UID.
    Calls status_callback(parsed_so_far, total) periodically.
    Returns total successfully parsed count.
    """
    pending = await get_unparsed_videos(uid, limit=batch_limit)
    total = len(pending)
    if total == 0:
        return 0

    parsed_count = 0
    for i, video in enumerate(pending):
        try:
            title = await parse_one_video(video.bvid, video.url)
            if title:
                parsed_count += 1
        except Exception as e:
            # 单个视频解析异常不中断整批任务，记录后继续
            logger.warning(f"parse_one_video({video.bvid}) raised: {e}")

        # Call status every 5 videos or on the last one
        if status_callback and ((i + 1) % 5 == 0 or (i + 1) == total):
            await status_callback(i + 1, total)

        # Small delay between BBDown invocations to avoid rate limiting
        await asyncio.sleep(2)

    return parsed_count
