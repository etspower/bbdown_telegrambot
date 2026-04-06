"""
tests/test_utils.py - 工具函数测试
"""

import pytest
from pathlib import Path

from bot.utils import (
    sort_downloaded_files,
    parse_pages,
    extract_bvid,
    create_progress_bar,
    format_duration,
)
from bot.config import VIDEO_EXT, AUDIO_EXT


class TestSortDownloadedFiles:
    """测试文件排序功能"""
    
    def test_sort_video_first(self, tmp_path):
        """视频文件应该排在最前面"""
        files = [
            tmp_path / "audio.mp3",
            tmp_path / "video.mp4",
            tmp_path / "doc.txt",
        ]
        sorted_files = sort_downloaded_files(files)
        assert sorted_files[0].name == "video.mp4"
        assert sorted_files[1].name == "audio.mp3"
        assert sorted_files[2].name == "doc.txt"
    
    def test_sort_audio_second(self, tmp_path):
        """音频文件应该排在第二位"""
        files = [
            tmp_path / "other.txt",
            tmp_path / "audio.m4a",
        ]
        sorted_files = sort_downloaded_files(files)
        assert sorted_files[0].name == "audio.m4a"
        assert sorted_files[1].name == "other.txt"
    
    def test_empty_list(self):
        """空列表应该返回空列表"""
        assert sort_downloaded_files([]) == []


class TestParsePages:
    """测试分P解析功能"""
    
    def test_single_page(self):
        """测试单个页码"""
        assert parse_pages("5", 10) == [5]
    
    def test_range(self):
        """测试范围"""
        assert parse_pages("1-3", 10) == [1, 2, 3]
    
    def test_mixed(self):
        """测试混合格式"""
        assert parse_pages("1-3,5,7", 10) == [1, 2, 3, 5, 7]
    
    def test_chinese_comma(self):
        """测试中文逗号"""
        assert parse_pages("1,3，5", 10) == [1, 3, 5]
    
    def test_with_spaces(self):
        """测试带空格"""
        assert parse_pages("1 - 3 , 5", 10) == [1, 2, 3, 5]
    
    def test_boundary_clamp(self):
        """测试边界限制"""
        assert parse_pages("1-100", 5) == [1, 2, 3, 4, 5]
    
    def test_empty_raises(self):
        """空输入应该抛出异常"""
        with pytest.raises(ValueError):
            parse_pages("", 10)
    
    def test_invalid_range_raises(self):
        """无效范围应该抛出异常"""
        with pytest.raises(ValueError):
            parse_pages("abc", 10)
    
    def test_no_valid_pages_raises(self):
        """没有有效页码应该抛出异常"""
        with pytest.raises(ValueError):
            parse_pages("100-200", 5)
    
    def test_deduplication(self):
        """测试去重"""
        assert parse_pages("1,1,2,2,3", 10) == [1, 2, 3]


class TestExtractBvid:
    """测试 BVID 提取功能"""
    
    def test_extract_bv(self):
        """测试提取 BV 号"""
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert extract_bvid(url) == "BV1xx411c7mD"
    
    def test_extract_av(self):
        """测试提取 AV 号"""
        url = "https://www.bilibili.com/video/av123456"
        assert extract_bvid(url) == "av123456"
    
    def test_short_url(self):
        """测试短链接"""
        url = "https://b23.tv/BV1xx411c7mD"
        assert extract_bvid(url) is None  # 短链需要额外处理
    
    def test_invalid_url(self):
        """测试无效 URL"""
        assert extract_bvid("https://example.com") is None


class TestCreateProgressBar:
    """测试进度条生成功能"""
    
    def test_zero_percent(self):
        """测试 0%"""
        bar = create_progress_bar(0, length=10)
        assert "0.0%" in bar
        assert bar.count("░") == 10
    
    def test_hundred_percent(self):
        """测试 100%"""
        bar = create_progress_bar(100, length=10)
        assert "100.0%" in bar
        assert bar.count("█") == 10
    
    def test_fifty_percent(self):
        """测试 50%"""
        bar = create_progress_bar(50, length=10)
        assert "50.0%" in bar
        assert bar.count("█") == 5
        assert bar.count("░") == 5


class TestFormatDuration:
    """测试时长格式化功能"""
    
    def test_seconds_only(self):
        """测试只有秒"""
        assert format_duration(45) == "45s"
    
    def test_minutes_and_seconds(self):
        """测试分钟和秒"""
        assert format_duration(125) == "2m 5s"
    
    def test_hours_minutes_seconds(self):
        """测试小时、分钟和秒"""
        assert format_duration(3665) == "1h 1m 5s"
    
    def test_zero(self):
        """测试 0 秒"""
        assert format_duration(0) == "0s"
