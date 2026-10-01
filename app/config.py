"""설정 저장소 — 모든 키와 토큰은 이 PC의 data/ 폴더에만 저장됩니다."""
import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
CREDENTIALS_PATH = DATA_DIR / "credentials.json"   # 구글 OAuth 클라이언트 (사용자가 업로드)
TOKEN_PATH = DATA_DIR / "token.json"               # 구글 로그인 토큰 (자동 생성)

HOST = "127.0.0.1"   # 외부에서 접속 못 하도록 내 PC에서만 열림
PORT = 8765

DEFAULTS = {
    "gemini_api_key": "",
    "model": "",
    "profile": {"name": "", "department": "", "grade": "", "interests": ""},
    "setup_done": False,
    "auto_scan": True,   # 새 메일에서 일정 후보 자동 찾기
    "telegram": {"token": "", "bot_username": "", "chat_id": None, "link_code": "", "owner_name": ""},
    "discord": {"token": "", "bot_username": "", "app_id": "", "owner_id": None, "owner_name": "", "link_code": ""},
    "automation": {
        "briefing": True, "briefing_time": "08:00",          # 아침 브리핑
        "weekly": True, "weekly_day": 4, "weekly_time": "18:00",  # 주간 회고 (0=월 … 4=금)
        "reminders": True,                                     # 마감 D-3·D-1·당일 알림
        "notice_scan": True,                                   # 학교 공지에서 일정 후보 찾기
    },
}

_lock = threading.Lock()


def load() -> dict:
    with _lock:
        if not CONFIG_PATH.exists():
            return json.loads(json.dumps(DEFAULTS))
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    merged = json.loads(json.dumps(DEFAULTS))
    for k, v in data.items():
        if isinstance(merged.get(k), dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}   # 새 버전에서 추가된 기본값 유지
        else:
            merged[k] = v
    return merged


def save(updates: dict) -> dict:
    cfg = load()
    for k, v in updates.items():
        if k in ("profile", "automation") and isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    with _lock:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def mask(key: str) -> str:
    if not key:
        return ""
    return key[:4] + "•" * 8 + key[-4:] if len(key) > 10 else "•" * len(key)
