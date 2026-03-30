import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip('"').strip("'")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip('"').strip("'"))
BBDOWN_PATH = os.getenv("BBDOWN_PATH", "BBDown").strip('"').strip("'")
DATA_DIR = os.getenv("DATA_DIR", "../data").strip('"').strip("'")
API_URL = os.getenv("API_URL", "https://api.telegram.org").strip('"').strip("'")
SCHEDULER_MAX_PAGES = int(os.getenv("SCHEDULER_MAX_PAGES", "2"))

# Ensure DATA_DIR exists
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

# File type constants (shared by handlers.py and scheduler.py)
VIDEO_EXT = {'.mp4', '.mkv', '.flv'}
AUDIO_EXT = {'.mp3', '.m4a', '.aac'}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID
