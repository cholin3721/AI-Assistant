"""순수 로직 단위 테스트. 네트워크·Gemini 없이 동작해야 합니다.
실행:  프로젝트 폴더에서  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import os
import struct
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KST = timezone(timedelta(hours=9))


class HwpPara(unittest.TestCase):
    def test_plain_and_newline(self):
        from app.attachments import _hwp_para_text
        buf = struct.pack("<5H", ord("안"), ord("녕"), 10, ord("A"), ord("B"))
        self.assertIn("안녕", _hwp_para_text(buf))
        self.assertIn("AB", _hwp_para_text(buf))

    def test_tab_control(self):
        from app.attachments import _hwp_para_text
        # 탭(9)은 8글자 크기 제어문자. 나머지 7칸은 더미.
        buf = struct.pack("<10H", ord("A"), 9, 0, 0, 0, 0, 0, 0, 0, ord("B"))
        self.assertEqual(_hwp_para_text(buf), "A\tB")


class Similar(unittest.TestCase):
    def test_same_and_subset(self):
        from app.schedule_finder import _similar
        self.assertTrue(_similar("AID 공모전 마감", "AID공모전마감"))
        self.assertTrue(_similar("캡스톤 중간발표", "캡스톤 중간 발표"))
        self.assertFalse(_similar("장학 신청", "수강 정정"))


class FreeSlots(unittest.TestCase):
    def test_gap_between_class_and_event(self):
        from app.timetable import free_slots
        now = datetime(2026, 10, 5, 8, 0, tzinfo=KST)  # 월요일
        classes = [{"day": 0, "start": "09:00", "end": "11:00", "title": "전공"}]
        busy = [(datetime(2026, 10, 5, 13, 0, tzinfo=KST),
                 datetime(2026, 10, 5, 14, 0, tzinfo=KST), "일정")]
        slots = free_slots(0, 60, "09:00", "18:00", False, busy, classes, now)
        labels = [f"{s:%H:%M}-{e:%H:%M}" for s, e in slots]
        self.assertTrue(any(x.startswith("11:00") for x in labels))

    def test_academic_busy_blocks_day(self):
        from app.timetable import free_slots
        now = datetime(2026, 10, 5, 8, 0, tzinfo=KST)
        extra = [(datetime(2026, 10, 5, 0, 0, tzinfo=KST),
                  datetime(2026, 10, 6, 0, 0, tzinfo=KST))]
        slots = free_slots(0, 60, "09:00", "18:00", False, [], [], now, extra_busy=extra)
        self.assertEqual(slots, [])


class AcademicParse(unittest.TestCase):
    HTML = """
    <div class="yearSchdulWrap">
      <p id="yearmonth20262">2026.2.</p>
      <div class="scheList"><ul>
        <li><dl><dt><span>02.09. (월) ~ 02.12. (목)</span></dt>
            <dd><span>2026-1학기 학과 기본 수강 신청</span></dd></dl></li>
        <li><dl><dt><span>02.16. (월) ~ 02.18. (수)</span></dt>
            <dd><span>설 연휴</span></dd></dl></li>
      </ul></div>
    </div>
    """

    def test_range_and_busy(self):
        from app.tools.academic import parse_sche_list
        ev = parse_sche_list(self.HTML)
        self.assertEqual(len(ev), 2)
        self.assertEqual(ev[0]["start"], "2026-02-09")
        self.assertEqual(ev[0]["end"], "2026-02-12")
        self.assertFalse(ev[0]["busy"])
        self.assertTrue(ev[1]["busy"])
        self.assertIn("설", ev[1]["title"])


class DiscordDm(unittest.TestCase):
    def test_help_and_stranger(self):
        from app import config, discord_bot
        orig = config.load
        try:
            config.load = lambda: {"discord": {**discord_bot.EMPTY, "owner_id": "1", "owner_name": "나"}}
            replies, action = discord_bot.handle_dm("1", "나", "도움말")
            self.assertIsNone(action)
            self.assertTrue(replies and "비서" in replies[0])
            replies, action = discord_bot.handle_dm("99", "남", "안녕")
            self.assertIn("주인만", replies[0])
        finally:
            config.load = orig

    def test_link_code(self):
        from app import config, discord_bot
        saved = {}
        orig_load, orig_save = config.load, config.save
        cfg = {**discord_bot.EMPTY, "link_code": "123456"}
        try:
            config.load = lambda: {"discord": cfg}
            config.save = lambda u: saved.update(u)
            replies, action = discord_bot.handle_dm("42", "민수", "코드는 123456 입니다")
            self.assertIsNone(action)
            self.assertTrue(any("연결" in r for r in replies))
            self.assertEqual(str(saved["discord"]["owner_id"]), "42")
        finally:
            config.load, config.save = orig_load, orig_save


class TelegramHtml(unittest.TestCase):
    def test_md_to_html(self):
        from app.telegram_bot import md_to_html
        html = md_to_html("**제목**\n- 항목\n[공지](https://www.inhatc.ac.kr/a)")
        self.assertIn("<b>제목</b>", html)
        self.assertIn("• ", html)
        self.assertIn('href="https://www.inhatc.ac.kr/a"', html)
        self.assertIn("&lt;", md_to_html("<script>"))


class SchedulerWindow(unittest.TestCase):
    def test_due_daily_catchup_after_midnight(self):
        from app.scheduler import due_daily
        now = datetime(2026, 10, 3, 1, 0, tzinfo=KST)  # 토요일 01:00
        ok, slot = due_daily(now, "22:00", "")
        self.assertTrue(ok)
        self.assertTrue(slot.startswith("2026-10-02"))

    def test_due_daily_already_sent(self):
        from app.scheduler import due_daily
        now = datetime(2026, 10, 2, 23, 0, tzinfo=KST)
        ok, _ = due_daily(now, "22:00", "2026-10-02 22:00")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
