import asyncio
import os
import time

async def test_dl():
    cmd = ["C:/bbdown/BBDown.exe", "https://www.bilibili.com/video/BV11FckzjEse/", "--audio-only", "--work-dir", "data", "-p", "1"]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="c:/Python/biliupdater"
    )
    
    buffer = bytearray()
    while True:
        chunk = await process.stdout.read(1024)
        if not chunk:
            break
        print(f"GOT CHUNK (len={len(chunk)}): {repr(chunk)}")
        buffer.extend(chunk)
        
    await process.wait()

asyncio.run(test_dl())
