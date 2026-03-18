import httpx
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Referer": "https://space.bilibili.com/"
}

async def get_up_info(uid: str) -> dict:
    """
    Fetch UP name based on UID.
    Returns {"name": str} or None if failed.
    """
    url = f"https://api.bilibili.com/x/space/wbi/acc/info?mid={uid}"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url)
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
    """
    Fetch paginated videos for a UP.
    Keywords is a comma-separated string, if provided, filters the returned list locally.
    Returns list of dicts: {"bvid": str, "title": str}
    """
    url = f"https://api.bilibili.com/x/space/wbi/arc/search?mid={uid}&ps={ps}&pn={pn}"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url)
            data = resp.json()
            if data.get("code") == 0:
                vlist = data["data"]["list"]["vlist"]
                results = []
                
                # Parse multiple keywords (split by comma or wide comma)
                filter_keys = []
                if keywords:
                    filter_keys = [k.strip().lower() for k in keywords.replace('，', ',').split(',') if k.strip()]
                
                for v in vlist:
                    title = v.get("title", "")
                    # Multiple Keyword Logic (If any of the keywords match or if there are no keywords)
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
