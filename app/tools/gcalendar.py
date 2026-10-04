from datetime import datetime, timedelta, timezone

from . import tool, google_service, ToolError

KST = timezone(timedelta(hours=9))
TZ = "Asia/Seoul"


def fetch_events(days_ahead: int = 7, days_back: int = 0, keyword: str = "", limit: int = 50) -> dict:
    """기본 캘린더 일정을 읽습니다 (도구가 아닌 내부용 — 자동 확인이 리포트에 세어지지 않음).
    limit까지 여러 페이지를 이어 읽습니다. 구글 미연동이면 ToolError."""
    svc = google_service("calendar", "v3")
    now = datetime.now(KST)
    start = (now - timedelta(days=int(days_back))).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=int(days_ahead))).replace(hour=23, minute=59, second=59, microsecond=0)
    params = dict(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
                  singleEvents=True, orderBy="startTime", maxResults=min(250, max(1, int(limit))), timeZone=TZ)
    if keyword:
        params["q"] = keyword
    events, token = [], None
    for _ in range(10):
        res = svc.events().list(**params, pageToken=token).execute()
        for e in res.get("items", []):
            s, en = e.get("start", {}), e.get("end", {})
            events.append({
                "title": e.get("summary", "(제목 없음)"),
                "start": s.get("dateTime") or s.get("date"),
                "end": en.get("dateTime") or en.get("date"),
                "all_day": "date" in s,
                "location": e.get("location", ""),
                "link": e.get("htmlLink", ""),
            })
        token = res.get("nextPageToken")
        if not token or len(events) >= limit:
            break
    more = bool(token) or len(events) > limit
    return {"range": f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}", "count": len(events[:limit]), "events": events[:limit],
            "more": more}


@tool("일정 조회")
def list_calendar_events(days_ahead: int = 7, days_back: int = 0, keyword: str = "") -> dict:
    """구글 캘린더(기본 캘린더)의 일정을 조회합니다.

    Args:
        days_ahead: 오늘부터 며칠 뒤까지 볼지 (0이면 오늘만).
        days_back: 며칠 전부터 볼지 (지난 일정 확인용, 보통 0).
        keyword: 일정 제목/내용 검색어 (없으면 빈 문자열).
    """
    res = fetch_events(days_ahead, days_back, keyword, limit=50)
    if res.pop("more", False):
        res["note"] = "일정이 많아 앞의 50개만 가져왔어요. 기간을 줄이거나 keyword로 좁혀보세요."
    return res


@tool("일정 등록")
def create_calendar_event(title: str, start: str, end: str, description: str = "", location: str = "") -> dict:
    """구글 캘린더에 일정을 등록합니다. 사용자가 명확히 등록을 요청했을 때만 사용하세요.

    Args:
        title: 일정 제목.
        start: 시작 시각 "YYYY-MM-DDTHH:MM" (한국 시간). 하루 종일 일정이면 "YYYY-MM-DD".
        end: 종료 시각 "YYYY-MM-DDTHH:MM". 하루 종일 일정이면 다음 날짜 "YYYY-MM-DD".
        description: 메모 (선택).
        location: 장소 (선택).
    """
    svc = google_service("calendar", "v3")

    def to_field(v: str):
        v = v.strip()
        if len(v) == 10:
            datetime.strptime(v, "%Y-%m-%d")
            return {"date": v}
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ToolError(f"시각 형식이 잘못됐어요: {v} (예: 2026-10-15T14:00)")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return {"dateTime": dt.isoformat(), "timeZone": TZ}

    body = {"summary": title, "start": to_field(start), "end": to_field(end)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    e = svc.events().insert(calendarId="primary", body=body).execute()
    return {"created": True, "title": title, "link": e.get("htmlLink", "")}
