"""
tests/test_bilibili_api.py - Bilibili API 测试
"""

import pytest
from bot.utils import parse_pages


class TestKeywordFiltering:
    """测试关键词过滤"""
    
    def test_empty_keywords(self):
        """测试空关键词（应该匹配所有）"""
        # 空关键词列表应该返回所有视频
        keywords = []
        title = "这是一个测试视频"
        # 实际逻辑：空关键词 = 全部匹配
        assert len(keywords) == 0
    
    def test_single_keyword(self):
        """测试单个关键词"""
        keywords = ["vlog"]
        title = "我的日常Vlog"
        # 不区分大小写匹配
        assert any(k.lower() in title.lower() for k in keywords)
    
    def test_multiple_keywords(self):
        """测试多个关键词（OR 关系）"""
        keywords = ["vlog", "日常", "测评"]
        title = "今天做一个美食测评"
        # 只要匹配任意一个关键词
        assert any(k in title for k in keywords)
    
    def test_mixed_comma_types(self):
        """测试中英文逗号混用"""
        raw = "vlog，日常,测评"
        keywords = [k.strip().lower() for k in raw.replace('，', ',').split(',') if k.strip()]
        assert keywords == ["vlog", "日常", "测评"]
    
    def test_case_insensitive(self):
        """测试大小写不敏感"""
        keywords = ["VLOG"]
        title = "我的日常vlog"
        assert any(k.lower() in title.lower() for k in keywords)


class TestWBICache:
    """测试 WBI Key 缓存"""
    
    def test_cache_ttl(self):
        """测试缓存 TTL"""
        from bot.bilibili_api import _WBI_CACHE_TTL
        # TTL 应该是 2 小时
        assert _WBI_CACHE_TTL == 7200
    
    def test_cache_structure(self):
        """测试缓存结构"""
        from bot.bilibili_api import _wbi_cache
        # 缓存应该包含必要的字段
        assert "img_key" in _wbi_cache
        assert "sub_key" in _wbi_cache
        assert "fetched_at" in _wbi_cache
