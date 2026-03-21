"""
测试 Bilibili WBI API 响应格式。

运行: python -m tests.test_api
"""
import asyncio
import httpx

async def test():
    sh = httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    resp = await sh.get("https://api.bilibili.com/x/space/wbi/arc/search?mid=41368&ps=10&pn=1")
    print(resp.json())

if __name__ == "__main__":
    asyncio.run(test())
