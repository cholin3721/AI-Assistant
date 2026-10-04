"""안정성·보안 회귀 테스트 + 새 기능(휴일 인식·지난 마감·.ics·답변 중지) 테스트.
네트워크·Gemini 없이 동작해야 합니다.
실행:  프로젝트 폴더에서  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

# 실제 data/ 폴더를 건드리지 않도록 임시 폴더 사용 (app 을 import 하기 전에 정해야 함)
os.environ.setdefault("INHA_AI_DATA_DIR", tempfile.mkdtemp(prefix="inha-ai-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, store  # noqa: E402

KST = timezone(timedelta(hours=9))

EVENTS = [   # 학사일정 예시 (실제 홈페이지 표기와 같은 형태)
    {"title": "2026-2학기 개시(개강/입학)일", "start": "2026-08-31", "end": "2026-08-31", "busy": True, "url": ""},
    {"title": "추석 연휴", "start": "2026-09-24", "end": "2026-09-26", "busy": True, "url": ""},
    {"title": "개천절 대체휴일", "start": "2026-10-05", "end": "2026-10-05", "busy": False, "url": ""},
    {"title": "한글날", "start": "2026-10-09", "end": "2026-10-09", "busy": False, "url": ""},
    {"title": "중간종합평가", "start": "2026-10-19", "end": "2026-10-23", "busy": True, "url": ""},
    {"title": "2026-2학기 종강일", "start": "2026-12-11", "end": "2026-12-11", "busy": True, "url": ""},
]


def use_events(events=EVENTS):
    from app.tools import academic
    academic._cache.update(events=list(events), at=time.time(), failed_at=0.0, disk=True)


class DataDir(unittest.TestCase):
    def test_tests_do_not_touch_real_data(self):
        self.assertNotEqual(config.DATA_DIR, config.BASE_DIR / "data")


class ConfigSafety(unittest.TestCase):
    def test_concurrent_saves_do_not_lose_updates(self):
        config.save({"notice_keywords": []})
        def work(i):
            config.save({f"k{i}": i})
        ts = [threading.Thread(target=work, args=(i,)) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        cfg = config.load()
        self.assertEqual([cfg.get(f"k{i}") for i in range(20)], list(range(20)))

    def test_broken_config_is_kept_aside(self):
        config.save({"gemini_api_key": "KEEP-ME"})
        config.CONFIG_PATH.write_text('{"gemini_api_key": "KEEP-ME", "mod', encoding="utf-8")   # 쓰다 만 파일
        self.assertEqual(config.load()["gemini_api_key"], "")
        kept = list(config.DATA_DIR.glob("config.broken-*.json"))
        self.assertTrue(kept and "KEEP-ME" in kept[0].read_text(encoding="utf-8"))


class GoogleOffline(unittest.TestCase):
    """와이파이가 끊겼을 때 토큰 갱신 실패로 화면 전체가 죽지 않아야 한다."""
    class Creds:
        valid, expired, refresh_token = False, True, "r"

        def __init__(self, exc):
            self.exc = exc

        def refresh(self, request):
            raise self.exc

    def setUp(self):
        from app import google_auth
        self.ga = google_auth
        google_auth._state.update(error="", offline=False, retry_at=0.0)
        config.TOKEN_PATH.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.ga._creds_cache["creds"] = None
        self.ga._state.update(error="", offline=False, retry_at=0.0)
        if config.TOKEN_PATH.exists():
            config.TOKEN_PATH.unlink()

    def test_transport_error_is_temporary(self):
        from google.auth.exceptions import TransportError
        creds = self.Creds(TransportError("offline"))
        self.ga._creds_cache["creds"] = creds
        self.assertIsNone(self.ga.get_credentials())           # 예외 없이 None
        self.assertTrue(config.TOKEN_PATH.exists())            # 로그인 정보는 지우지 않음
        st = self.ga.status()
        self.assertTrue(st["offline"] and not st["connected"])
        with mock.patch.object(creds, "refresh", side_effect=AssertionError("바로 다시 시도하면 안 됨")):
            self.assertIsNone(self.ga.get_credentials())       # 잠시 동안은 재시도하지 않음

    def test_expired_login_disconnects_and_notifies_once(self):
        from google.auth.exceptions import RefreshError
        self.ga._creds_cache["creds"] = self.Creds(RefreshError("invalid_grant: Token has been expired or revoked."))
        with mock.patch("app.notify.add") as add:
            self.assertIsNone(self.ga.get_credentials())
            self.assertIsNone(self.ga.get_credentials())
        self.assertEqual(add.call_count, 1)
        self.assertFalse(config.TOKEN_PATH.exists())
        self.assertIn("만료", self.ga.status()["error"])


class SchedulerRetry(unittest.TestCase):
    """브리핑이 실패해도 매분 다시 시도하지 않는다."""
    def setUp(self):
        from app import scheduler
        self.sc = scheduler
        store.save("scheduler", dict(scheduler.DEFAULT))
        config.save({"gemini_api_key": "test-key"})

    def tearDown(self):
        config.save({"gemini_api_key": ""})

    def test_backoff_schedule(self):
        t0 = 1_000_000.0
        tries = {"briefing": {"slot": "S", "n": 1, "at": t0}}
        self.assertTrue(self.sc.may_try({}, "briefing", "S", t0))
        self.assertFalse(self.sc.may_try(tries, "briefing", "S", t0 + 60))        # 1분 뒤: 아직
        self.assertTrue(self.sc.may_try(tries, "briefing", "S", t0 + 5 * 60))     # 5분 뒤: 다시
        self.assertTrue(self.sc.may_try(tries, "briefing", "다른 회차", t0 + 1))
        tries["briefing"]["n"] = self.sc.MAX_TRIES
        self.assertFalse(self.sc.may_try(tries, "briefing", "S", t0 + 10 ** 6))   # 포기한 회차

    def test_failures_stop_after_max_tries(self):
        slot = "2026-10-05 08:00"
        with mock.patch("app.agent.run_task", side_effect=RuntimeError("한도 초과")) as run, \
                mock.patch("app.notify.add") as add:
            for _ in range(self.sc.MAX_TRIES):
                self.sc.run_briefing(slot=slot)
            self.assertEqual(run.call_count, self.sc.MAX_TRIES)
        st = store.load("scheduler", self.sc.DEFAULT)
        self.assertEqual(st["last_briefing"], slot)            # 이 회차는 끝난 것으로 표시
        self.assertEqual(add.call_count, 1)                    # 실패 안내는 한 번만
        self.assertFalse(self.sc.may_try(st["attempts"], "briefing", slot, time.time() + 10 ** 6))

    def test_tick_does_not_retry_every_minute(self):
        now = datetime(2026, 10, 5, 8, 1, tzinfo=KST)
        config.save({"automation": {"weekly": False, "reminders": False, "notice_scan": False}, "auto_scan": False})
        with mock.patch("app.agent.run_task", side_effect=RuntimeError("429")) as run, \
                mock.patch("app.notify.add"), mock.patch("app.google_auth.get_credentials", return_value=None):
            self.sc.tick(now)
            self.sc.tick(now + timedelta(minutes=1))
            self.sc.tick(now + timedelta(minutes=2))
        self.assertEqual(run.call_count, 1)

    def test_manual_run_reports_failure(self):
        with mock.patch("app.agent.run_task", side_effect=RuntimeError("키 오류")), mock.patch("app.notify.add"):
            with self.assertRaises(self.sc.JobError):
                self.sc.run_now("briefing")

    def test_bad_time_values_do_not_crash(self):
        now = datetime(2026, 10, 5, 9, 0, tzinfo=KST)
        self.assertEqual(self.sc._hm("25:99"), (8, 0))
        self.sc.due_daily(now, "25:99", "")
        self.sc.due_weekly(now, "금", "aa:bb", "")


class Overdue(unittest.TestCase):
    def test_overdue_lines(self):
        from app.scheduler import overdue_lines
        today = date(2026, 10, 5)
        items = [{"id": "a", "title": "보고서", "due": "2026-10-04", "link": ""},          # 1일 지남 → 알림
                 {"id": "b", "title": "신청서", "due": "2026-10-03", "link": ""},          # 2일 지남 → 조용히
                 {"id": "c", "title": "과제", "due": "2026-10-02", "link": "https://x.y"},  # 3일 지남 → 알림
                 {"id": "d", "title": "내일 것", "due": "2026-10-06", "link": ""},
                 {"id": "e", "title": "마감 없음", "due": "", "link": ""}]
        lines, keys = overdue_lines(items, today, {})
        self.assertEqual(len(lines), 2)
        self.assertIn("1일 지남", lines[0])
        self.assertIn("[과제](https://x.y)", lines[1])
        again, _ = overdue_lines(items, today, {k: "2026-10-05" for k in keys})
        self.assertEqual(again, [])                            # 같은 날 두 번 알리지 않음

    def test_run_reminders_sends_overdue_as_private_kind(self):
        from app import scheduler, todos
        store.save("todos", {"items": []})
        store.save("scheduler", dict(scheduler.DEFAULT))
        todos.add("지난 과제", due="2026-10-04")
        use_events([])
        with mock.patch("app.notify.add") as add, mock.patch("app.google_auth.get_credentials", return_value=None), \
                mock.patch("app.tools.academic.fetch", return_value=[]):
            scheduler.run_reminders(datetime(2026, 10, 5, 9, 0, tzinfo=KST))
            scheduler.run_reminders(datetime(2026, 10, 5, 9, 30, tzinfo=KST))
        kinds = [c.args[0] for c in add.call_args_list]
        self.assertEqual(kinds, ["overdue"])                   # 팀 채널로 나가는 'reminder'가 아님, 한 번만


class CandidateStore(unittest.TestCase):
    """스캔이 도는 동안 사용자가 누른 「무시」가 스캔이 끝나며 되돌아가면 안 된다."""
    def cand(self, cid, status="pending"):
        return {"id": cid, "status": status, "title": cid, "start": "2030-01-01", "end": "2030-01-02", "source": "mail"}

    def setUp(self):
        from app import schedule_finder
        self.sf = schedule_finder
        store.save("schedule", dict(schedule_finder.DEFAULT))

    def test_user_decision_survives_a_running_scan(self):
        self.sf._commit({"a": self.cand("a")})
        snapshot = self.sf._load()                       # 스캔이 시작하며 읽어 둔 사본
        self.sf.ignore("a")                              # 그 사이 사용자가 「무시」
        snapshot["candidates"]["b"] = self.cand("b")     # 스캔이 새 후보를 찾음
        self.sf._commit({"a": snapshot["candidates"]["a"], "b": snapshot["candidates"]["b"]}, processed={"m1": 1.0})
        data = self.sf._load()
        self.assertEqual(data["candidates"]["a"]["status"], "ignored")
        self.assertEqual(data["candidates"]["b"]["status"], "pending")
        self.assertIn("m1", data["processed"])

    def test_two_scans_merge(self):
        self.sf._commit({"m": self.cand("m")}, processed={"mail-1": 1.0})
        self.sf._commit({"n": self.cand("n")}, processed_notices={"https://www.inhatc.ac.kr/n1": 2.0})
        data = self.sf._load()
        self.assertEqual(set(data["candidates"]), {"m", "n"})
        self.assertTrue(data["processed"] and data["processed_notices"])

    def test_unknown_candidate(self):
        with self.assertRaises(self.sf.ScanError):
            self.sf.ignore("nope")


class StatsCounting(unittest.TestCase):
    """리포트의 '아낀 시간'은 대화에서 AI가 직접 쓴 도구만 센다."""
    def count(self, name):
        day = store.load("stats", {"days": {}}).get("days", {})
        return sum(b.get(f"tool:{name}", 0) for b in day.values())

    def test_background_and_nested_calls_are_not_counted(self):
        from app import tools

        @tools.tool("안쪽")
        def inner_probe():
            return {"ok": 1}

        @tools.tool("바깥쪽")
        def outer_probe():
            return inner_probe()

        tools.end_log()
        outer_probe()                                    # 자동 확인(대화 밖) → 세지 않음
        self.assertEqual((self.count("outer_probe"), self.count("inner_probe")), (0, 0))
        tools.start_log(count_stats=False)               # 자동 브리핑 → 세지 않음
        outer_probe()
        self.assertEqual(self.count("outer_probe"), 0)
        tools.start_log()                                # 사용자와의 대화
        outer_probe()
        self.assertEqual([t["name"] for t in tools.get_log()], ["outer_probe", "inner_probe"])
        tools.end_log()
        self.assertEqual((self.count("outer_probe"), self.count("inner_probe")), (1, 0))   # 안쪽은 중복으로 세지 않음
        outer_probe()                                    # 대화가 끝난 뒤 같은 스레드에서 → 세지 않음
        self.assertEqual(self.count("outer_probe"), 1)

    def test_stop_skips_remaining_tools(self):
        from app import tools
        called = []

        @tools.tool("탐침")
        def stop_probe():
            called.append(1)
            return {"ok": 1}

        tools.start_log(should_stop=lambda: True)
        res = stop_probe()
        tools.end_log()
        self.assertIn("error", res)
        self.assertEqual(called, [])


class LinkCode(unittest.TestCase):
    """연결 코드는 '정확히 그 숫자'일 때만 맞고, 여러 번 틀리면 새 코드로 바뀐다."""
    def test_matches(self):
        from app import linkcode
        self.assertTrue(linkcode.matches("123456", "코드는 123456 입니다"))
        self.assertTrue(linkcode.matches("123456", " 123456 "))
        self.assertFalse(linkcode.matches("123456", "000000123456999999"))     # 긴 숫자열에 섞어 보내기
        self.assertFalse(linkcode.matches("123456", "111111 123456"))          # 여러 개를 한꺼번에
        self.assertFalse(linkcode.matches("", ""))
        self.assertRegex(linkcode.new_code(), r"^\d{6}$")

    def _discord(self, cfg):
        from app import discord_bot
        discord_bot._attempts.wrong = 0
        saved = {}
        return discord_bot, saved, mock.patch.object(config, "load", lambda: {"discord": cfg}), \
            mock.patch.object(config, "save", lambda u: (saved.update(u), cfg.update(u["discord"])))

    def test_discord_rejects_digit_flood_and_rotates(self):
        from app import discord_bot
        cfg = {**discord_bot.EMPTY, "link_code": "123456"}
        bot, saved, p1, p2 = self._discord(cfg)
        with p1, p2:
            bot.handle_dm("666", "남", "00000012345699")
            self.assertIsNone(cfg.get("owner_id"))
            bot.handle_dm("666", "남", "안녕하세요")               # 숫자 없는 말은 시도로 세지 않음
            for guess in ("000001", "000002", "000003", "000004"):
                replies, _ = bot.handle_dm("666", "남", guess)
            self.assertNotEqual(cfg["link_code"], "123456")        # 5번 틀림 → 새 코드
            self.assertIn("새 코드", replies[0])
            self.assertIsNone(cfg.get("owner_id"))
            bot.handle_dm("42", "주인", cfg["link_code"])           # 화면의 새 코드로는 연결됨
            self.assertEqual(cfg["owner_id"], "42")

    def test_telegram_link_step(self):
        from app import telegram_bot
        telegram_bot._attempts.wrong = 0
        t = {"token": "x", "link_code": "654321", "chat_id": None}
        with mock.patch.object(config, "save", lambda u: t.update(u["telegram"])):
            self.assertNotEqual(telegram_bot.link_step(t, 9, "/start 111111654321"), "linked")
            self.assertIsNone(t["chat_id"])
            self.assertEqual(telegram_bot.link_step(t, 7, "/start 654321", "나"), "linked")
            self.assertEqual(t["chat_id"], 7)


class SchoolUrl(unittest.TestCase):
    def test_host_is_checked_not_prefix(self):
        from app.tools.notices import is_school_url
        ok = ["https://www.inhatc.ac.kr/bbs/kr/11/1/artclView.do", "https://cms.inhatc.ac.kr/x", "https://www.inhatc.ac.kr:443/a"]
        bad = ["https://www.inhatc.ac.kr.evil.example/x", "https://www.inhatc.ac.kr@evil.example/x",
               "http://www.inhatc.ac.kr/x", "https://evil.example/www.inhatc.ac.kr", "https://xinhatc.ac.kr/",
               "https://www.inhatc.ac.kr:8443/x", "javascript:alert(1)", "", "https://[bad"]
        for u in ok:
            self.assertTrue(is_school_url(u), u)
        for u in bad:
            self.assertFalse(is_school_url(u), u)

    def test_tools_refuse_lookalike(self):
        from app.tools import notices
        with mock.patch("requests.get", side_effect=AssertionError("접속하면 안 됨")):
            self.assertIn("error", notices.read_school_notice(url="https://www.inhatc.ac.kr.evil.example/a"))
            self.assertIn("error", notices.read_notice_attachment(url="https://www.inhatc.ac.kr@evil.example/a"))


class TodoLink(unittest.TestCase):
    def test_only_http_links(self):
        from app import todos
        store.save("todos", {"items": []})
        self.assertEqual(todos.add("a", link="javascript:alert(1)")["link"], "")
        self.assertEqual(todos.add("b", link="https://www.inhatc.ac.kr/x")["link"], "https://www.inhatc.ac.kr/x")
        self.assertEqual(todos.add("c", link="https://a.b/c d")["link"], "")


class HolidayAware(unittest.TestCase):
    def setUp(self):
        use_events()

    def test_day_info(self):
        from app.tools import academic
        self.assertEqual(academic.day_info("2026-10-05")["holiday"], "개천절 대체휴일")
        self.assertEqual(academic.day_info(date(2026, 9, 25))["holiday"], "추석 연휴")
        self.assertEqual(academic.day_info("2026-10-20"), {"holiday": "", "exam": "중간종합평가"})
        self.assertEqual(academic.day_info("2026-10-06"), {"holiday": "", "exam": ""})
        self.assertEqual(sorted(academic.no_class_days(date(2026, 10, 1), date(2026, 10, 10))),
                         [date(2026, 10, 5), date(2026, 10, 9)])

    def test_semester_range(self):
        from app.tools import academic
        self.assertEqual(academic.semester_range(date(2026, 10, 4)), (date(2026, 8, 31), date(2026, 12, 11)))
        self.assertIsNone(academic.semester_range(date(2026, 12, 20)))

    def test_today_block_says_no_class_on_holiday(self):
        from app import timetable
        store.save("timetable", {"classes": [
            {"id": "1", "day": 0, "start": "09:00", "end": "10:45", "title": "전공", "place": "", "prof": ""},
            {"id": "2", "day": 1, "start": "13:35", "end": "15:20", "title": "교양", "place": "", "prof": ""}]})
        real = datetime

        class Sunday(datetime):
            @classmethod
            def now(cls, tz=None):
                return real(2026, 10, 4, 20, 0, tzinfo=tz)   # 일요일 저녁 → 내일(월)은 대체휴일
        with mock.patch("app.timetable.datetime", Sunday):
            block = timetable.today_block()
        self.assertIn("개천절 대체휴일", block)
        self.assertNotIn("전공", block)

        class Monday(datetime):
            @classmethod
            def now(cls, tz=None):
                return real(2026, 10, 5, 9, 0, tzinfo=tz)
        with mock.patch("app.timetable.datetime", Monday):
            s = timetable.summary()
            self.assertEqual((s["today"], s["holiday"]), ([], "개천절 대체휴일"))
            self.assertIn("교양", timetable.today_block())   # 내일(화)은 정상 수업

    def test_free_slots_ignore_classes_on_holiday(self):
        from app.timetable import free_slots
        now = datetime(2026, 10, 5, 8, 0, tzinfo=KST)        # 월요일(대체휴일)
        classes = [{"day": 0, "start": "09:00", "end": "17:00", "title": "전공"}]
        busy_day = free_slots(0, 120, "09:00", "18:00", False, [], classes, now)
        free_day = free_slots(0, 120, "09:00", "18:00", False, [], classes, now, no_class_days={date(2026, 10, 5)})
        self.assertEqual(busy_day, [])
        self.assertEqual([(s.hour, e.hour) for s, e in free_day], [(9, 18)])

    def test_fetch_falls_back_without_hanging(self):
        from app.tools import academic
        import requests
        academic._cache.update(events=[], at=0.0, failed_at=0.0, disk=True)
        with mock.patch("requests.get", side_effect=requests.ConnectionError("offline")) as get:
            with self.assertRaises(academic.ToolError):
                academic.fetch()
            with self.assertRaises(academic.ToolError):
                academic.fetch()                             # 실패 직후에는 다시 접속하지 않음
            self.assertEqual(get.call_count, 1)
        use_events()


class IcsExport(unittest.TestCase):
    def test_timetable(self):
        from app import ics
        classes = [{"day": 0, "start": "09:00", "end": "10:45", "title": "생성형AI프로그래밍; 실습, 이론", "place": "7호관 301호", "prof": "이원주"},
                   {"day": 4, "start": "13:35", "end": "15:20", "title": "캡스톤디자인" * 8, "place": "", "prof": ""}]
        text = ics.timetable_ics(classes, date(2026, 8, 31), date(2026, 12, 11),
                                 {date(2026, 10, 5): "대체휴일", date(2026, 10, 9): "한글날"},
                                 now=datetime(2026, 10, 4, tzinfo=timezone.utc))
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(text.count("BEGIN:VEVENT"), 2)
        self.assertIn("DTSTART;TZID=Asia/Seoul:20260831T090000", text)          # 개강일(월)부터
        self.assertIn("DTSTART;TZID=Asia/Seoul:20260904T133500", text)          # 첫 금요일
        self.assertIn("RRULE:FREQ=WEEKLY;UNTIL=20261211T145959Z", text)         # 종강일까지
        self.assertIn("EXDATE;TZID=Asia/Seoul:20261005T090000", text)           # 월요일 휴일 제외
        self.assertIn("EXDATE;TZID=Asia/Seoul:20261009T133500", text)           # 금요일 휴일 제외
        self.assertIn("SUMMARY:생성형AI프로그래밍\\; 실습\\, 이론", text)
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)           # 줄 접기
        self.assertIn("캡스톤디자인" * 8, text.replace("\r\n ", ""))             # 접힌 줄을 이으면 원래 글자

    def test_todos(self):
        from app import ics
        text = ics.todos_ics([{"id": "a", "title": "출품보고서", "due": "2026-11-03", "note": "양식 확인\n2쪽", "link": "https://x.y/z"},
                              {"id": "b", "title": "마감 없음", "due": ""}])
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)
        self.assertIn("DTSTART;VALUE=DATE:20261103", text)
        self.assertIn("DTEND;VALUE=DATE:20261104", text)
        self.assertIn("DESCRIPTION:양식 확인\\n2쪽\\nhttps://x.y/z", text)

    def test_default_range(self):
        from app import ics
        s, e = ics.default_range(date(2026, 10, 7))          # 수요일
        self.assertEqual((s, (e - s).days), (date(2026, 10, 5), 16 * 7 - 1))


class StopAnswer(unittest.TestCase):
    """「중지」를 누르면 그때까지 쓴 글로 답을 끝낸다."""
    class FakeChat:
        def __init__(self, on_second):
            self.on_second = on_second

        def send_message_stream(self, message, config=None):
            for i, piece in enumerate(["첫 조각 ", "둘째 조각 ", "셋째 조각"]):
                if i == 1:
                    self.on_second()
                yield type("Chunk", (), {"text": piece})()

    def test_stop_mid_stream(self):
        from app import agent, chats
        config.save({"gemini_api_key": "test-key", "model": "m"})
        sid = "stop-test"
        sess = {"key": "test-key", "model": "m", "client": None, "turns": 0, "lock": threading.Lock(),
                "chat": self.FakeChat(lambda: self.assertTrue(agent.request_stop(sid)))}
        events = []
        try:
            with mock.patch.object(agent, "_session_for", return_value=(sess, "")), \
                    mock.patch.object(agent, "_config", return_value=None):
                res = agent.chat(sid, "길게 설명해줘", on_event=events.append)
        finally:
            config.save({"gemini_api_key": "", "model": ""})
        self.assertTrue(res["stopped"])
        self.assertTrue(res["reply"].startswith("첫 조각"))
        self.assertNotIn("셋째", res["reply"])
        self.assertIn("중지", res["reply"])
        self.assertEqual([m["role"] for m in chats.get(sid)], ["user", "bot"])   # 중지한 답도 기록에 남음
        self.assertFalse(agent.request_stop(sid))                                # 끝난 뒤에는 멈출 것이 없음
        self.assertNotIn(sid, agent._stop)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import main
        cls.c = TestClient(main.app, base_url="http://127.0.0.1:8765")
        use_events()

    def test_settings_validation(self):
        bad = [{"briefing_time": "25:99"}, {"weekly_day": "금"}, {"weekly_day": 9}, {"briefing": "yes"}]
        for a in bad:
            self.assertEqual(self.c.post("/api/settings", json={"automation": a}).status_code, 400, a)
        self.assertEqual(self.c.post("/api/settings", json={"automation": {"briefing_time": "7:30", "weekly_day": 2,
                                                                          "reminders": False, "unknown": 1}}).status_code, 200)
        auto = config.load()["automation"]
        self.assertEqual((auto["briefing_time"], auto["weekly_day"], auto["reminders"]), ("07:30", 2, False))
        self.assertNotIn("unknown", auto)

    def test_local_only(self):
        from fastapi.testclient import TestClient
        from app import main
        self.assertEqual(TestClient(main.app, base_url="http://evil.example").get("/api/status").status_code, 403)
        self.assertEqual(self.c.post("/api/todos", json={"title": "x"}, headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_status_works_when_google_refresh_fails(self):
        from google.auth.exceptions import TransportError
        from app import google_auth
        creds = GoogleOffline.Creds(TransportError("offline"))
        google_auth._creds_cache["creds"] = creds
        google_auth._state.update(retry_at=0.0)
        try:
            r = self.c.get("/api/status")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["google"]["offline"])
        finally:
            google_auth._creds_cache["creds"] = None
            google_auth._state.update(error="", offline=False, retry_at=0.0)

    def test_stop_and_ics_endpoints(self):
        self.assertEqual(self.c.post("/api/chat/stop", json={"session_id": "none"}).json(), {"ok": True, "running": False})
        store.save("todos", {"items": []})
        store.save("timetable", {"classes": []})
        self.assertEqual(self.c.get("/api/todos/ics").status_code, 400)
        self.assertEqual(self.c.get("/api/timetable/ics").status_code, 400)
        self.c.post("/api/todos", json={"title": "출품보고서", "due": "2026-11-03"})
        self.c.put("/api/timetable", json={"classes": [{"day": 0, "start": "09:00", "end": "10:45", "title": "전공"}]})
        with mock.patch("app.tools.academic.fetch", return_value=EVENTS):
            r = self.c.get("/api/timetable/ics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/calendar", r.headers["content-type"])
        self.assertIn("RRULE:FREQ=WEEKLY", r.text)
        r = self.c.get("/api/todos/ics")
        self.assertIn("SUMMARY:[할 일] 출품보고서", r.text)


if __name__ == "__main__":
    unittest.main()
