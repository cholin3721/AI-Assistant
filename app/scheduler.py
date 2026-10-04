"""비서가 '먼저' 하는 일들 (비서 프로그램이 켜져 있는 동안 1분마다 확인).

- 아침 브리핑      : 매일 정한 시각 (기본 08:00)
- 주간 회고        : 매주 정한 요일·시각 (기본 금 18:00)
- 마감 리마인더    : 캘린더 마감·할 일 마감을 D-3, D-1, 당일에 알림 (30분마다 확인, 08시 이후)
- 지난 마감        : 기한이 지난 할 일을 1·3·7·14일째에 한 번씩 알림
- 메일 일정 찾기   : 30분마다 새 메일 확인
- 공지 일정 찾기   : 6시간마다 새 학교 공지 확인
PC가 꺼져 있던 시간대의 브리핑은, 켜진 뒤 그날 안(정한 시각부터 10시간 이내)이면 한 번 보냅니다.
브리핑·회고가 실패하면(무료 한도 초과 등) 5분·15분·30분 뒤에만 다시 해 보고, 그래도 안 되면 그 회차는 포기하고 알려줍니다.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, google_auth, notify, store

KST = timezone(timedelta(hours=9))
DAYS = "월화수목금토일"
MAIL_EVERY, NOTICE_EVERY, REMIND_EVERY = 30 * 60, 6 * 3600, 30 * 60
DEADLINE_WORDS = ("마감", "제출", "신청", "접수", "기한", "due", "deadline")
DEFAULT = {"last_mail_scan": 0, "last_notice_scan": 0, "last_reminder": 0,
           "last_briefing": "", "last_weekly": "", "reminders_sent": {}, "results": {}, "attempts": {}}
RETRY_WAIT = (5 * 60, 15 * 60, 30 * 60)   # 실패 뒤 다시 해 보기까지 기다리는 시간 (1·2·3번째 실패 뒤)
MAX_TRIES = len(RETRY_WAIT) + 1           # 한 회차에 최대 4번
OVERDUE_DAYS = (1, 3, 7, 14)              # 기한이 지난 할 일을 다시 알려주는 날
log = logging.getLogger("inha")


class JobError(Exception):
    """「지금 받아보기」가 실패했을 때 화면에 그대로 보여줄 안내."""

BRIEF_PROMPT = """아침 브리핑을 만들어줘. 도구로 실제 데이터를 확인해서 아래 순서로 정리해.
1. 오늘 일정과 수업 (캘린더 + 시간표). [수업]에 휴일·시험기간 표시가 있으면 그대로 따르고, 휴일에 수업이 있다고 말하지 마
2. 다가오는 학사일정 (수강신청·시험·등록금 등, get_academic_calendar)
3. 안 읽은 메일 중 중요한 것 (최근 2일)
4. 답장해야 하는 메일, 답을 기다리는 메일 (있을 때만)
5. 3일 안에 다가오는 마감 (캘린더 마감 + 할 일)
6. 최근 2일 새 학교 공지 중 나에게 맞는 것
7. (디스코드 팀 채널 대화 읽기가 켜져 있을 때만) 어제부터 팀 채널에서 결정된 것과 내가 맡은 일 (read_team_chat)
맨 앞에 짧은 인사 한 줄. 항목마다 2~4줄, 해당 내용이 없으면 그 항목은 생략. 링크는 [제목](URL)로.
구글이 연동되지 않았으면 메일·캘린더 항목은 건너뛰고 학사일정·학교 공지·할 일만 정리해."""

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


def _hm(v: str, default=(8, 0)):
    try:
        h, m = map(int, str(v).split(":"))
    except ValueError:
        return default
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else default


def _ready() -> bool:
    return bool(config.load().get("gemini_api_key"))


# ---------- 실패한 회차 다시 해 보기 ----------
def may_try(attempts: dict, job: str, slot: str, now_ts: float) -> bool:
    """이 회차(slot)를 지금 시도해도 되는지. 실패 직후 매분 다시 시도해 무료 한도를 태우지 않게 간격을 둡니다."""
    a = (attempts or {}).get(job) or {}
    if a.get("slot") != slot:
        return True
    n = int(a.get("n", 0))
    if n <= 0:
        return True
    if n >= MAX_TRIES:
        return False
    return now_ts - float(a.get("at", 0)) >= RETRY_WAIT[n - 1]


def _note_failure(job: str, slot: str, now_ts: float) -> int:
    """실패 횟수를 적고, 몇 번째 실패인지 돌려줌."""
    def fn(d):
        a = d.setdefault("attempts", {}).get(job) or {}
        n = int(a.get("n", 0)) + 1 if a.get("slot") == slot else 1
        d["attempts"][job] = {"slot": slot, "n": n, "at": now_ts}
        return n
    return store.update("scheduler", DEFAULT, fn)


def _clear_attempts(job: str):
    def fn(d):
        d.setdefault("attempts", {}).pop(job, None)
    store.update("scheduler", DEFAULT, fn)


# ---------- 작업들 ----------
JOBS = {
    "briefing": {"prompt": BRIEF_PROMPT, "kind": "briefing", "name": "아침 브리핑", "last": "last_briefing",
                 "title": lambda now: f"{now.month}월 {now.day}일 ({DAYS[now.weekday()]}) 아침 브리핑"},
    "weekly": {"prompt": WEEKLY_PROMPT, "kind": "weekly", "name": "주간 회고", "last": "last_weekly",
               "title": lambda now: f"{now.month}월 {now.day}일 주간 회고"},
}


def _run_job(job: str, manual: bool, now: datetime | None, slot: str) -> tuple:
    """(성공했는지, 글 또는 실패 사유)."""
    from . import agent
    spec = JOBS[job]
    now = now or datetime.now(KST)
    if not _ready():
        return False, f"Gemini 키가 없어 {spec['name']}을(를) 만들 수 없어요."
    try:
        text = agent.run_task(spec["prompt"])
    except Exception as e:
        reason = str(e)
        _result(job, f"실패: {reason}")
        log.warning("%s 실패: %s", job, reason[:300])
        if manual:
            notify.add("system", f"{spec['name']} 실패", reason)
        elif slot:
            n = _note_failure(job, slot, time.time())
            if n >= MAX_TRIES:   # 더 시도하지 않고 이번 회차는 끝냄 (한 번만 알림)
                _set(**{spec["last"]: slot})
                notify.add("system", f"{spec['name']}을(를) 만들지 못했어요",
                           f"{n}번 시도했지만 실패했어요: {reason}\n\n설정 > 자동 알림의 「지금 받아보기」로 다시 해볼 수 있어요.")
        return False, reason
    notify.add(spec["kind"], spec["title"](now), text)
    if not manual and slot:
        _set(**{spec["last"]: slot})
    _clear_attempts(job)
    _result(job, "보냄")
    return True, text


def run_briefing(manual: bool = False, now: datetime | None = None, slot: str = "") -> str:
    now = now or datetime.now(KST)
    return _run_job("briefing", manual, now, slot or ("" if manual else now.strftime("%Y-%m-%d")))[1]


def run_weekly(manual: bool = False, now: datetime | None = None, slot: str = "") -> str:
    now = now or datetime.now(KST)
    y, w, _ = now.isocalendar()
    return _run_job("weekly", manual, now, slot or ("" if manual else f"{y}-W{w}"))[1]


def _deadline_items(now: datetime) -> list:
    """[(날짜 'YYYY-MM-DD', 제목, 종류, 링크)]"""
    out = []
    if google_auth.get_credentials() is not None:
        try:
            from .tools.gcalendar import fetch_events
            for e in fetch_events(days_ahead=3, limit=250)["events"]:
                title = e.get("title", "")
                if e.get("all_day") or any(w in title.lower() for w in DEADLINE_WORDS):
                    out.append((str(e.get("start", ""))[:10], title, "일정", e.get("link", "")))
        except Exception as e:   # 캘린더를 못 읽어도 할 일·학사일정 알림은 계속
            log.warning("마감 알림: 캘린더를 읽지 못함 (%s)", type(e).__name__)
    from . import todos
    limit = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    for t in todos.items():
        if t.get("due") and today <= t["due"] <= limit:
            out.append((t["due"], t["title"], "할 일", t.get("link", "")))
    try:
        from .tools.academic import upcoming
        for e in upcoming(3):
            if any(w in e["title"] for w in ("마감", "신청", "등록", "제출", "평가", "시험")):
                out.append((e["end"], e["title"], "학사일정", e.get("url", "")))
    except Exception:
        pass
    return out


def overdue_lines(items: list, today, sent: dict) -> tuple:
    """기한이 지난 할 일 → (알림 줄 목록, 보냈다고 적을 키 목록). 1·3·7·14일째에만 한 번씩."""
    lines, keys = [], []
    for t in items:
        try:
            d = datetime.strptime(t.get("due") or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        late = (today - d).days
        if late not in OVERDUE_DAYS:
            continue
        key = f"지남|{t['id']}|{late}"
        if key in sent:
            continue
        title = f"[{t['title']}]({t['link']})" if t.get("link") else t["title"]
        lines.append(f"- **{late}일 지남** {title} · 마감 {d.month}/{d.day}({DAYS[d.weekday()]})")
        keys.append(key)
    return lines, keys


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
    from . import todos
    late_lines, late_keys = overdue_lines(todos.items(), today, sent)
    if late_lines:   # 내 할 일이므로 팀 채널에는 보내지 않는 종류(overdue)로
        notify.add("overdue", "기한이 지난 할 일", "\n".join(late_lines)
                   + "\n\n이미 끝냈다면 화면 왼쪽 「할 일」에서 체크해 주세요.")
    if keys or late_keys:
        stamp = today.strftime("%Y-%m-%d")

        def fn(d):
            rs = d.setdefault("reminders_sent", {})
            for k in keys + late_keys:
                rs[k] = stamp
            old = (today - timedelta(days=21)).strftime("%Y-%m-%d")
            d["reminders_sent"] = {k: v for k, v in rs.items() if v >= old}
        store.update("scheduler", DEFAULT, fn)
    total = len(lines) + len(late_lines)
    _result("reminders", f"{total}건 알림" if total else "보낼 알림 없음")
    return lines + late_lines


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
def due_daily(now: datetime, hhmm: str, last: str):
    """(보낼지, 슬롯id). 시각을 놓쳤어도 10시간 안이면 한 번 보냄. 자정을 넘어도 됨."""
    h, m = _hm(hhmm)
    window = timedelta(hours=10)
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    for sched in (at, at - timedelta(days=1)):
        slot = f"{sched:%Y-%m-%d} {h:02d}:{m:02d}"
        if last not in (slot, slot[:10]) and sched <= now < sched + window:
            return True, slot
    return False, ""


def due_weekly(now: datetime, weekday: int, hhmm: str, last: str):
    h, m = _hm(hhmm, (18, 0))
    try:
        weekday = int(weekday) % 7
    except (TypeError, ValueError):
        weekday = 4
    window = timedelta(hours=10)
    this = now.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(days=(now.weekday() - weekday) % 7)
    for sched in (this, this - timedelta(days=7)):
        y, w, _ = sched.isocalendar()
        slot = f"{y}-W{w}"
        if last != slot and sched <= now < sched + window:
            return True, slot
    return False, ""


def _guard(name: str, fn, *args, **kw):
    """작업 하나가 실패해도 같은 분의 다른 작업은 계속 돌도록."""
    try:
        return fn(*args, **kw)
    except Exception as e:
        log.exception("자동 작업 오류 (%s)", name)
        _result(name, f"오류: {type(e).__name__}: {str(e)[:200]}")


def tick(now: datetime | None = None):
    now = now or datetime.now(KST)
    cfg = config.load()
    if not cfg.get("gemini_api_key"):
        return
    auto = cfg["automation"]
    st = _state()
    google_on = google_auth.get_credentials() is not None
    t = time.time()
    tries = st.get("attempts", {})

    brief_ok, brief_slot = due_daily(now, auto.get("briefing_time", "08:00"), st.get("last_briefing", ""))
    if auto.get("briefing") and brief_ok and may_try(tries, "briefing", brief_slot, t):
        _guard("briefing", run_briefing, now=now, slot=brief_slot)
    week_ok, week_slot = due_weekly(now, auto.get("weekly_day", 4), auto.get("weekly_time", "18:00"),
                                    st.get("last_weekly", ""))
    if auto.get("weekly") and week_ok and may_try(tries, "weekly", week_slot, t):
        _guard("weekly", run_weekly, now=now, slot=week_slot)
    if auto.get("reminders") and t - st.get("last_reminder", 0) >= REMIND_EVERY:
        _set(last_reminder=t)
        _guard("reminders", run_reminders, now)
    if cfg.get("auto_scan", True) and google_on and t - st.get("last_mail_scan", 0) >= MAIL_EVERY:
        _set(last_mail_scan=t)
        _guard("mail_scan", _scan_mail)
    if auto.get("notice_scan") and t - st.get("last_notice_scan", 0) >= NOTICE_EVERY:
        _set(last_notice_scan=t)
        _guard("notice_scan", _scan_notices)


def _loop():
    time.sleep(15)
    try:   # 학사일정을 미리 받아 둠 (휴일 표시·브리핑이 첫 화면부터 맞도록)
        from .tools import academic
        academic.fetch()
    except Exception:
        pass
    while True:
        if not _mem["busy"]:
            _mem["busy"] = True
            try:
                tick()
            except Exception as e:
                log.exception("스케줄러 오류")
                _result("scheduler", f"오류: {e}")
            finally:
                _mem["busy"] = False
        time.sleep(60)


def start():
    threading.Thread(target=_loop, daemon=True).start()


def run_now(job: str) -> str:
    """설정 화면의 '지금 받아보기' 버튼."""
    if job in JOBS:
        ok, text = _run_job(job, True, None, "")
        if not ok:
            raise JobError(text)
        return "브리핑을 알림함으로 보냈어요." if job == "briefing" else "주간 회고를 알림함으로 보냈어요."
    if job == "reminders":
        lines = run_reminders(force=True)
        return f"알림 {len(lines)}건을 보냈어요." if lines else "지금 알릴 마감(D-3·D-1·오늘, 지난 마감)이 없어요."
    if job == "notice_scan":
        _scan_notices()
        return _state()["results"].get("notice_scan", {}).get("message", "")
    raise ValueError(job)


def status() -> dict:
    st = _state()
    return {"results": st.get("results", {}), "last_briefing": st.get("last_briefing", ""),
            "last_weekly": st.get("last_weekly", "")}
