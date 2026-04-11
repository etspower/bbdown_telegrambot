"""
bot/rss_fetcher.py - 通过 RSSHub 获取 UP 主最新视频列表

替代 bilibili_api.get_up_videos()，绕过 B 站 WBI 风控。

RSSHub 路由：
  /bilibili/user/video/:uid          普通投稿列表
  /bilibili/user/video/:uid/0/1      同上，含字幕

官方实例：https://rsshub.app
自部署：  http://your-rsshub:1200

返回数据格式（与 get_up_videos 保持一致）:
  (raw_count: int, videos: list[dict])
  每个 dict: {"bvid": str, "title": str, "published": str | None}
"""

import re
import logging
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from bot.config import RSSHUB_BASE_URL

logger = logging.getLogger(__name__)

# RSS 条目中提取 BV 号的正则（匹配 BV1xxxxxxxxx 格式）
_BVID_RE = re.compile(r"(BV[a-zA-Z0-9]{10})")

# RSSHub 请求超时（秒）
_RSSHUB_TIMEOUT = 15.0

# RSSHub 请求头 - 模拟合法 RSS 阅读器请求
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


class RSSFetchError(Exception):
    """RSS 获取失败，附带用户可读的原因描述"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.user_message = message


def _extract_bvid(text: str) -> Optional[str]:
    """从文本（链接/描述）中提取 BV 号"""
    m = _BVID_RE.search(text)
    return m.group(1) if m else None


def _parse_rss_xml(xml_text: str, keywords: Optional[str] = None) -> tuple[int, list]:
    """
    解析 RSS XML，返回 (raw_count, filtered_videos)

    raw_count: 解析到的总条目数（关键词过滤前）
    filtered_videos: 经关键词过滤后的列表，每项 {"bvid", "title", "published"}
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RSSFetchError(f"RSS XML 解析失败：{e}")

    # 兼容 RSS 2.0 (<item>) 和 Atom (<entry>) 格式
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)

    raw_count = len(items)

    # 构建关键词列表（支持中英文逗号）
    filter_keys: list[str] = []
    if keywords:
        filter_keys = [
            k.strip().lower()
            for k in keywords.replace("，", ",").split(",")
            if k.strip()
        ]

    videos = []
    for item in items:
        # 提取标题
        title_el = item.find("title") or item.find("atom:title", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""

        # 提取 BV 号：优先从 <link>，其次从 <guid>，再从描述
        bvid = None
        for tag in ("link", "guid", "atom:link", "atom:id"):
            el = item.find(tag) if ":" not in tag else item.find(tag, ns)
            if el is not None:
                # <atom:link> 的链接在 href 属性里
                text_val = el.get("href") or el.text or ""
                bvid = _extract_bvid(text_val)
                if bvid:
                    break

        if not bvid:
            # 最后尝试从描述中提取
            for desc_tag in ("description", "atom:summary", "atom:content"):
                el = item.find(desc_tag) if ":" not in desc_tag else item.find(desc_tag, ns)
                if el is not None and el.text:
                    bvid = _extract_bvid(el.text)
                    if bvid:
                        break

        if not bvid:
            logger.warning(f"RSS item missing bvid, title='{title}', skipping")
            continue

        # 提取发布时间（可选）
        pub_el = item.find("pubDate") or item.find("atom:published", ns)
        published = (pub_el.text or "").strip() if pub_el is not None else None

        # 关键词过滤
        if filter_keys:
            if not any(k in title.lower() for k in filter_keys):
                continue

        videos.append({"bvid": bvid, "title": title, "published": published})

    return raw_count, videos


async def get_up_videos_rss(
    uid: str,
    keywords: Optional[str] = None,
    rsshub_base: Optional[str] = None,
) -> tuple[int, list]:
    """
    通过 RSSHub 获取 UP 主最新投稿列表。

    接口签名与 bilibili_api.get_up_videos() 保持兼容：
      返回 (raw_count, filtered_videos)

    Args:
        uid:         B 站 UP 主 UID（纯数字字符串）
        keywords:    关键词过滤字符串，逗号分隔，None 表示不过滤
        rsshub_base: RSSHub 实例地址，默认使用 config.RSSHUB_BASE_URL

    Raises:
        RSSFetchError: 含用户可读的失败原因
    """
    base = (rsshub_base or RSSHUB_BASE_URL).rstrip("/")
    url = f"{base}/bilibili/user/video/{uid}"

    logger.info(f"[RSSHub] Fetching UP {uid} from {url}")

    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=_RSSHUB_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        raise RSSFetchError(
            f"❌ RSSHub 请求超时（>{_RSSHUB_TIMEOUT:.0f}s）\n"
            f"请检查 RSSHub 实例地址是否可访问：`{base}`"
        )
    except httpx.ConnectError as e:
        raise RSSFetchError(
            f"❌ 无法连接到 RSSHub 实例：`{base}`\n"
            f"错误详情：{e}\n"
            f"请在 `.env` 中设置正确的 `RSSHUB_BASE_URL`。"
        )
    except httpx.RequestError as e:
        raise RSSFetchError(f"❌ RSSHub 网络请求失败：{e}")

    # HTTP 层错误处理
    if resp.status_code == 404:
        raise RSSFetchError(
            f"❌ RSSHub 返回 404：找不到 UID `{uid}` 的视频 RSS\n"
            f"请确认 UID 正确，且该 UP 主有公开投稿。"
        )
    if resp.status_code == 503:
        raise RSSFetchError(
            f"❌ RSSHub 服务暂时不可用（503）\n"
            f"可能是公共实例限速，建议自部署 RSSHub 或稍后重试。"
        )
    if resp.status_code >= 400:
        raise RSSFetchError(
            f"❌ RSSHub 返回异常状态码 {resp.status_code}\n"
            f"请求 URL：`{url}`",
            status_code=resp.status_code,
        )

    # 解析 XML
    raw_count, videos = _parse_rss_xml(resp.text, keywords=keywords)

    logger.info(
        f"[RSSHub] UP {uid}: raw={raw_count} items, "
        f"filtered={len(videos)} (keywords={keywords!r})"
    )
    return raw_count, videos
