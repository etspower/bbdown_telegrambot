"""
bot/utils.py - 公共工具函数

包含：
- 文件排序
- 分P解析
- BVID 提取
- 进度条生成
"""

import re
from pathlib import Path
from typing import Optional

from bot.config import VIDEO_EXT, AUDIO_EXT


# BVID/AV 提取正则
_BVID_RE = re.compile(r"/video/((?:BV[\w]+|av\d+))")


def sort_downloaded_files(files):
    """
    按文件类型排序：视频 > 音频 > 其他，确保发送顺序可控。
    
    Args:
        files: Path 对象列表
    
    Returns:
        排序后的 Path 列表
    """
    def _key(f):
        ext = f.suffix.lower()
        if ext in VIDEO_EXT:
            return (0, f.name)
        if ext in AUDIO_EXT:
            return (1, f.name)
        return (2, f.name)
    return sorted(files, key=_key)


def parse_pages(text: str, total_pages: int) -> list[int]:
    """
    解析用户输入的分P范围。
    
    支持格式：
    - "1-3,5,7" → [1, 2, 3, 5, 7]
    - "1,3,5" → [1, 3, 5]
    - "1-5" → [1, 2, 3, 4, 5]
    
    Args:
        text: 用户输入的字符串
        total_pages: 视频总P数（用于边界限制）
    
    Returns:
        排序后的页码列表
    
    Raises:
        ValueError: 解析失败时抛出
    """
    text = text.replace(" ", "").replace("，", ",")
    pages = set()
    
    if not text:
        raise ValueError("Empty input")
    
    for part in text.split(","):
        if not part:
            continue
        if "-" in part:
            try:
                start, end = map(int, part.split("-", 1))
                start = max(1, start)
                end = min(total_pages, end)
                if start <= end:
                    pages.update(range(start, end + 1))
            except ValueError:
                raise ValueError(f"Invalid range: {part}")
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
            except ValueError:
                raise ValueError(f"Invalid page number: {part}")
    
    if not pages:
        raise ValueError("No valid pages found")
    
    return sorted(pages)


def extract_bvid(url: str) -> Optional[str]:
    """
    从 Bilibili URL 中提取 BVID 或 AVID。
    
    Args:
        url: Bilibili 视频 URL
    
    Returns:
        BVID/AVID 字符串，或 None
    """
    m = _BVID_RE.search(url)
    return m.group(1) if m else None


def create_progress_bar(percentage: float, length: int = 15) -> str:
    """
    创建文本进度条。
    
    Args:
        percentage: 百分比 (0-100)
        length: 进度条长度
    
    Returns:
        进度条字符串
    """
    filled = int(length * percentage / 100)
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"


def escape_markdown(text: str) -> str:
    """
    转义 Telegram MarkdownV1 保留字符，防止视频标题破坏消息格式。
    转义：\ _ * ` [ ] ( )（覆盖 V1 全部特殊字符）
    """
    for ch in ('\\', '_', '*', '`', '[', ']', '(', ')'):
        text = text.replace(ch, '\\' + ch)
    return text


def format_duration(seconds: int) -> str:
    """
    将秒数格式化为可读的时间字符串。
    
    Args:
        seconds: 秒数
    
    Returns:
        格式化后的字符串，如 "1h 23m 45s"
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)
