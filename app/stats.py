"""사용 통계: 비서가 실제로 한 일을 날짜별로 세고, 아낀 시간을 추정합니다.

메일·공지·대화 '내용'은 저장하지 않고 횟수만 셉니다. 베타 테스트 결과를 모을 때
「통계 내보내기」로 익명 JSON을 받아 보고서에 쓸 수 있습니다.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import store

KST = timezone(timedelta(hours=9))
DEFAULT = {"install_id": "", "since": "", "days": {}, "feedback": []}
KEEP_DAYS = 180

# 직접 했다면 걸렸을 시간(분) — 보고서에 '추정치'로 표기
MINUTES = {
    "tool:search_emails": 1, "tool:read_email": 2, "tool:read_email_attachment": 4,
    "tool:create_email_draft": 5, "tool:find_unanswered_emails": 5, "tool:find_awaiting_replies": 5,
    "tool:list_calendar_events": 1, "tool:create_calendar_event": 1, "tool:find_free_time": 5,
    "tool:search_drive_files": 1, "tool:read_drive_file": 3,
    "tool:get_school_notices": 3, "tool:read_school_notice": 2, "tool:read_notice_attachment": 5,
    "tool:find_events_in_emails": 5, "tool:find_events_in_notices": 10, "tool:get_academic_calendar": 2,
    "notify:briefing": 10, "notify:weekly": 15, "notify:reminder": 2,
    "tool:read_team_chat": 10,
    "candidate_added": 2, "candidate_todo": 2, "form_draft": 30, "form_rewrite": 2,
}
MINUTE_LABELS = [
    ("아침 브리핑 1회", 10), ("주간 회고 1회", 15), ("마감 알림 1건", 2), ("공지 목록 확인", 3),
    ("공지 첨부 읽기", 5), ("공지에서 일정 찾기", 10), ("메일 답장 초안", 5), ("빈 시간 찾기", 5),
    ("일정 후보를 캘린더·할 일에 저장", 2), ("신청서 초안 1건", 30),
]
TOOL_NAMES = {
    "search_emails": "메일 검색", "read_email": "메일 읽기", "read_email_attachment": "메일 첨부 읽기",
    "create_email_draft": "답장 초안", "find_unanswered_emails": "답장 챙기기", "find_awaiting_replies": "회신 대기 확인",
    "list_calendar_events": "일정 조회", "create_calendar_event": "일정 등록", "find_free_time": "빈 시간 찾기",
    "search_drive_files": "드라이브 검색", "read_drive_file": "드라이브 읽기",
    "get_school_notices": "학교 공지 확인", "read_school_notice": "공지 본문 읽기", "read_notice_attachment": "공지 첨부 읽기",
    "find_events_in_emails": "메일에서 일정 찾기", "find_events_in_notices": "공지에서 일정 찾기",
    "get_academic_calendar": "학사일정 확인",
    "remember": "기억하기", "forget": "기억 지우기", "add_todo": "할 일 추가", "list_todos": "할 일 보기",
    "complete_todo": "할 일 완료", "read_team_chat": "팀 채널 대화 읽기", "get_timetable": "시간표 보기", "draft_application": "신청서 초안",
}


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(KST)).strftime("%Y-%m-%d")


def record(event: str, n: int = 1, now: datetime | None = None):
    """이벤트 횟수 +n. 실패해도 비서 동작에는 영향 없음."""
    if n <= 0:
        return
    day = _today(now)

    def fn(d):
        if not d.get("install_id"):
            d["install_id"] = uuid.uuid4().hex[:12]   # 익명 식별자 (기기 구분용)
        if not d.get("since"):
            d["since"] = day
        bucket = d.setdefault("days", {}).setdefault(day, {})
        bucket[event] = bucket.get(event, 0) + n
        if len(d["days"]) > KEEP_DAYS:
            for k in sorted(d["days"])[:-KEEP_DAYS]:
                del d["days"][k]
    try:
        store.update("stats", DEFAULT, fn)
    except Exception:
        pass


def feedback(rating: str, tools: list | None = None, reason: str = "") -> dict:
    if rating not in ("up", "down"):
        raise ValueError("rating")
    item = {"at": time.time(), "day": _today(), "rating": rating,
            "tools": [str(t)[:40] for t in (tools or [])][:10], "reason": reason.strip()[:200]}

    def fn(d):
        d.setdefault("feedback", []).append(item)
        del d["feedback"][:-500]
    store.update("stats", DEFAULT, fn)
    record(f"feedback:{rating}")
    return item


def _sum(days: dict, keys_from: str, keys_to: str) -> dict:
    total: dict = {}
    for day, bucket in days.items():
        if keys_from <= day <= keys_to:
            for k, v in bucket.items():
                total[k] = total.get(k, 0) + v
    return total


def summary(period_days: int = 30, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    d = store.load("stats", DEFAULT)
    days = d.get("days", {})
    end = _today(now)
    start = (now - timedelta(days=period_days - 1)).strftime("%Y-%m-%d") if period_days else min(list(days) + [end])
    t = _sum(days, start, end)
    g = lambda prefix: sum(v for k, v in t.items() if k.startswith(prefix))

    minutes = sum(MINUTES.get(k, 0) * v for k, v in t.items())
    fb = [f for f in d.get("feedback", []) if start <= f.get("day", "") <= end]
    up = sum(1 for f in fb if f["rating"] == "up")

    # 최근 14일 일별 활동 (대화 + 비서가 한 일)
    chart = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        b = days.get(day, {})
        chats = sum(v for k, v in b.items() if k.startswith("chat:"))
        auto = sum(v for k, v in b.items() if k.startswith("notify:") or k.startswith("candidate_found"))
        chart.append({"day": day, "chats": chats, "auto": auto})

    tools = sorted(((k[5:], v) for k, v in t.items() if k.startswith("tool:")), key=lambda kv: -kv[1])
    return {
        "period": {"start": start, "end": end, "days": period_days},
        "since": d.get("since", ""),
        "kpi": {
            "chats": g("chat:"),
            "chats_by_channel": {k[5:]: v for k, v in t.items() if k.startswith("chat:")},
            "reminders": t.get("notify:reminder", 0),
            "briefings": t.get("notify:briefing", 0) + t.get("notify:weekly", 0),
            "candidates_found": g("candidate_found"),
            "candidates_added": t.get("candidate_added", 0),
            "candidates_todo": t.get("candidate_todo", 0),
            "email_drafts": t.get("tool:create_email_draft", 0),
            "form_drafts": t.get("form_draft", 0),
            "todos_done": t.get("todo_done", 0),
            "feedback_up": up, "feedback_total": len(fb),
            "satisfaction": round(up / len(fb) * 100) if fb else None,
            "minutes_saved": minutes,
        },
        "chart": chart,
        "top_tools": [{"name": TOOL_NAMES.get(k, k), "count": v} for k, v in tools[:6]],
        "assumptions": [{"label": a, "minutes": m} for a, m in MINUTE_LABELS],
        "recent_feedback": [{"rating": f["rating"], "reason": f["reason"], "day": f["day"]}
                            for f in fb[-5:] if f.get("reason")],
    }


def export() -> dict:
    """베타 테스트 제출용 익명 통계 — 메일·공지·대화 내용, 이름, 이메일은 들어가지 않음."""
    d = store.load("stats", DEFAULT)
    return {
        "app": "inha-ai-assistant",
        "exported_at": datetime.now(KST).isoformat(timespec="seconds"),
        "install_id": d.get("install_id", ""),
        "since": d.get("since", ""),
        "summary_all": summary(0)["kpi"],
        "summary_30d": summary(30)["kpi"],
        "daily_counts": d.get("days", {}),
        "feedback": [{"day": f["day"], "rating": f["rating"], "tools": f["tools"], "reason": f["reason"]}
                     for f in d.get("feedback", [])],
    }
