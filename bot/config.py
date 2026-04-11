import os
import logging
from pathlib import Path
from dotenv import load_dotenv
import shutil

logger = logging.getLogger(__name__)

# 调试：检查环境变量在 load_dotenv 之前的值（不打印 token 内容）
_pre_token = os.getenv("BOT_TOKEN", "")
logger.debug(f"BOT_TOKEN env var present: {bool(_pre_token)}")

# 尝试从多个位置加载 .env 文件
# 1. 当前工作目录
# 2. bot 模块的父目录（项目根目录）
_env_loaded = load_dotenv()
logger.debug(f"load_dotenv() from cwd returned: {_env_loaded}")

if not _env_loaded:
    # 尝试从项目根目录加载
    _project_root = Path(__file__).parent.parent
    _env_path = _project_root / ".env"
    logger.debug(f"Trying to load from: {_env_path}")
    _env_loaded = load_dotenv(_env_path, override=True)
    logger.debug(f"load_dotenv(_env_path) returned: {_env_loaded}")

# 强制重新加载，覆盖已存在的环境变量
_project_root = Path(__file__).parent.parent
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)
    logger.debug("Force reloaded .env with override=True")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip('"').strip("'")

logger.debug(f"BOT_TOKEN loaded: {bool(BOT_TOKEN)}")
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is empty!")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip('"').strip("'"))

# BBDown 路径解析：支持多种形式
# 1. 绝对路径: /usr/local/bin/BBDown 或 /home/user/bbdown/BBDown
# 2. 相对路径（相对于项目根目录）: tools/BBDown
# 3. 仅文件名（依赖 PATH）: BBDown
def get_bbdown_path() -> str:
    """动态获取 BBDown 路径，每次调用时重新解析。

    支持三种形式的环境变量 BBDOWN_PATH：
    1. 纯文件名（无路径分隔符）：优先从 PATH 查找，再查 tools/BBDown
    2. 绝对路径：直接返回
    3. 相对路径：相对于项目根目录解析
    """
    raw = os.getenv("BBDOWN_PATH", "BBDown").strip('"').strip("'")
    # 1. 纯文件名 -> PATH 查找，但必须验证文件真实存在
    if os.path.sep not in raw and "/" not in raw and "\\" not in raw:
        found = shutil.which(raw)
        if found and os.path.exists(found):
            return found
        fallback = Path(__file__).parent.parent / "tools" / raw
        if fallback.exists():
            return str(fallback)
        return raw
    # 2. 绝对路径
    if os.path.isabs(raw):
        return raw
    # 3. 相对路径 -> 相对项目根目录
    return str(Path(__file__).parent.parent / raw)


# 向后兼容：模块级变量（首次导入时缓存）
BBDOWN_PATH = get_bbdown_path()

API_URL = os.getenv("API_URL", "https://api.telegram.org").strip('"').strip("'")
SCHEDULER_MAX_PAGES = int(os.getenv("SCHEDULER_MAX_PAGES", "2"))

# DATA_DIR: 默认为项目根目录下的 data/ 目录
# 使用相对于此文件的位置解析，而不是当前工作目录
_raw_data_dir = os.getenv("DATA_DIR", "").strip('"').strip("'")
if _raw_data_dir:
    _data_path = Path(_raw_data_dir)
    if not _data_path.is_absolute():
        _data_path = Path(__file__).parent.parent / _data_path
    DATA_DIR = str(_data_path.resolve())
else:
    DATA_DIR = str(Path(__file__).parent.parent / "data")

# Ensure DATA_DIR exists
_path_data = Path(DATA_DIR)
if not _path_data.exists():
    try:
        _path_data.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        logger.warning(f"Cannot create DATA_DIR '{DATA_DIR}': {e}")
        logger.warning(f"Please set DATA_DIR in .env to a writable location.")

# File type constants (shared by handlers.py and scheduler.py)
VIDEO_EXT = {'.mp4', '.mkv', '.flv'}
AUDIO_EXT = {'.mp3', '.m4a', '.aac'}

# -----------------------------------------------------------------------
# BBDown 全局附加参数
# -tv  使用 TV 端接口下载，无需大会员也可访问较高画质，且不受部分地区 412 限制
# 该列表会被注入到所有 BBDown 调用（解析 & 下载），可在 .env 中通过
# BBDOWN_EXTRA_ARGS 覆盖（空格分隔，例如 "-tv --only-hevc"）
# -----------------------------------------------------------------------
_raw_extra = os.getenv("BBDOWN_EXTRA_ARGS", "-tv").strip('"').strip("'")
BBDOWN_EXTRA_ARGS: list[str] = [a for a in _raw_extra.split() if a]
logger.debug(f"BBDOWN_EXTRA_ARGS: {BBDOWN_EXTRA_ARGS}")

# -----------------------------------------------------------------------
# RSSHub 配置
# 用于订阅轮询时获取 UP 主最新视频，绕过 B 站 WBI 风控
#
# 官方实例：https://rsshub.app  （内地用户可能访问不稳，建议自部署）
# 自部署：  http://localhost:1200 或 http://your-server:1200
# -----------------------------------------------------------------------
RSSHUB_BASE_URL = os.getenv("RSSHUB_BASE_URL", "https://rsshub.app").strip('"').strip("'")

# 画质选项映射
# Bilibili 视频清晰度名称（dfn）完整列表
# 注意：BBDown 的 -q 参数需要使用这些完整名称
# 不同视频可能支持不同的清晰度组合
QUALITY_DFN = {
    # 8K / 超高清
    "8K": "8K 超高清",
    
    # 杜比视界 / HDR
    "dolby": "杜比视界",
    "hdr": "HDR 真彩",
    "hdr60": "HDR 真彩 60帧",
    
    # 4K
    "4k": "4K 超清",
    "4k60": "4K 超清 60帧",
    
    # 1080P 系列
    "1080p60": "1080P60",
    "1080p_plus": "1080P+",
    "1080p_high": "1080P 高码率",
    "1080p": "1080P",
    
    # 720P 系列
    "720p60": "720P60",
    "720p_high": "720P 高清",
    "720p": "720P",
    
    # 480P 系列
    "480p": "480P",
    "480p_clear": "480P 清晰",
    
    # 360P 系列
    "360p": "360P",
    "360p_smooth": "360P 流畅",
}

# 画质优先级配置 - 用于 BBDown -q 参数
QUALITY_PRIORITY = {
    "best": [],
    "1080": [
        "1080P 高码率", "1080P60", "1080P+", "1080P 高清", "1080P 大会员", "1080P",
        "720P60", "720P 高清", "720P 大会员", "720P",
        "480P 清晰", "480P 高清", "480P 大会员", "480P",
        "360P 流畅", "360P 高清", "360P 大会员", "360P"
    ],
    "720": [
        "720P60", "720P 高清", "720P 大会员", "720P",
        "480P 清晰", "480P 高清", "480P 大会员", "480P",
        "360P 流畅", "360P 高清", "360P 大会员", "360P"
    ],
    "480": [
        "480P 清晰", "480P 高清", "480P 大会员", "480P",
        "360P 流畅", "360P 高清", "360P 大会员", "360P"
    ],
    "360": [
        "360P 流畅", "360P 高清", "360P 大会员", "360P"
    ],
}

# 用户显示的画质选项（用于 UI 展示）
QUALITY_OPTIONS = {
    "best": "🎯 最高画质（不限制）",
    "1080": "📺 1080P（最高 1080P）",
    "720": "📺 720P（最高 720P）",
    "480": "📱 480P（最高 480P）",
    "360": "📱 360P（最高 360P）",
    "audio": "🎵 仅音频",
    "danmaku": "💬 仅弹幕",
    "sub": "📝 仅字幕",
}

DEFAULT_QUALITY = "best"  # 默认画质

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID
