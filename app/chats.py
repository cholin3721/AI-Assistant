"""웹 화면 대화 기록. 새로고침해도 이어서 볼 수 있게 data/ 에만 저장합니다."""
import time

from . import store

DEFAULT = {"sessions": {}}
MAX_MSGS = 40
MAX_SESSIONS = 15


def get(session_id: str) -> list:
    if not session_id:
        return []
    return store.load("chats", DEFAULT)["sessions"].get(session_id, {}).get("messages", [])


def append(session_id: str, role: str, text: str, tools: list | None = None):
    if not session_id or session_id.startswith("task-") or session_id in ("discord", "telegram"):
        return
    item = {"role": role, "text": text, "tools": [{"name": t.get("name"), "label": t.get("label"), "ok": t.get("ok")}
                                                 for t in (tools or [])][:12], "at": time.time()}

    def fn(d):
        sess = d.setdefault("sessions", {}).setdefault(session_id, {"messages": [], "updated": 0})
        sess["messages"].append(item)
        del sess["messages"][:-MAX_MSGS]
        sess["updated"] = time.time()
        if len(d["sessions"]) > MAX_SESSIONS:
            keep = sorted(d["sessions"], key=lambda k: d["sessions"][k].get("updated", 0))[-MAX_SESSIONS:]
            d["sessions"] = {k: d["sessions"][k] for k in keep}
    store.update("chats", DEFAULT, fn)


def clear(session_id: str):
    def fn(d):
        d.get("sessions", {}).pop(session_id, None)
    store.update("chats", DEFAULT, fn)
