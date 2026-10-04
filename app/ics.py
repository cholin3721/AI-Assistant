"""시간표·할 일을 캘린더 파일(.ics)로 내보내기.

구글 연동 없이도 폰 캘린더·아웃룩·구글 캘린더의 「가져오기」로 넣을 수 있는 표준 형식(iCalendar, RFC 5545)입니다.
- 시간표: 수업마다 '매주 반복' 일정 하나. 학기 종강일까지 반복하고, 학사일정의 휴일은 빼 둡니다.
- 할 일: 마감일이 있는 것만 '하루 종일' 일정으로.
순수 함수만 두어 네트워크 없이 테스트할 수 있습니다.
"""
import hashlib
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
TZID = "Asia/Seoul"
PRODID = "-//Inha AI Assistant//KO"
VTIMEZONE = ["BEGIN:VTIMEZONE", f"TZID:{TZID}", "BEGIN:STANDARD", "DTSTART:19700101T000000",
             "TZOFFSETFROM:+0900", "TZOFFSETTO:+0900", "TZNAME:KST", "END:STANDARD", "END:VTIMEZONE"]


def esc(text: str) -> str:
    """글자 값 이스케이프 (역슬래시·쉼표·세미콜론·줄바꿈)."""
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """한 줄을 75바이트 이하로 접기. 한글(3바이트)이 중간에서 잘리지 않게 글자 단위로 자릅니다."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):   # 이어지는 줄은 맨 앞 공백 1바이트를 뺀 만큼
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _uid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20] + "@inha-ai-assistant"


def _wrap(events: list, name: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{esc(name)}", f"X-WR-TIMEZONE:{TZID}", *VTIMEZONE]
    for ev in events:
        lines += ["BEGIN:VEVENT", *ev, "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_range(today: date) -> tuple:
    """학기 기간을 모를 때: 이번 주 월요일부터 16주."""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(weeks=16) - timedelta(days=1)


def timetable_ics(classes: list, start: date, end: date, holidays=None, now: datetime | None = None) -> str:
    """classes: [{day(0=월), start 'HH:MM', end 'HH:MM', title, place, prof}] → 매주 반복 일정.
    start~end: 반복 기간(보통 개강일~종강일). holidays: 수업 없는 날짜들(반복에서 제외)."""
    stamp, events = _stamp(now), []
    off = sorted(d for d in (holidays or ()) if start <= d <= end)
    until = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=KST).astimezone(timezone.utc)
    for c in classes:
        first = start + timedelta(days=(int(c["day"]) - start.weekday()) % 7)
        if first > end:
            continue
        hm = lambda v: v.replace(":", "") + "00"
        ev = [f"UID:{_uid('class', c['day'], c['start'], c['title'])}", f"DTSTAMP:{stamp}",
              f"DTSTART;TZID={TZID}:{first:%Y%m%d}T{hm(c['start'])}",
              f"DTEND;TZID={TZID}:{first:%Y%m%d}T{hm(c['end'])}",
              f"RRULE:FREQ=WEEKLY;UNTIL={until:%Y%m%dT%H%M%SZ}",
              f"SUMMARY:{esc(c['title'])}"]
        skip = [d for d in off if d.weekday() == first.weekday()]
        if skip:
            ev.append(f"EXDATE;TZID={TZID}:" + ",".join(f"{d:%Y%m%d}T{hm(c['start'])}" for d in skip))
        if c.get("place"):
            ev.append(f"LOCATION:{esc(c['place'])}")
        if c.get("prof"):
            ev.append(f"DESCRIPTION:{esc(c['prof'] + ' 교수')}")
        events.append(ev)
    return _wrap(events, "수업 시간표")


def todos_ics(items: list, now: datetime | None = None) -> str:
    """마감일이 있는 할 일 → 하루 종일 일정."""
    stamp, events = _stamp(now), []
    for t in items:
        try:
            d = datetime.strptime(t.get("due") or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        desc = "\n".join(x for x in (t.get("note"), t.get("link")) if x)
        ev = [f"UID:{_uid('todo', t.get('id'), t.get('due'))}", f"DTSTAMP:{stamp}",
              f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
              f"SUMMARY:{esc('[할 일] ' + t['title'])}", "TRANSP:TRANSPARENT"]
        if desc:
            ev.append(f"DESCRIPTION:{esc(desc)}")
        link = str(t.get("link") or "")
        if link.startswith(("http://", "https://")) and not any(ch.isspace() for ch in link):
            ev.append(f"URL:{link}")
        events.append(ev)
    return _wrap(events, "할 일 마감")
