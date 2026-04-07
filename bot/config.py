import os
import logging
from pathlib import Path
from dotenv import load_dotenv
import shutil

logger = logging.getLogger(__name__)

# 调试：检查环境变量在 load_dotenv 之前的值
_pre_token = os.getenv("BOT_TOKEN", "")
logger.debug(f"Before load_dotenv, BOT_TOKEN from env: {f'{_pre_token[:10]}...{_pre_token[-4:]}' if _pre_token and len(_pre_token) > 14 else '(empty or too short)'}")

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

# 调试：打印 token 前后缀（不打印完整 token）
if BOT_TOKEN:
    _token_preview = f"{BOT_TOKEN[:10]}...{BOT_TOKEN[-4:]}" if len(BOT_TOKEN) > 14 else "(too short)"
    logger.debug(f"BOT_TOKEN after all loading: {_token_preview}")
else:
    logger.warning("BOT_TOKEN is empty!")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip('"').strip("'"))

# BBDown 路径解析：支持多种形式
# 1. 绝对路径: /usr/local/bin/BBDown 或 /home/user/bbdown/BBDown
# 2. 相对路径（相对于项目根目录）: tools/BBDown
# 3. 仅文件名（依赖 PATH）: BBDown
_raw_bbdown_path = os.getenv("BBDOWN_PATH", "BBDown").strip('"').strip("'")

# 检查是否是 PATH 中的命令（不带路径分隔符）
if os.path.sep not in _raw_bbdown_path and "/" not in _raw_bbdown_path and "\\" not in _raw_bbdown_path:
    # 仅文件名，尝试在 PATH 中查找
    bbdown_in_path = shutil.which(_raw_bbdown_path)
    if bbdown_in_path:
        BBDOWN_PATH = bbdown_in_path
        logger.debug(f"Found BBDown in PATH: {BBDOWN_PATH}")
    else:
        # 不在 PATH 中，保持原值（后续会在项目根目录下查找 tools/BBDown）
        _fallback_path = _project_root / "tools" / _raw_bbdown_path
        if _fallback_path.exists():
            BBDOWN_PATH = str(_fallback_path)
            logger.debug(f"Using fallback BBDown at: {BBDOWN_PATH}")
        else:
            BBDOWN_PATH = _raw_bbdown_path
            logger.debug(f"BBDown path not resolved, using: {BBDOWN_PATH}")
else:
    # 用户指定了路径，可能是绝对或相对路径
    if os.path.isabs(_raw_bbdown_path):
        BBDOWN_PATH = _raw_bbdown_path
    else:
        # 相对路径，相对于项目根目录解析
        BBDOWN_PATH = str(_project_root / _raw_bbdown_path)
    logger.debug(f"Using specified BBDown path: {BBDOWN_PATH}")

API_URL = os.getenv("API_URL", "https://api.telegram.org").strip('"').strip("'")
SCHEDULER_MAX_PAGES = int(os.getenv("SCHEDULER_MAX_PAGES", "2"))

# DATA_DIR: 默认为项目根目录下的 data/ 目录
# 使用相对于此文件的位置解析，而不是当前工作目录
_raw_data_dir = os.getenv("DATA_DIR", "").strip('"').strip("'")
if _raw_data_dir:
    # 用户指定了路径，可能是相对或绝对路径
    DATA_DIR = _raw_data_dir
else:
    # 默认：bot/../data（项目根目录下的 data）
    DATA_DIR = str(Path(__file__).parent.parent / "data")

# Ensure DATA_DIR exists (deferred to avoid permission issues during import)
# This will be called when the config module is imported
_path_data = Path(DATA_DIR)
if not _path_data.exists():
    try:
        _path_data.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        # Delay error until runtime, don't crash on import
        logger.warning(f"Cannot create DATA_DIR '{DATA_DIR}': {e}")
        logger.warning(f"Please set DATA_DIR in .env to a writable location.")

# File type constants (shared by handlers.py and scheduler.py)
VIDEO_EXT = {'.mp4', '.mkv', '.flv'}
AUDIO_EXT = {'.mp3', '.m4a', '.aac'}

# 画质选项映射
QUALITY_OPTIONS = {
    "best": "最高画质",
    "1080": "1080P",
    "720": "720P",
    "480": "480P",
    "360": "360P"
}
DEFAULT_QUALITY = "best"  # 默认画质

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID
