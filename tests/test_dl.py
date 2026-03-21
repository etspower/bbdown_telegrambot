"""
测试 BBDown 下载流程。

注意: 此文件包含硬编码的 Windows 路径，仅用于本地开发测试。
运行前请根据你的环境修改路径。

运行: python -m tests.test_dl
"""
import asyncio
import os

async def test_dl():
    # TODO: 根据你的环境修改这些路径
    bbdown_path = "BBDown"  # 或 "C:/bbdown/BBDown.exe"
    video_url = "https://www.bilibili.com/video/BV11FckzjEse/"
    work_dir = "data"
    
    cmd = [bbdown_path, video_url, "--audio-only", "--work-dir", work_dir, "-p", "1"]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=os.getcwd()
    )
    
    buffer = bytearray()
    while True:
        chunk = await process.stdout.read(1024)
        if not chunk:
            break
        print(f"GOT CHUNK (len={len(chunk)}): {repr(chunk)}")
        buffer.extend(chunk)
        
    await process.wait()
    print(f"Exit code: {process.returncode}")

if __name__ == "__main__":
    asyncio.run(test_dl())
