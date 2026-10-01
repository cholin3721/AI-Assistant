"""인하공전 학사일정 크롤러 (K2Web schdulmanage 연간 뷰).

https://www.inhatc.ac.kr/kr/123/subview.do  →  /schdulmanage/kr/3/view.do
연간 페이지에 월별 .scheList 가 렌더되어 있어 AJAX 없이 HTML만 파싱합니다.
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
BUSY_WORDS = ("시험", "평가", "휴강", "연휴", "공휴일", "방학", "학위수여", "종강", "개강")

# "02.09. (월)" 또는 "02.09. (월) ~ 02.12. (목)"
_DT = re.compile(
    r"(?P<m1>\d{1,2})\.(?P<d1>\d{1,2})\.(?:\s*\([^)]+\))?"
    r"(?:\s*~\s*(?P<m2>\d{1,2})\.(?P<d2>\d{1,2})\.(?:\s*\([^)]+\))?)?"
)

_cache = {"at": 0.0, "events": []}


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


def fetch(force: bool = False) -> list:
    now = time.time()
    if not force and _cache["events"] and now - _cache["at"] < CACHE_SEC:
        return _cache["events"]
    try:
        r = requests.get(CAL_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    except requests.RequestException as e:
        if _cache["events"]:
            return _cache["events"]
        raise ToolError(f"학사일정을 가져오지 못했어요 ({type(e).__name__})")
    events = parse_sche_list(r.text)
    _cache["at"], _cache["events"] = now, events
    return events


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
