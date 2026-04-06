"""
tests/test_handlers.py - Handler 功能测试
"""

import pytest
from bot.utils import parse_pages, extract_bvid


class TestParsePagesIntegration:
    """集成测试：分P解析"""
    
    def test_complex_input(self):
        """测试复杂输入"""
        # 混合中英文逗号、空格、范围
        result = parse_pages("1-3，5, 7-9", 20)
        assert result == [1, 2, 3, 5, 7, 8, 9]
    
    def test_out_of_range(self):
        """测试超出范围"""
        # 视频只有 5P，但用户输入 1-10
        result = parse_pages("1-10", 5)
        assert result == [1, 2, 3, 4, 5]
    
    def test_negative_start(self):
        """测试负数开始"""
        result = parse_pages("-5-3", 10)
        assert result == [1, 2, 3]
    
    def test_reversed_range(self):
        """测试反向范围（应该被忽略）"""
        # 5-1 是无效的，因为 start > end
        with pytest.raises(ValueError):
            parse_pages("5-1", 10)


class TestURLRecognition:
    """测试 URL 识别"""
    
    def test_full_url(self):
        """测试完整 URL"""
        url = "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.1007"
        assert extract_bvid(url) == "BV1xx411c7mD"
    
    def test_mobile_url(self):
        """测试移动端 URL"""
        url = "https://m.bilibili.com/video/BV1xx411c7mD"
        assert extract_bvid(url) == "BV1xx411c7mD"
    
    def test_url_with_params(self):
        """测试带参数的 URL"""
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=123"
        assert extract_bvid(url) == "BV1xx411c7mD"


class TestVideoInfoEdgeCases:
    """测试视频信息解析边界情况"""
    
    def test_empty_title_handling(self):
        """测试空标题处理"""
        # 模拟 get_video_info 返回 None 的情况
        # 实际测试需要 mock BBDown 调用
        pass  # 需要集成测试环境
    
    def test_single_page_video(self):
        """测试单P视频"""
        # 单P视频应该返回 total_pages=1
        pass  # 需要集成测试环境
    
    def test_multi_page_video(self):
        """测试多P视频"""
        # 多P视频应该正确解析分P信息
        pass  # 需要集成测试环境
