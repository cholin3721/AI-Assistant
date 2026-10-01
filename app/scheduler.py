"""비서가 '먼저' 하는 일들 (비서 프로그램이 켜져 있는 동안 1분마다 확인).

- 아침 브리핑      : 매일 정한 시각 (기본 08:00)
- 주간 회고        : 매주 정한 요일·시각 (기본 금 18:00)
- 마감 리마인더    : 캘린더 마감·할 일 마감을 D-3, D-1, 당일에 알림 (30분마다 확인, 08시 이후)
- 메일 일정 찾기   : 30분마다 새 메일 확인
- 공지 일정 찾기   : 6시간마다 새 학교 공지 확인
PC가 꺼져 있던 시간대의 브리핑은, 켜진 뒤 그날 안(정한 시각부터 10시간 이내)이면 한 번 보냅니다.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, google_auth, notify, store

KST = timezone(timedelta(hours=9))
DAYS = "월화수목금토일"
MAIL_EVERY, NOTICE_EVERY, REMIND_EVERY = 30 * 60, 6 * 3600, 30 * 60
DEADLINE_WORDS = ("마감", "제출", "신청", "접수", "기한", "due", "deadline")
DEFAULT = {"last_mail_scan": 0, "last_notice_scan": 0, "last_reminder": 0,
           "last_briefing": "", "last_weekly": "", "reminders_sent": {}, "results": {}}

BRIEF_PROMPT = """아침 브리핑을 만들어줘. 도구로 실제 데이터를 확인해서 아래 순서로 정리해.
1. 오늘 일정과 수업 (캘린더 + 시간표)
2. 안 읽은 메일 중 중요한 것 (최근 2일)
3. 답장해야 하는 메일, 답을 기다리는 메일 (있을 때만)
4. 3일 안에 다가오는 마감 (캘린더 마감 + 할 일)
5. 최근 2일 새 학교 공지 중 나에게 맞는 것
맨 앞에 짧은 인사 한 줄. 항목마다 2~4줄, 해당 내용이 없으면 그 항목은 생략. 링크는 [제목](URL)로.
구글이 연동되지 않았으면 1~4는 건너뛰고 학교 공지와 할 일만 정리해."""

WEEKLY_PROMPT = """이번 주 회고를 만들어줘. 도구로 실제 데이터를 확인해서:
1. 이번 주에 있었던 일정 (지난 7일 캘린더)
2. 이번 주 완료한 할 일 / 아직 남은 할 일
3. 다음 주 일정과 마감
4. 아직 답장 안 한 메일, 회신을 기다리는 메일
5. 다음 주 우선순위 추천 3가지 (마감·중요도 기준, 한 줄씩)
간결하게, 항목이 비면 생략."""

_mem = {"busy": False}


def _state() -> dict:
    return store.load("scheduler", DEFAULT)


def _set(**kw):
    def fn(d):
        for k, v in kw.items():
            if k == "results":
                d.setdefault("results", {}).update(v)
            else:
                d[k] = v
    store.update("scheduler", DEFAULT, fn)


def _result(job: str, msg: str):
    _set(results={job: {"at": time.time(), "message": msg}})


def _hm(v: str):
    try:
        h, m = map(int, str(v).split(":"))
        return h, m
    except ValueError:
        return 8, 0


def _ready() -> bool:
    return bool(config.load().get("gemini_api_key"))


# ---------- 작업들 ----------
def run_briefing(manual: bool = False, now: datetime | None = None) -> str:
    from . import agent
    now = now or datetime.now(KST)
    if not _ready():
        return "Gemini 키가 없어 브리핑을 만들 수 없어요."
    try:
        text = agent.run_task(BRIEF_PROMPT)
    except Exception as e:
        _result("briefing", f"실패: {e}")
        if manual:
            notify.add("system", "브리핑 실패", str(e))
        return str(e)
    notify.add("briefing", f"{now.month}월 {now.day}일 ({DAYS[now.weekday()]}) 아침 브리핑", text)
    if not manual:
        _set(last_briefing=now.strftime("%Y-%m-%d"))
    _result("briefing", "보냄")
    return text


def run_weekly(manual: bool = False, now: datetime | None = None) -> str:
    from . import agent
    now = now or datetime.now(KST)
    if not _ready():
        return "Gemini 키가 없어 회고를 만들 수 없어요."
    try:
        text = agent.run_task(WEEKLY_PROMPT)
    except Exception as e:
        _result("weekly", f"실패: {e}")
        return str(e)
    notify.add("weekly", f"{now.month}월 {now.day}일 주간 회고", text)
    if not manual:
        y, w, _ = now.isocalendar()
        _set(last_weekly=f"{y}-W{w}")
    _result("weekly", "보냄")
    return text


def _deadline_items(now: datetime) -> list:
    """[(날짜 'YYYY-MM-DD', 제목, 종류, 링크)]"""
    out = []
    if google_auth.get_credentials() is not None:
        from .tools.gcalendar import list_calendar_events
        res = list_calendar_events(days_ahead=3)
        for e in res.get("events", []):
            title = e.get("title", "")
            if e.get("all_day") or any(w in title.lower() for w in DEADLINE_WORDS):
                out.append((str(e.get("start", ""))[:10], title, "일정", e.get("link", "")))
    from . import todos
    limit = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    for t in todos.items():
        if t.get("due") and t["due"] <= limit:
            out.append((t["due"], t["title"], "할 일", t.get("link", "")))
    return out


def run_reminders(now: datetime | None = None, force: bool = False) -> list:
    now = now or datetime.now(KST)
    if now.hour < 8 and not force:
        return []
    st = _state()
    sent = st.get("reminders_sent", {})
    today = now.date()
    lines, keys = [], []
    for day, title, kind, link in _deadline_items(now):
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        left = (d - today).days
        if left not in (3, 1, 0):
            continue
        key = f"{kind}|{title}|{day}|{left}"
        if key in sent:
            continue
        label = "오늘" if left == 0 else f"D-{left}"
        when = f"{d.month}/{d.day}({DAYS[d.weekday()]})"
        t = f"[{title}]({link})" if link else title
        lines.append(f"- **{label}** {t} · {when} · {kind}")
        keys.append(key)
    if lines:
        notify.add("reminder", "마감 알림", "\n".join(lines))
        stamp = today.strftime("%Y-%m-%d")

        def fn(d):
            rs = d.setdefault("reminders_sent", {})
            for k in keys:
                rs[k] = stamp
            old = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            d["reminders_sent"] = {k: v for k, v in rs.items() if v >= old}
        store.update("scheduler", DEFAULT, fn)
    _result("reminders", f"{len(lines)}건 알림" if lines else "보낼 알림 없음")
    return lines


def _scan_mail():
    from . import schedule_finder
    try:
        res = schedule_finder.scan(days=3)
        msg = res["message"]
        if res["found"]:
            notify.add("schedule", "메일에서 새 일정 후보",
                       "\n".join(f"- {c['title']} · {c['start'].replace('T', ' ')}" for c in res["new"])
                       + "\n\n화면 왼쪽 「일정 후보」에서 확인 후 추가하세요.")
    except Exception as e:
        msg = f"실패: {e}"
    _result("mail_scan", msg)


def _scan_notices():
    from . import schedule_finder
    try:
        res = schedule_finder.scan_notices(days=3)
        msg = res["message"]
        if res["found"]:
            notify.add("schedule", "학교 공지에서 새 일정 후보",
                       "\n".join(f"- {c['title']} · {c['start'].replace('T', ' ')}" for c in res["new"])
                       + "\n\n화면 왼쪽 「일정 후보」에서 확인 후 추가하세요.")
    except Exception as e:
        msg = f"실패: {e}"
    _result("notice_scan", msg)


# ---------- 시계 ----------
def due_daily(now: datetime, hhmm: str, last: str) -> bool:
    h, m = _hm(hhmm)
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return last != now.strftime("%Y-%m-%d") and at <= now < at + timedelta(hours=10)


def due_weekly(now: datetime, weekday: int, hhmm: str, last: str) -> bool:
    if now.weekday() != int(weekday):
        return False
    h, m = _hm(hhmm)
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    y, w, _ = now.isocalendar()
    return last != f"{y}-W{w}" and at <= now < at + timedelta(hours=10)


def tick(now: datetime | None = None):
    now = now or datetime.now(KST)
    cfg = config.load()
    if not cfg.get("gemini_api_key"):
        return
    auto = cfg["automation"]
    st = _state()
    google_on = google_auth.get_credentials() is not None
    t = time.time()

    if auto.get("briefing") and due_daily(now, auto.get("briefing_time", "08:00"), st.get("last_briefing", "")):
        run_briefing(now=now)
    if auto.get("weekly") and due_weekly(now, auto.get("weekly_day", 4), auto.get("weekly_time", "18:00"),
                                         st.get("last_weekly", "")):
        run_weekly(now=now)
    if auto.get("reminders") and t - st.get("last_reminder", 0) >= REMIND_EVERY:
        _set(last_reminder=t)
        run_reminders(now)
    if cfg.get("auto_scan", True) and google_on and t - st.get("last_mail_scan", 0) >= MAIL_EVERY:
        _set(last_mail_scan=t)
        _scan_mail()
    if auto.get("notice_scan") and t - st.get("last_notice_scan", 0) >= NOTICE_EVERY:
        _set(last_notice_scan=t)
        _scan_notices()


def _loop():
    time.sleep(15)
    while True:
        if not _mem["busy"]:
            _mem["busy"] = True
            try:
                tick()
            except Exception as e:
                _result("scheduler", f"오류: {e}")
            finally:
                _mem["busy"] = False
        time.sleep(60)


def start():
    threading.Thread(target=_loop, daemon=True).start()


def run_now(job: str) -> str:
    """설정 화면의 '지금 받아보기' 버튼."""
    if job == "briefing":
        run_briefing(manual=True)
        return "브리핑을 알림함으로 보냈어요."
    if job == "weekly":
        run_weekly(manual=True)
        return "주간 회고를 알림함으로 보냈어요."
    if job == "reminders":
        lines = run_reminders(force=True)
        return f"마감 알림 {len(lines)}건을 보냈어요." if lines else "지금 알릴 마감(D-3·D-1·오늘)이 없어요."
    if job == "notice_scan":
        _scan_notices()
        return _state()["results"].get("notice_scan", {}).get("message", "")
    raise ValueError(job)


def status() -> dict:
    st = _state()
    return {"results": st.get("results", {}), "last_briefing": st.get("last_briefing", ""),
            "last_weekly": st.get("last_weekly", "")}
