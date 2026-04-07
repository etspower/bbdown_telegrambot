"""
subprocess_executor.py - 统一的子进程执行器，封装超时、进度解析、异常处理。

所有 BBDown 调用都应通过此模块执行，确保：
  1. 统一的超时控制
  2. 统一的输出解析（进度条、日志）
  3. 防止僵尸进程
  4. 可选的进度回调
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, AsyncGenerator, Tuple, List
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认超时配置
DEFAULT_DOWNLOAD_TIMEOUT = 3600  # 1 小时（下载大文件）
DEFAULT_INFO_TIMEOUT = 60        # 1 分钟（仅获取信息）
DEFAULT_SCAN_TIMEOUT = 600       # 10 分钟（扫描 UP 主全部视频）

# 进度百分比正则 - 支持多种格式
# BBDown 格式: "下载中... 45.5%" 或 "45.5%" 或 "[45.5%]" 或 "Downloading... 45.5%"
# ffmpeg 格式: "frame= 123 fps=30 q=28.0 size= 12345kB time=00:01:23.45 bitrate=1234.5kbits/s speed=1.23x"
PROGRESS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# 文件大小和速度格式: "12.34 MB" 或 "1.23 GB" 或 "1.23 MB/s"
SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(MB|GB|KB)(?:/s)?", re.IGNORECASE)
# 时间格式: "00:01:23" 或 "1:23"
TIME_PATTERN = re.compile(r"(\d+):(\d{2})(?::(\d{2}))?")


@dataclass
class ProcessResult:
    """子进程执行结果"""
    return_code: int
    output: str
    timed_out: bool = False
    error: Optional[str] = None


@dataclass
class ProgressUpdate:
    """进度更新事件"""
    percentage: float
    line: str
    size: Optional[str] = None  # 如 "12.34 MB"
    speed: Optional[str] = None  # 如 "1.23 MB/s"


class SubprocessExecutor:
    """
    统一的子进程执行器。
    
    用法示例:
        executor = SubprocessExecutor(timeout=3600)
        async for progress in executor.run_with_progress(cmd, cwd):
            print(f"进度: {progress.percentage}%")
        result = await executor.wait()
    """
    
    def __init__(
        self,
        timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
        read_timeout: int = 30,
    ):
        self.timeout = timeout
        self.read_timeout = read_timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._output_lines: List[str] = []
        self._timed_out: bool = False
        self._start_time: float = 0.0  # 记录整体开始时间，避免超时重复计时
    
    async def run_with_progress(
        self,
        cmd: List[str],
        cwd: str,
    ) -> AsyncGenerator[ProgressUpdate, None]:
        """
        执行命令并 yield 进度更新。
        
        用法:
            executor = SubprocessExecutor(timeout=3600)
            async for progress in executor.run_with_progress(cmd, cwd):
                await update_ui(progress.percentage)
            result = await executor.get_result()
        """
        self._output_lines = []
        self._timed_out = False
        self._start_time = asyncio.get_running_loop().time()  # 记录开始时间
        
        try:
            # Create subprocess in a new process group / session
            # so we can kill the entire tree (BBDown + ffmpeg children)
            if sys.platform == 'win32':
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
                )
            else:
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    start_new_session=True,
                )
        except FileNotFoundError as e:
            # 可执行文件不存在
            logger.error(f"❌ 可执行文件未找到: {cmd[0]} - {e}")
            raise  # 向上层抛出，让调用方处理
        except Exception as e:
            logger.error(f"Failed to create subprocess: {e}")
            return
        
        buffer = bytearray()
        
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(1024),
                    timeout=self.read_timeout
                )
            except asyncio.TimeoutError:
                # 读超时，检查进程是否还在运行
                if self._process.returncode is not None:
                    break
                continue
            
            if not chunk:
                break
            
            buffer.extend(chunk)
            
            # 解析完整的行
            while b'\r' in buffer or b'\n' in buffer:
                # 找到第一个分隔符
                idx = self._find_line_end(buffer)
                if idx == -1:
                    break
                
                line_bytes = buffer[:idx]
                # 兼容 \r\n：如果下一个字节是 \n，一并跳过
                skip = 2 if (idx + 1 < len(buffer) and buffer[idx] == ord('\r') and buffer[idx + 1] == ord('\n')) else 1
                del buffer[:idx + skip]
                
                if not line_bytes:
                    continue
                
                try:
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    line = line_bytes.decode('gbk', errors='ignore').strip()
                
                self._output_lines.append(line)
                
                # 调试：记录所有 BBDown 输出（帮助诊断画质选择问题）
                if any(keyword in line.lower() for keyword in ["清晰度", "dfn", "quality", "resolution", "分辨率", "选择", "select"]):
                    logger.info(f"📺 BBDown 画质信息: {line}")
                
                # 检查进度 - 即使没有百分比也 yield 进度更新（用于显示文件大小等）
                match = PROGRESS_PATTERN.search(line)
                size_match = SIZE_PATTERN.search(line)
                
                if match or size_match:
                    try:
                        percentage = float(match.group(1)) if match else 0.0
                        size = None
                        speed = None
                        
                        # 解析文件大小和速度
                        for m in SIZE_PATTERN.finditer(line):
                            val = float(m.group(1))
                            unit = m.group(2).upper()
                            # 检查是否是速度（带 /s）
                            remaining = line[m.end():m.end()+3] if m.end() < len(line) else ""
                            if "/s" in remaining or "MB/s" in line or "GB/s" in line or "KB/s" in line:
                                speed = f"{val:.2f} {unit}/s"
                            else:
                                size = f"{val:.2f} {unit}"
                        
                        # 调试日志：记录解析到的进度信息
                        if percentage > 0 or size or speed:
                            logger.debug(f"Progress: {percentage:.1f}% | size={size} | speed={speed} | line={line[:80]}")
                        
                        yield ProgressUpdate(
                            percentage=percentage,
                            line=line,
                            size=size,
                            speed=speed
                        )
                    except ValueError:
                        pass
    
    def _find_line_end(self, buffer: bytearray) -> int:
        """找到行结束符位置，兼容 \\n、\\r、\\r\\n"""
        idx_r = buffer.find(b'\r')
        idx_n = buffer.find(b'\n')

        if idx_r != -1 and idx_n != -1:
            # \r\n 连续时，截到 \r，外部 del buffer[:idx+2] 跳过两个字节
            if idx_n == idx_r + 1:
                return idx_r  # 调用方需处理 \r\n 的两字节删除
            return min(idx_r, idx_n)
        elif idx_r != -1:
            return idx_r
        elif idx_n != -1:
            return idx_n
        return -1
    
    async def wait(self) -> ProcessResult:
        """等待进程结束并返回结果，超时基于整体运行时间计算。"""
        if self._process is None:
            return ProcessResult(return_code=-1, output="", error="Process not started")
        
        # 防御：如果 run_with_progress 未被调用（_start_time 仍为 0），使用完整超时
        if self._start_time == 0.0:
            remaining = float(self.timeout)
        else:
            elapsed = asyncio.get_running_loop().time() - self._start_time
            remaining = max(1.0, self.timeout - elapsed)  # 至少保留 1 秒
        
        try:
            await asyncio.wait_for(self._process.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning(f"Process timeout after {self.timeout}s, killing...")
            await self.kill()
            self._timed_out = True
        
        return ProcessResult(
            return_code=self._process.returncode or -1,
            output="\n".join(self._output_lines),
            timed_out=self._timed_out,
        )
    
    async def kill(self):
        """Kill the entire process tree (BBDown + child ffmpeg processes)."""
        if self._process and self._process.returncode is None:
            try:
                if sys.platform == 'win32':
                    # Windows: taskkill /T kills the entire process tree
                    os.system(f'taskkill /F /T /PID {self._process.pid} >nul 2>&1')
                else:
                    # Unix: kill the entire process group
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                # Process already exited or pgid inaccessible
                pass
            try:
                await self._process.wait()
            except Exception:
                pass


async def run_bbdown(
    args: List[str],
    cwd: str,
    bbdown_path: str = None,
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
    progress_callback: Optional[Callable[[ProgressUpdate], Awaitable[None]]] = None,
) -> ProcessResult:
    """
    执行 BBDown 命令的便捷封装。
    
    Args:
        args: BBDown 参数列表（不含 bbdown 可执行文件本身）
        cwd: 工作目录
        bbdown_path: BBDown 可执行文件路径（如果为 None，从 config 导入）
        timeout: 总超时时间（秒）
        progress_callback: 可选的进度回调函数
    
    Returns:
        ProcessResult 对象
    """
    if bbdown_path is None:
        from bot.config import BBDOWN_PATH
        bbdown_path = BBDOWN_PATH
    
    cmd = [bbdown_path] + args
    
    executor = SubprocessExecutor(timeout=timeout)
    
    try:
        async for progress in executor.run_with_progress(cmd, cwd):
            if progress_callback:
                try:
                    await progress_callback(progress)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")
    except Exception as e:
        logger.error(f"BBDown execution error: {e}")
        return ProcessResult(return_code=-1, output="", error=str(e))
    
    return await executor.wait()


async def run_bbdown_simple(args: list[str], cwd: str, timeout: int = DEFAULT_INFO_TIMEOUT) -> ProcessResult:
    """
    执行 BBDown 命令（无进度回调），适用于快速查询操作。
    """
    return await run_bbdown(args, cwd, timeout=timeout, progress_callback=None)


def create_progress_bar(percentage: float, length: int = 15) -> str:
    """创建文本进度条"""
    filled = int(length * percentage / 100)
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"


class ThrottledMessageUpdater:
    """Throttled Telegram message updater — deduplicates edits by time and content.

    Usage:
        updater = ThrottledMessageUpdater(message, interval=3.0)
        await updater.update("Downloading...", force=True)
    """

    def __init__(self, message, interval: float = 3.0):
        self._msg = message
        self._interval = interval
        self._last: float = 0.0
        self._last_text: str = ""

    async def update(self, text: str, *, force: bool = False, parse_mode: str = "Markdown"):
        now = asyncio.get_running_loop().time()
        if force or (now - self._last) >= self._interval:
            if text != self._last_text:
                try:
                    await self._msg.edit_text(text, parse_mode=parse_mode)
                    self._last_text = text
                    self._last = now
                except Exception:
                    pass
