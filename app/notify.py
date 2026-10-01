"""알림 센터: 화면의 알림함 + 연결된 메신저(디스코드·텔레그램)로 동시에 보냄."""
import time
import uuid

from . import store

DEFAULT = {"items": []}


def add(kind: str, title: str, body: str, link: str = "", push: bool = True) -> dict:
    """kind: briefing | weekly | reminder | schedule | system"""
    n = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "body": body,
         "link": link, "created": time.time(), "read": False}

    def fn(d):
        d["items"].insert(0, n)
        del d["items"][100:]
    store.update("notifications", DEFAULT, fn)
    from . import stats
    stats.record(f"notify:{kind}")
    if push:
        from . import discord_bot, telegram_bot
        text = f"**{title}**\n\n{body}" + (f"\n\n{link}" if link else "")
        for messenger in (discord_bot, telegram_bot):
            messenger.send_safe(text)
    return n


def items() -> list:
    return store.load("notifications", DEFAULT)["items"]


def unread() -> int:
    return sum(1 for n in items() if not n["read"])


def mark_all_read():
    def fn(d):
        for n in d["items"]:
            n["read"] = True
    store.update("notifications", DEFAULT, fn)
