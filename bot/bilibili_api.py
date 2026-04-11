import httpx
import logging
import time
import urllib.parse
import os
import re
import uuid
import asyncio
from functools import reduce
from hashlib import md5
from typing import Tuple

from bot.config import DATA_DIR

logger = logging.getLogger(__name__)

# 请求头：不声明 Accept-Encoding，避免服务器返回 gzip 压缩体。
# httpx 默认不自动解压，若收到 gzip 响应直接调用 resp.json() 会报
# 'utf-8 codec can't decode byte 0xa4' (gzip magic bytes)。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://space.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://space.bilibili.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# --- WBI 密钥内存缓存 ---
_wbi_cache: dict = {
    "img_key": None,
    "sub_key": None,
    "fetched_at": 0.0,
}
_WBI_CACHE_TTL = 7200  # 2 小时（秒）
_wbi_lock = asyncio.Lock()

# --- BBDown.data Cookie 内存缓存 ---
_cookie_cache: dict = {
    "cookies": None,
    "file_mtime": 0.0,
}
_cookie_lock = asyncio.Lock()

# 持久化 buvid3
_buvid3_cache: str | None = None
_BUVID3_FILE = os.path.join(DATA_DIR, ".buvid3")


def _load_buvid3() -> str:
    """加载或生成持久化的 buvid3（UUID 格式）。"""
    global _buvid3_cache
    if _buvid3_cache:
        return _buvid3_cache
    try:
        if os.path.exists(_BUVID3_FILE):
            with open(_BUVID3_FILE, "r") as f:
                _buvid3_cache = f.read().strip()
                if _buvid3_cache:
                    return _buvid3_cache
    except Exception:
        pass
    _buvid3_cache = str(uuid.uuid4()).upper()
    try:
        with open(_BUVID3_FILE, "w") as f:
            f.write(_buvid3_cache)
    except Exception:
        pass
    return _buvid3_cache


def _load_cookies_from_disk() -> dict:
    """从磁盘同步读取 BBDown.data，返回 cookies dict。"""
    buvid3 = _load_buvid3()
    cookies = {
        "buvid3": buvid3,
        "b_nut": str(int(time.time())),
        "CURRENT_FNVAL": "4048",
        "buvid4": buvid3 + "-" + str(int(time.time())) + "-" + str(uuid.uuid4())[:8].upper(),
        "buvid_fp": buvid3,
        "b_lsid": str(uuid.uuid4())[:8].upper() + "_" + str(int(time.time())),
    }
    try:
        data_file = os.path.join(DATA_DIR, "BBDown.data")
        if os.path.exists(data_file):
            with open(data_file, "rb") as f:
                content = f.read().decode("utf-8", errors="ignore")
            match = re.search(r"SESSDATA=([^;&]+)", content)
            if match:
                cookies["SESSDATA"] = match.group(1)
                logger.info("BBDown.data loaded: SESSDATA found")
            else:
                logger.info("BBDown.data loaded: no SESSDATA (not logged in yet)")
    except Exception as e:
        logger.warning(f"Failed to parse BBDown.data cookies: {e}")
    return cookies


async def get_auth_cookies() -> dict:
    """获取认证 cookies，优先从内存缓存读取。

    仅当 BBDown.data 文件 mtime 变化时（即重新登录后）才刷新，
    其余情况直接返回缓存，不阻塞事件循环。
    """
    global _cookie_cache

    async with _cookie_lock:
        try:
            data_file = os.path.join(DATA_DIR, "BBDown.data")
            current_mtime = os.path.getmtime(data_file) if os.path.exists(data_file) else 0.0
        except Exception:
            current_mtime = 0.0

        if _cookie_cache["cookies"] is not None and current_mtime == _cookie_cache["file_mtime"]:
            return _cookie_cache["cookies"]

        logger.info("BBDown.data changed or first load, refreshing cookie cache...")
        cookies = _load_cookies_from_disk()
        _cookie_cache["cookies"] = cookies
        _cookie_cache["file_mtime"] = current_mtime
        return cookies


mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]


def getMixinKey(orig: str):
    return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, "")[:32]


def encWbi(params: dict, img_key: str, sub_key: str):
    mixin_key = getMixinKey(img_key + sub_key)
    curr_time = round(time.time())
    params["wts"] = curr_time
    params = dict(sorted(params.items()))
    params = {
        k: "".join(filter(lambda chr: chr not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    wbi_sign = md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = wbi_sign
    return params


async def _get_wbi_keys() -> Tuple[str, str]:
    """获取 WBI keys，带 TTL 缓存。自行管理内部 client，调用方无需传入 client。"""
    global _wbi_cache

    async with _wbi_lock:
        now = time.time()
        if (
            _wbi_cache["img_key"]
            and _wbi_cache["sub_key"]
            and (now - _wbi_cache["fetched_at"]) < _WBI_CACHE_TTL
        ):
            logger.debug("WBI keys cache hit")
            return _wbi_cache["img_key"], _wbi_cache["sub_key"]

        logger.info("WBI keys cache miss, fetching from /nav ...")
        cookies = await get_auth_cookies()
        async with httpx.AsyncClient(headers=HEADERS, cookies=cookies, timeout=10.0) as client:
            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
            resp.raise_for_status()
            json_content = resp.json()

        img_url = json_content["data"]["wbi_img"]["img_url"]
        sub_url = json_content["data"]["wbi_img"]["sub_url"]
        img_key = img_url.rsplit("/", 1)[1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]

        _wbi_cache["img_key"] = img_key
        _wbi_cache["sub_key"] = sub_key
        _wbi_cache["fetched_at"] = time.time()
        logger.info(f"WBI keys refreshed, next refresh in {_WBI_CACHE_TTL // 60} min")
        return img_key, sub_key


async def get_up_info(uid: str) -> dict:
    """获取 UP 主基本信息（名称等）。"""
    try:
        img_key, sub_key = await _get_wbi_keys()
        cookies = await get_auth_cookies()
        params = encWbi({"mid": uid}, img_key, sub_key)
        async with httpx.AsyncClient(headers=HEADERS, cookies=cookies, timeout=10.0) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/space/wbi/acc/info", params=params
            )
            data = resp.json()
        if data.get("code") == 0:
            name = data["data"].get("name", "Unknown UP")
            return {"name": name}
        else:
            logger.error(
                f"Failed to get UP info for {uid}: code={data.get('code')} msg={data.get('message')}"
            )
    except Exception as e:
        logger.error(f"Exception fetching UP info {uid}: {e}")
    return None


async def get_up_videos(uid: str, pn: int = 1, ps: int = 10, keywords: str = None) -> tuple[int, list]:
    """获取 UP 主视频列表。

    Returns:
        (raw_count, filtered_list)
        - raw_count: API 返回的原始视频数量（过滤前），用于判断是否还有下一页。
        - filtered_list: 经关键词过滤后的视频列表。
    """
    try:
        img_key, sub_key = await _get_wbi_keys()
        cookies = await get_auth_cookies()
        params = encWbi({"mid": uid, "ps": ps, "pn": pn}, img_key, sub_key)
        async with httpx.AsyncClient(headers=HEADERS, cookies=cookies, timeout=10.0) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/space/wbi/arc/search", params=params
            )
            data = resp.json()
        if data.get("code") == 0:
            vlist = data["data"]["list"]["vlist"]
            raw_count = len(vlist)
            results = []

            filter_keys = []
            if keywords:
                filter_keys = [
                    k.strip().lower()
                    for k in keywords.replace("，", ",").split(",")
                    if k.strip()
                ]

            for v in vlist:
                title = v.get("title", "")
                is_match = True
                if filter_keys:
                    is_match = any(k in title.lower() for k in filter_keys)
                if is_match:
                    results.append({"bvid": v.get("bvid"), "title": title})
            return raw_count, results
        else:
            logger.error(
                f"Failed to fetch videos for {uid}: code={data.get('code')} msg={data.get('message')}"
            )
    except Exception as e:
        logger.error(f"Exception fetching videos for {uid}: {e}")
    return 0, []
