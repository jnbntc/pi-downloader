"""Configuration. Importing this module has no filesystem side effects."""
import os
from pathlib import Path


def integer(name, default, minimum=1):
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def ids(name, default=""):
    return frozenset(int(v.strip()) for v in os.getenv(name, default).split(",") if v.strip())


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "http://telegram-api:8081/bot")
QBIT_HOST = os.getenv("QBIT_HOST", "http://qbittorrent:8080")
QBIT_USERNAME = os.getenv("QBIT_USERNAME", "admin")
QBIT_PASSWORD = os.getenv("QBIT_PASSWORD", "")
ALLOWED_USER_IDS = ids("ALLOWED_USER_IDS")
ALLOWED_CHAT_IDS = ids("ALLOWED_CHAT_IDS", os.getenv("ALLOWED_USER_IDS", ""))
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", "/data/workspace"))
VAULT_DIR = Path(os.getenv("VAULT_DIR", "/data/media"))
TORRENT_DIR = Path(os.getenv("TORRENT_DIR", "/data/torrents"))
TELEGRAM_CACHE_DIR = Path(os.getenv("TELEGRAM_CACHE_DIR", "/var/lib/telegram-bot-api"))
DIR_TMP_MEDIA = WORKSPACE_DIR / "ffmpeg-tmp"
DIR_TMP_DECOMPRESS = WORKSPACE_DIR / "decompress"
DIR_MEDIA_RRSS = VAULT_DIR / "rrss"
DIR_MEDIA_EXTRACTED = VAULT_DIR / "extracted"
DIR_TELEGRAM_SAVED = VAULT_DIR / "telegram-saved"
STATE_DIR = WORKSPACE_DIR / ".pi-downloader"
MAX_CONCURRENT_TASKS = integer("MAX_CONCURRENT_TASKS", 2)
MAX_QUEUE_SIZE = integer("MAX_QUEUE_SIZE", 50)
MAX_MEDIA_FILES = integer("MAX_MEDIA_FILES", 10)
MAX_DOWNLOAD_BYTES = integer("MAX_DOWNLOAD_BYTES", 8_000_000_000)
MAX_UPLOAD_BYTES = integer("MAX_UPLOAD_BYTES", 2_000_000_000)
MAX_EXTRACT_BYTES = integer("MAX_EXTRACT_BYTES", 10_000_000_000)
MAX_EXTRACT_FILES = integer("MAX_EXTRACT_FILES", 10_000)
TASK_TIMEOUT = integer("TASK_TIMEOUT", 900)
VIDEO_TIMEOUT = integer("VIDEO_TIMEOUT", 3600)
TELEGRAM_TIMEOUT = integer("TELEGRAM_TIMEOUT", 1800)


def validate():
    for name in ["BOT_TOKEN", "QBIT_PASSWORD"]:
        if not globals()[name]:
            raise ValueError(f"Missing required setting: {name}")
    if not ALLOWED_USER_IDS or not ALLOWED_CHAT_IDS:
        raise ValueError("ALLOWED_USER_IDS and ALLOWED_CHAT_IDS must authorize at least one ID")


def prepare_directories():
    for directory in [DIR_TMP_MEDIA, DIR_TMP_DECOMPRESS, DIR_MEDIA_RRSS,
                      DIR_MEDIA_EXTRACTED, DIR_TELEGRAM_SAVED]:
        directory.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.chmod(0o700)
