"""순수 로직 단위 테스트. 네트워크·Gemini 없이 동작해야 합니다.
실행:  프로젝트 폴더에서  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

# 테스트는 실제 data/ 폴더(내 키·기록)를 건드리지 않도록 임시 폴더를 씁니다. app 을 import 하기 전에 정해야 합니다.
os.environ.setdefault("INHA_AI_DATA_DIR", tempfile.mkdtemp(prefix="inha-ai-test-"))
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



class PortalTimetable(unittest.TestCase):
    """포털 「개인수업시간표조회」 PDF 파서 — 좌표만으로 요일·교시를 맞히는지 (가짜 좌표로 검증)."""

    def _fake(self):
        chars, boxes = [], []

        def put(text, x, y, step=7.9):
            for i, ch in enumerate(text):
                chars.append((ch, x + i * step, y))
        put("일반", 40, 545)                         # 소속 줄의 '일' — 요일로 착각하면 안 됨
        for i, d in enumerate("월화수목금토"):        # 요일 머리글
            chars.append((d, 156 + i * 105, 530))
        put("기타", 774, 530)
        times = ["09:00~09:50", "09:55~10:45", "10:50~11:40", "11:45~12:35", "12:40~13:30"]
        for i, t in enumerate(times):                # 교시별 시간 (위에서 아래로)
            put(t, 54, 505 - i * 15.6, step=4.2)
            boxes.append((42.5, 498.9 - i * 15.6, 107.7, 514.5 - i * 15.6))
        # 화요일 2~4교시가 합쳐진 칸: 과목명이 줄바꿈되어 교수명이 둘로 쪼개진 경우
        boxes.append((212.6, 452.1, 317.5, 498.9))
        put("생성형AI프로그래밍:3C", 216, 480, step=5.5)
        put("(이", 301.3, 480)
        put("원주)", 254.8, 472)
        put("7", 243.2, 464); put("호관", 247.6, 464)      # 실제 PDF처럼 숫자는 좁고 한글은 넓게
        put("301", 266.5, 464, step=4.4); put("호", 279.7, 464)
        # 수요일 1교시 한 칸짜리
        boxes.append((317.5, 498.9, 422.4, 514.5))
        put("데이터분석:3C", 327, 505, step=6.0); put("(이수정)", 381, 505)
        boxes.append((422.4, 498.9, 527.2, 514.5))    # 빈 칸
        return chars, boxes

    def test_parse(self):
        from app import portal_timetable as pt
        fake = self._fake()
        orig = pt._read
        pt._read = lambda data: fake
        try:
            rows = pt.parse(b"%PDF-")
        finally:
            pt._read = orig
        self.assertEqual(len(rows), 2)
        a = rows[0]
        self.assertEqual((a["day"], a["start"], a["end"]), (1, "09:55", "12:35"))
        self.assertEqual((a["title"], a["prof"], a["place"]), ("생성형AI프로그래밍", "이원주", "7호관 301호"))
        b = rows[1]
        self.assertEqual((b["day"], b["start"], b["end"], b["title"], b["prof"]), (2, "09:00", "09:50", "데이터분석", "이수정"))

    def test_not_a_timetable(self):
        from app import portal_timetable as pt
        self.assertEqual(pt.parse(b"not a pdf"), [])

    def test_normalize_keeps_prof(self):
        from app import timetable
        rows = timetable.normalize([{"day": 1, "start": "09:55", "end": "12:35", "title": "블록체인", "prof": "최효현"}])
        self.assertEqual(rows[0]["prof"], "최효현")
        self.assertEqual(len(timetable.PERIODS), 16)
        self.assertEqual(timetable.PERIODS[1], ("09:55", "10:45"))


class DiscordTeamChat(unittest.TestCase):
    def test_format_messages(self):
        from app import discord_bot as db
        raw = [("민수", "10/02 09:00", "내일 3시에 회의하자", False),
               ("봇", "10/02 09:01", "알림입니다", True),
               ("지은", "10/02 09:02", "", False),
               ("지은", "10/02 09:03", "좋아 발표자료는 내가 할게", False)]
        out = db.format_messages(raw)
        self.assertEqual([m["author"] for m in out], ["민수", "지은"])   # 봇·빈 메시지 제외, 순서 유지
        long = [("a", "t", "가" * 600, False)] * 50
        self.assertLess(sum(len(m["text"]) for m in db.format_messages(long, max_chars=3000)), 3000)


class CalendarBusy(unittest.TestCase):
    """빈 시간 계산은 일정 목록(calendar.events 권한)만으로 바쁜 시간을 만든다."""
    def test_busy_from_events(self):
        from app.timetable import busy_from_events
        ev = lambda title, s, e, **kw: {"summary": title, "start": {"dateTime": s}, "end": {"dateTime": e}, **kw}
        items = [
            ev("팀 회의", "2026-10-05T10:00:00+09:00", "2026-10-05T11:00:00+09:00"),
            ev("UTC 표기", "2026-10-05T04:00:00Z", "2026-10-05T05:00:00Z"),
            ev("취소됨", "2026-10-05T12:00:00+09:00", "2026-10-05T13:00:00+09:00", status="cancelled"),
            ev("한가함 표시", "2026-10-05T12:00:00+09:00", "2026-10-05T13:00:00+09:00", transparency="transparent"),
            ev("거절한 초대", "2026-10-05T15:00:00+09:00", "2026-10-05T16:00:00+09:00",
               attendees=[{"self": True, "responseStatus": "declined"}]),
            {"summary": "출품 마감", "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}},
        ]
        busy, all_day = busy_from_events(items)
        self.assertEqual([(s.strftime("%H:%M"), e.strftime("%H:%M"), t) for s, e, t in busy],
                         [("10:00", "11:00", "팀 회의"), ("13:00", "14:00", "UTC 표기")])
        self.assertEqual(all_day, ["출품 마감"])


class SavedHistory(unittest.TestCase):
    """프로그램을 다시 켜도 AI가 직전 대화를 이어받는다."""
    def _run(self, msgs):
        from unittest import mock
        from app import agent
        with mock.patch("app.chats.get", return_value=msgs):
            return [(c.role, c.parts[0].text) for c in agent._saved_history("s1")]

    def test_pairs_restored(self):
        h = self._run([{"role": "user", "text": "지도교수님은 김철수 교수님이야"}, {"role": "bot", "text": "기억할게요"},
                       {"role": "user", "text": "고마워"}, {"role": "bot", "text": "네!"}])
        self.assertEqual([r for r, _ in h], ["user", "model", "user", "model"])
        self.assertIn("김철수", h[0][1])

    def test_broken_pairs_are_dropped(self):
        h = self._run([{"role": "bot", "text": "앞이 잘린 답"}, {"role": "user", "text": "응답 없이 끝난 질문"},
                       {"role": "user", "text": "질문"}, {"role": "bot", "text": "답"}, {"role": "user", "text": "마지막 질문"}])
        self.assertEqual(h, [("user", "질문"), ("model", "답")])

    def test_long_and_many(self):
        from app import agent
        msgs = []
        for i in range(30):
            msgs += [{"role": "user", "text": f"q{i} " + "가" * 5000}, {"role": "bot", "text": f"a{i}"}]
        h = self._run(msgs)
        self.assertEqual(len(h), agent.RESTORE_MSGS)
        self.assertTrue(h[0][1].startswith("q24") and len(h[0][1]) <= agent.RESTORE_CHARS)
        self.assertEqual(h[-1], ("model", "a29"))


if __name__ == "__main__":
    unittest.main()
