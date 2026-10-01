"""윈도우 바탕화면 알림(토스트). 연결이 안 되거나 실패한 경우 조용히 넘어갑니다."""
import sys


def show(title: str, body: str):
    if sys.platform != "win32":
        return
    from . import config
    if not config.load().get("automation", {}).get("desktop_toast", True):
        return
    msg = " ".join((body or "").replace("*", "").replace("#", "").split())[:180]
    try:
        from winotify import Notification
        Notification(app_id="인하 AI 비서", title=title[:60] or "인하 AI 비서",
                     msg=msg or "새 알림이 있어요.", duration="short").show()
    except Exception:
        pass
