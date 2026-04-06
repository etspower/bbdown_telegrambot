"""
tests/test_subprocess_executor.py - 子进程执行器测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.subprocess_executor import (
    SubprocessExecutor,
    ProcessResult,
    ProgressUpdate,
    create_progress_bar,
    DEFAULT_DOWNLOAD_TIMEOUT,
    DEFAULT_INFO_TIMEOUT,
)


class TestProcessResult:
    """测试 ProcessResult 数据类"""
    
    def test_basic_creation(self):
        """测试基本创建"""
        result = ProcessResult(return_code=0, output="success")
        assert result.return_code == 0
        assert result.output == "success"
        assert result.timed_out is False
        assert result.error is None
    
    def test_with_timeout(self):
        """测试超时情况"""
        result = ProcessResult(return_code=-1, output="", timed_out=True)
        assert result.timed_out is True
    
    def test_with_error(self):
        """测试错误情况"""
        result = ProcessResult(return_code=1, output="", error="Command failed")
        assert result.error == "Command failed"


class TestProgressUpdate:
    """测试 ProgressUpdate 数据类"""
    
    def test_creation(self):
        """测试创建"""
        progress = ProgressUpdate(percentage=50.5, line="50% completed")
        assert progress.percentage == 50.5
        assert progress.line == "50% completed"


class TestCreateProgressBar:
    """测试进度条生成"""
    
    def test_default_length(self):
        """测试默认长度"""
        bar = create_progress_bar(50)
        # 默认长度 15
        assert len(bar) > 15
    
    def test_custom_length(self):
        """测试自定义长度"""
        bar = create_progress_bar(50, length=10)
        # 进度条应该包含填充字符和百分比
        assert "█" in bar or "░" in bar
        assert "50.0%" in bar


class TestSubprocessExecutor:
    """测试 SubprocessExecutor"""
    
    def test_init_default_timeout(self):
        """测试默认超时"""
        executor = SubprocessExecutor()
        assert executor.timeout == DEFAULT_DOWNLOAD_TIMEOUT
    
    def test_init_custom_timeout(self):
        """测试自定义超时"""
        executor = SubprocessExecutor(timeout=600)
        assert executor.timeout == 600
    
    def test_init_with_read_timeout(self):
        """测试读取超时"""
        executor = SubprocessExecutor(timeout=3600, read_timeout=60)
        assert executor.read_timeout == 60


class TestTimeoutConstants:
    """测试超时常量"""
    
    def test_download_timeout(self):
        """测试下载超时"""
        # 应该是 1 小时
        assert DEFAULT_DOWNLOAD_TIMEOUT == 3600
    
    def test_info_timeout(self):
        """测试信息获取超时"""
        # 应该是 1 分钟
        assert DEFAULT_INFO_TIMEOUT == 60


# 注意：以下测试需要实际运行子进程，属于集成测试
@pytest.mark.skip(reason="需要实际子进程环境")
class TestSubprocessExecutorIntegration:
    """子进程执行器集成测试"""
    
    @pytest.mark.asyncio
    async def test_run_simple_command(self):
        """测试运行简单命令"""
        executor = SubprocessExecutor(timeout=10)
        # 使用 echo 命令测试
        async for progress in executor.run_with_progress(["echo", "hello"], "."):
            pass
        result = await executor.wait()
        assert result.return_code == 0
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """测试超时处理"""
        executor = SubprocessExecutor(timeout=1)
        # 使用 sleep 命令测试超时
        async for _ in executor.run_with_progress(["sleep", "10"], "."):
            pass
        result = await executor.wait()
        assert result.timed_out is True
    
    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """测试文件不存在"""
        executor = SubprocessExecutor()
        with pytest.raises(FileNotFoundError):
            async for _ in executor.run_with_progress(["nonexistent_command"], "."):
                pass
