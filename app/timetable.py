"""시간표 저장 + 사진에서 시간표 읽기 + 빈 시간 찾기."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from . import config, google_auth, store
from .tools import ToolError, google_service, tool

KST = timezone(timedelta(hours=9))
DAYS = "월화수목금토일"
DEFAULT = {"classes": []}


class ClassItem(BaseModel):
    day: int = Field(description="요일 번호. 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6")
    start: str = Field(description="시작 시각 HH:MM (24시간제)")
    end: str = Field(description="종료 시각 HH:MM (24시간제)")
    title: str = Field(description="과목명")
    place: str = Field(description="강의실. 없으면 빈 문자열")


class Timetable(BaseModel):
    classes: list[ClassItem]


def _hhmm(v: str) -> str:
    m = re.match(r"^\s*(\d{1,2})[:시.]?\s*(\d{2})?", str(v or ""))
    if not m:
        raise ValueError(v)
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(v)
    return f"{h:02d}:{mi:02d}"


def normalize(rows: list) -> list:
    out = []
    for r in rows:
        try:
            day = int(r.get("day"))
            start, end = _hhmm(r.get("start")), _hhmm(r.get("end"))
        except (TypeError, ValueError):
            continue
        title = str(r.get("title") or "").strip()[:60]
        if not (0 <= day <= 6) or not title or end <= start:
            continue
        out.append({"id": r.get("id") or uuid.uuid4().hex[:8], "day": day, "start": start, "end": end,
                    "title": title, "place": str(r.get("place") or "").strip()[:40]})
    return sorted(out, key=lambda c: (c["day"], c["start"]))


def get() -> list:
    return store.load("timetable", DEFAULT)["classes"]


def replace(rows: list) -> list:
    rows = normalize(rows)
    store.save("timetable", {"classes": rows})
    return rows


def extract_from_image(data: bytes, mime: str) -> list:
    cfg = config.load()
    if not cfg.get("gemini_api_key"):
        raise ToolError("먼저 Gemini API 키를 설정해주세요.")
    client = genai.Client(api_key=cfg["gemini_api_key"])
    resp = client.models.generate_content(
        model=cfg.get("model") or "gemini-3.8-flash",
        contents=[types.Part.from_bytes(data=data, mime_type=mime),
                  "이 대학 시간표 이미지에서 수업을 모두 뽑아줘. 같은 과목이 여러 요일·교시에 있으면 각각 따로 적어. "
                  "교시만 적혀 있으면 표 옆의 시간 표시를 보고 실제 시각으로 바꿔. 연속된 교시는 하나로 합쳐."],
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=Timetable, temperature=0.1),
    )
    parsed = resp.parsed or Timetable.model_validate_json(resp.text or '{"classes": []}')
    return normalize([c.model_dump() for c in parsed.classes])


def today_block() -> str:
    """시스템 프롬프트용: 오늘·내일 수업."""
    now = datetime.now(KST)
    rows = get()
    if not rows:
        return "- (시간표 미등록)"
    lines = []
    for offset, label in ((0, "오늘"), (1, "내일")):
        d = (now + timedelta(days=offset)).weekday()
        cs = [f"{c['start']}~{c['end']} {c['title']}" + (f"({c['place']})" if c["place"] else "") for c in rows if c["day"] == d]
        lines.append(f"- {label}({DAYS[d]}): " + (", ".join(cs) if cs else "수업 없음"))
    return "\n".join(lines)


# ---------- 빈 시간 계산 ----------
def _busy_from_calendar(start: datetime, end: datetime) -> list:
    if google_auth.get_credentials() is None:
        return []
    svc = google_service("calendar", "v3")
    res = svc.freebusy().query(body={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                     "timeZone": "Asia/Seoul", "items": [{"id": "primary"}]}).execute()
    busy = []
    for b in res.get("calendars", {}).get("primary", {}).get("busy", []):
        s = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(KST)
        e = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(KST)
        busy.append((s, e, "일정"))
    return busy


def free_slots(days_ahead: int, duration_minutes: int, earliest: str, latest: str,
               include_weekends: bool, busy_calendar: list, classes: list, now: datetime) -> list:
    dur = timedelta(minutes=max(15, int(duration_minutes)))
    eh, em = map(int, _hhmm(earliest).split(":"))
    lh, lm = map(int, _hhmm(latest).split(":"))
    slots = []
    for off in range(0, int(days_ahead) + 1):
        day = (now + timedelta(days=off)).replace(hour=0, minute=0, second=0, microsecond=0)
        if not include_weekends and day.weekday() >= 5:
            continue
        w0, w1 = day.replace(hour=eh, minute=em), day.replace(hour=lh, minute=lm)
        if off == 0:
            rounded = now.replace(second=0, microsecond=0) + timedelta(minutes=(30 - now.minute % 30) % 30)
            w0 = max(w0, rounded)
        if w1 - w0 < dur:
            continue
        busy = [(s, e) for s, e, _ in busy_calendar if s < w1 and e > w0]
        for c in classes:
            if c["day"] == day.weekday():
                sh, sm = map(int, c["start"].split(":"))
                fh, fm = map(int, c["end"].split(":"))
                busy.append((day.replace(hour=sh, minute=sm), day.replace(hour=fh, minute=fm)))
        busy.sort()
        cur = w0
        for s, e in busy + [(w1, w1)]:
            s, e = max(s, w0), min(e, w1)
            if s - cur >= dur:
                slots.append((cur, s))
            cur = max(cur, e)
    return slots


@tool("빈 시간 찾기")
def find_free_time(days_ahead: int = 7, duration_minutes: int = 60, earliest: str = "09:00",
                   latest: str = "21:00", include_weekends: bool = False) -> dict:
    """구글 캘린더 일정과 시간표(수업)를 함께 보고 비어 있는 시간을 찾습니다. "회의 언제 가능해?", "이번 주 빈 시간" 같은 질문에 사용하세요.

    Args:
        days_ahead: 오늘부터 며칠 뒤까지 찾을지 (기본 7).
        duration_minutes: 필요한 시간(분). 기본 60.
        earliest: 하루 중 가장 이른 시각 "HH:MM".
        latest: 하루 중 가장 늦은 시각 "HH:MM".
        include_weekends: 주말도 포함할지.
    """
    now = datetime.now(KST)
    end = now + timedelta(days=int(days_ahead) + 1)
    try:
        busy = _busy_from_calendar(now, end)
    except ToolError:
        busy = []
    slots = free_slots(days_ahead, duration_minutes, earliest, latest, include_weekends, busy, get(), now)
    fmt = lambda s, e: f"{s.month}/{s.day}({DAYS[s.weekday()]}) {s:%H:%M}~{e:%H:%M} ({int((e - s).total_seconds() // 60)}분)"
    return {
        "used": {"calendar": google_auth.get_credentials() is not None, "timetable_classes": len(get())},
        "free_slots": [fmt(s, e) for s, e in slots[:20]],
        "note": "" if get() else "시간표가 등록되지 않아 수업 시간은 고려하지 못했어요. 설정 > 시간표에서 등록할 수 있어요.",
    }


@tool("시간표 보기")
def get_timetable() -> dict:
    """등록된 수업 시간표를 봅니다."""
    rows = get()
    if not rows:
        return {"classes": [], "note": "시간표가 없어요. 설정 > 시간표에서 사진으로 등록할 수 있어요."}
    return {"classes": [f"{DAYS[c['day']]} {c['start']}~{c['end']} {c['title']} {c['place']}".strip() for c in rows]}
