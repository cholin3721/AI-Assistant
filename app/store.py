"""data/ 폴더의 작은 JSON 저장소들 (스레드 안전)."""
import json
import threading

from . import config

_locks: dict = {}
_glock = threading.Lock()


def _lock(name: str):
    with _glock:
        return _locks.setdefault(name, threading.RLock())


def _path(name: str):
    return config.DATA_DIR / f"{name}.json"


def load(name: str, default):
    with _lock(name):
        p = _path(name)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return json.loads(json.dumps(default))


def save(name: str, data):
    with _lock(name):
        p = _path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def update(name: str, default, fn):
    """읽기-수정-쓰기를 한 번에. fn(data)의 반환값을 돌려줌."""
    with _lock(name):
        data = load(name, default)
        result = fn(data)
        save(name, data)
        return result
