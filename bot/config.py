import os
from pathlib import Path
from dotenv import load_dotenv

# 尝试从多个位置加载 .env 文件
# 1. 当前工作目录
# 2. bot 模块的父目录（项目根目录）
_env_loaded = load_dotenv()
if not _env_loaded:
    # 尝试从项目根目录加载
    _project_root = Path(__file__).parent.parent
    _env_path = _project_root / ".env"
    _env_loaded = load_dotenv(_env_path)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip('"').strip("'")

# 调试：打印 token 前后缀（不打印完整 token）
if BOT_TOKEN:
    _token_preview = f"{BOT_TOKEN[:10]}...{BOT_TOKEN[-4:]}" if len(BOT_TOKEN) > 14 else "(too short)"
    print(f"🔍 DEBUG: BOT_TOKEN loaded: {_token_preview}")
else:
    print("⚠️ DEBUG: BOT_TOKEN is empty!")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip('"').strip("'"))
BBDOWN_PATH = os.getenv("BBDOWN_PATH", "BBDown").strip('"').strip("'")
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
        print(f"⚠️ Warning: Cannot create DATA_DIR '{DATA_DIR}': {e}")
        print(f"   Please set DATA_DIR in .env to a writable location.")

# File type constants (shared by handlers.py and scheduler.py)
VIDEO_EXT = {'.mp4', '.mkv', '.flv'}
AUDIO_EXT = {'.mp3', '.m4a', '.aac'}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID
