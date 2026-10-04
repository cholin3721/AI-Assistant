"""인하공전 학사일정 크롤러 (K2Web schdulmanage 연간 뷰).

https://www.inhatc.ac.kr/kr/123/subview.do  →  /schdulmanage/kr/3/view.do
연간 페이지에 월별 .scheList 가 렌더되어 있어 AJAX 없이 HTML만 파싱합니다.
받아 온 일정은 data/academic.json 에도 남겨, 인터넷이 안 될 때나 프로그램을 막 켰을 때도 쓸 수 있습니다.
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from . import tool, ToolError
from .notices import HEADERS, SITE

KST = timezone(timedelta(hours=9))
CAL_URL = f"{SITE}/schdulmanage/kr/3/view.do"
PAGE_URL = f"{SITE}/kr/123/subview.do"
CACHE_SEC = 6 * 3600
FAIL_WAIT_SEC = 5 * 60    # 접속에 실패하면 이 시간 동안은 다시 시도하지 않음 (대화마다 15초씩 기다리지 않게)
BUSY_WORDS = ("시험", "평가", "휴강", "연휴", "공휴일", "방학", "학위수여", "종강", "개강")
# 수업이 없는 날로 보는 일정 (법정 공휴일·연휴·대체휴일·개교기념일). '근로자의날'·'스승의날'은 수업이 있어 넣지 않음.
HOLIDAY_WORDS = ("연휴", "대체휴일", "대체공휴일", "임시공휴일", "공휴일", "삼일절", "어린이날", "부처님오신날",
                 "석가탄신일", "현충일", "광복절", "개천절", "한글날", "성탄절", "크리스마스", "새해 첫날", "신정",
                 "설날", "추석", "선거일", "개교기념일", "휴강")
EXAM_WORDS = ("중간종합평가", "기말종합평가", "중간고사", "기말고사", "중간시험", "기말시험")

# "02.09. (월)" 또는 "02.09. (월) ~ 02.12. (목)"
_DT = re.compile(
    r"(?P<m1>\d{1,2})\.(?P<d1>\d{1,2})\.(?:\s*\([^)]+\))?"
    r"(?:\s*~\s*(?P<m2>\d{1,2})\.(?P<d2>\d{1,2})\.(?:\s*\([^)]+\))?)?"
)

_cache = {"at": 0.0, "events": [], "failed_at": 0.0, "disk": False}


def parse_sche_list(html: str) -> list:
    """연간 학사일정 HTML → [{title, start, end, all_day, busy}]. 테스트용으로 순수 함수."""
    soup = BeautifulSoup(html, "html.parser")
    events, seen = [], set()
    for wrap in soup.select(".yearSchdulWrap"):
        ym = wrap.select_one("[id^=yearmonth]")
        ytxt = ym.get_text(strip=True) if ym else ""
        ym_m = re.match(r"(20\d{2})\.(\d{1,2})", ytxt)
        year = int(ym_m.group(1)) if ym_m else datetime.now(KST).year
        for li in wrap.select(".scheList li"):
            dt_el, dd_el = li.select_one("dt"), li.select_one("dd")
            if not dt_el or not dd_el:
                continue
            title = re.sub(r"\s+", " ", dd_el.get_text(" ", strip=True)).strip()
            m = _DT.search(dt_el.get_text(" ", strip=True))
            if not m or not title:
                continue
            m1, d1 = int(m.group("m1")), int(m.group("d1"))
            start = f"{year:04d}-{m1:02d}-{d1:02d}"
            if m.group("m2"):
                m2, d2 = int(m.group("m2")), int(m.group("d2"))
                y2 = year if m2 >= m1 else year + 1
                end = f"{y2:04d}-{m2:02d}-{d2:02d}"
            else:
                end = start
            key = (start, end, title)
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "title": title, "start": start, "end": end, "all_day": True,
                "busy": any(w in title for w in BUSY_WORDS),
                "url": PAGE_URL,
            })
    events.sort(key=lambda e: e["start"])
    return events


def _load_disk():
    """프로그램을 켠 뒤 처음 한 번: 지난번에 받아 둔 학사일정을 불러옴."""
    if _cache["disk"]:
        return
    _cache["disk"] = True
    try:
        from .. import store
        saved = store.load("academic", {"at": 0.0, "events": []})
        if saved.get("events") and not _cache["events"]:
            _cache["at"], _cache["events"] = float(saved.get("at", 0)), saved["events"]
    except Exception:
        pass


def cached() -> list:
    """네트워크 없이 지금 알고 있는 학사일정 (없으면 빈 목록). 화면 상태 조회처럼 기다리면 안 되는 곳에서 사용."""
    _load_disk()
    return _cache["events"]


def fetch(force: bool = False) -> list:
    _load_disk()
    now = time.time()
    if not force and _cache["events"] and now - _cache["at"] < CACHE_SEC:
        return _cache["events"]
    if not force and now - _cache["failed_at"] < FAIL_WAIT_SEC:
        if _cache["events"]:
            return _cache["events"]
        raise ToolError("학사일정을 가져오지 못했어요 (잠시 뒤 다시 시도해요)")
    try:
        r = requests.get(CAL_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
        events = parse_sche_list(r.text)
        if not events:
            raise ValueError("empty")
    except (requests.RequestException, ValueError) as e:
        _cache["failed_at"] = now
        if _cache["events"]:
            return _cache["events"]       # 예전에 받아 둔 것으로 계속
        raise ToolError(f"학사일정을 가져오지 못했어요 ({type(e).__name__})")
    _cache.update(at=now, events=events, failed_at=0.0)
    try:
        from .. import store
        store.save("academic", {"at": now, "events": events})
    except Exception:
        pass
    return events


def _matches(events: list, day: str, words: tuple) -> str:
    for e in events:
        if e["start"] <= day <= e["end"] and any(w in e["title"] for w in words):
            return e["title"]
    return ""


def day_info(day, events: list | None = None) -> dict:
    """그 날짜가 휴일인지·시험기간인지. day: date 또는 'YYYY-MM-DD'.
    {"holiday": "개천절 대체휴일" | "", "exam": "중간종합평가" | ""}. 학사일정을 모르면 둘 다 빈 문자열."""
    key = day if isinstance(day, str) else day.strftime("%Y-%m-%d")
    events = cached() if events is None else events
    return {"holiday": _matches(events, key, HOLIDAY_WORDS), "exam": _matches(events, key, EXAM_WORDS)}


def no_class_days(start, end, events: list | None = None) -> dict:
    """start~end(date) 사이의 수업 없는 날 {date: 이유}."""
    events = cached() if events is None else events
    out, d = {}, start
    while d <= end:
        title = _matches(events, d.strftime("%Y-%m-%d"), HOLIDAY_WORDS)
        if title:
            out[d] = title
        d += timedelta(days=1)
    return out


def semester_range(today=None, events: list | None = None):
    """오늘이 속한(또는 곧 시작할) 학기의 (개강일, 종강일) date. 학사일정에서 못 찾으면 None."""
    events = cached() if events is None else events
    today = today or datetime.now(KST).date()
    key = today.strftime("%Y-%m-%d")
    starts = sorted(e["start"] for e in events if "개강" in e["title"] or "개시" in e["title"])
    ends = sorted(e["end"] for e in events if "종강" in e["title"])
    end = next((x for x in ends if x >= key), None)
    if end is None:
        return None
    before = [x for x in starts if x <= end]
    if not before:
        return None
    to_date = lambda v: datetime.strptime(v, "%Y-%m-%d").date()
    return to_date(before[-1]), to_date(end)


def upcoming(days_ahead: int = 60, days_back: int = 0) -> list:
    today = datetime.now(KST).date()
    lo = (today - timedelta(days=int(days_back))).strftime("%Y-%m-%d")
    hi = (today + timedelta(days=int(days_ahead))).strftime("%Y-%m-%d")
    return [e for e in fetch() if e["end"] >= lo and e["start"] <= hi]


def prompt_block(days_ahead: int = 21) -> str:
    """시스템 프롬프트용: 다가오는 학사일정 몇 줄."""
    items = upcoming(days_ahead)
    if not items:
        return "- (학사일정을 아직 못 가져왔어요)"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = []
    for e in items[:12]:
        span = e["start"][5:].replace("-", "/") if e["start"] == e["end"] else \
            f"{e['start'][5:].replace('-', '/')}~{e['end'][5:].replace('-', '/')}"
        tag = " · 오늘" if e["start"] <= today <= e["end"] else ""
        lines.append(f"- {span} {e['title']}{tag}")
    return "\n".join(lines)


def busy_ranges(start: datetime, end: datetime) -> list:
    """빈 시간 계산용: 시험·연휴 등을 (시작, 끝) datetime 목록으로."""
    out = []
    for e in upcoming(days_ahead=max(1, (end.date() - start.date()).days + 1), days_back=1):
        if not e["busy"]:
            continue
        s = datetime.strptime(e["start"], "%Y-%m-%d").replace(tzinfo=KST)
        last = datetime.strptime(e["end"], "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        if s < end and last > start:
            out.append((s, last, e["title"]))
    return out


@tool("학사일정 확인")
def get_academic_calendar(days_ahead: int = 60, keyword: str = "") -> dict:
    """인하공업전문대학 학사일정(수강신청·개강·중간고사·등록금 납부 등)을 가져옵니다.

    Args:
        days_ahead: 오늘부터 며칠 뒤까지 (기본 60).
        keyword: 제목 검색어 (예: "수강신청", "시험"). 없으면 빈 문자열.
    """
    items = upcoming(int(days_ahead))
    if keyword:
        kw = keyword.lower()
        items = [e for e in items if kw in e["title"].lower()]
    return {"count": len(items), "url": PAGE_URL, "events": items[:40]}
