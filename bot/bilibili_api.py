import httpx
import logging
import time
import urllib.parse
import os
import re
from functools import reduce
from hashlib import md5

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    "Referer": "https://space.bilibili.com/"
}

def get_auth_cookies():
    # BBDown saves login credentials in DATA_DIR/BBDown.data.
    # By picking up SESSDATA from there, our API requests simulate the logged-in user, bypassing Bilibili's strict Risk Control (403/352).
    cookies = {"buvid3": "xyj114514"}
    try:
        from config import DATA_DIR
        data_file = os.path.join(DATA_DIR, "BBDown.data")
        if os.path.exists(data_file):
            with open(data_file, "rb") as f:
                content = f.read().decode('utf-8', errors='ignore')
                match = re.search(r"SESSDATA=([^;&]+)", content)
                if match:
                    cookies["SESSDATA"] = match.group(1)
    except Exception as e:
        logger.warning(f"Failed to parse BBDown.data cookies: {e}")
    return cookies

mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

def getMixinKey(orig: str):
    """Generate mixin key from img_key and sub_key."""
    return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]

def encWbi(params: dict, img_key: str, sub_key: str):
    mixin_key = getMixinKey(img_key + sub_key)
    curr_time = round(time.time())
    params['wts'] = curr_time
    params = dict(sorted(params.items()))
    params = {
        k : ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    wbi_sign = md5((query + mixin_key).encode()).hexdigest()
    params['w_rid'] = wbi_sign
    return params

async def get_wbi_keys(client: httpx.AsyncClient) -> tuple[str, str]:
    resp = await client.get('https://api.bilibili.com/x/web-interface/nav')
    resp.raise_for_status()
    json_content = resp.json()
    img_url = json_content['data']['wbi_img']['img_url']
    sub_url = json_content['data']['wbi_img']['sub_url']
    img_key = img_url.rsplit('/', 1)[1].split('.')[0]
    sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
    return img_key, sub_key

async def get_up_info(uid: str) -> dict:
    try:
        async with httpx.AsyncClient(headers=HEADERS, cookies=get_auth_cookies(), timeout=10.0) as client:
            img_key, sub_key = await get_wbi_keys(client)
            params = encWbi({"mid": uid}, img_key, sub_key)
            resp = await client.get("https://api.bilibili.com/x/space/wbi/acc/info", params=params)
            data = resp.json()
            if data.get("code") == 0:
                name = data["data"].get("name", "Unknown UP")
                return {"name": name}
            else:
                logger.error(f"Failed to get UP info for {uid}: {data.get('message')}")
    except Exception as e:
        logger.error(f"Exception fetching UP info {uid}: {e}")
    return None

async def get_up_videos(uid: str, pn: int = 1, ps: int = 10, keywords: str = None) -> list:
    try:
        async with httpx.AsyncClient(headers=HEADERS, cookies=get_auth_cookies(), timeout=10.0) as client:
            img_key, sub_key = await get_wbi_keys(client)
            params = encWbi({"mid": uid, "ps": ps, "pn": pn}, img_key, sub_key)
            resp = await client.get("https://api.bilibili.com/x/space/wbi/arc/search", params=params)
            data = resp.json()
            if data.get("code") == 0:
                vlist = data["data"]["list"]["vlist"]
                results = []
                
                filter_keys = []
                if keywords:
                    filter_keys = [k.strip().lower() for k in keywords.replace('，', ',').split(',') if k.strip()]
                
                for v in vlist:
                    title = v.get("title", "")
                    is_match = True
                    if filter_keys:
                        is_match = any(k in title.lower() for k in filter_keys)
                    
                    if is_match:
                        results.append({
                            "bvid": v.get("bvid"),
                            "title": title
                        })
                return results
            else:
                logger.error(f"Failed to fetch videos for {uid}: {data.get('message')}")
    except Exception as e:
        logger.error(f"Exception fetching videos for {uid}: {e}")
    return []
