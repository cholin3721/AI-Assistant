"""ERP 포털 「개인수업시간표조회」 PDF를 AI 없이 그대로 읽습니다.

포털 PDF는 표의 칸 하나하나가 '채워진 사각형'으로 그려지고, 수업은 여러 교시가 합쳐진 큰 칸 안에
"과목명:분반 (교수명)" + "강의실" 로 적혀 있습니다. 그래서
  1) 글자 위치로 교시별 시간(09:00~09:50 …)과 요일 머리글(월~토)의 좌표를 찾고
  2) 글자가 들어 있는 칸(사각형)이 어느 요일 열, 몇 교시부터 몇 교시까지 걸쳐 있는지 계산해
수업을 뽑습니다. 좌표로 읽기 때문에 교시가 틀릴 일이 없고, 키가 없어도 동작합니다.
"""
import io
import re

DAY_CHARS = "월화수목금토일"
TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})")
CELL_RE = re.compile(r"(?s)^(.+?):\s*([^\s(]+)\s*\((.*?)\)\s*(.*)$")


def _read(data: bytes):
    """페이지의 글자(문자, x, y)와 채워진 사각형(x0, y0, x1, y1)을 모읍니다."""
    from pypdf import PdfReader
    page = PdfReader(io.BytesIO(data)).pages[0]
    chars, boxes, path = [], [], []

    def on_op(op, args, cm, tm):
        nonlocal path
        if op == b"m":
            path.append([(float(args[0]), float(args[1]))])
        elif op == b"l" and path:
            path[-1].append((float(args[0]), float(args[1])))
        elif op == b"re":
            x, y, w, h = (float(a) for a in args)
            path.append([(x, y), (x + w, y + h)])
        elif op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"):
            pts = [p for sub in path for p in sub]
            if len(pts) >= 2:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
            path = []
        elif op in (b"S", b"s", b"n"):
            path = []

    def on_text(text, cm, tm, font_dict, font_size):
        if text and text.strip():
            x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
            y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
            chars.append((text, x, y))

    page.extract_text(visitor_operand_before=on_op, visitor_text=on_text)
    return chars, boxes


def _lines(chars: list) -> list:
    """같은 높이의 글자를 한 줄로 묶어 [(y, [(x, 글자), …])]."""
    rows: list = []
    for ch, x, y in sorted(chars, key=lambda c: (-c[2], c[1])):
        if rows and abs(rows[-1][0] - y) <= 1.5:
            rows[-1][1].append((x, ch))
        else:
            rows.append((y, [(x, ch)]))
    return rows


def _join(items: list) -> str:
    """글자 사이 간격을 보고 띄어쓰기를 되살립니다."""
    items = sorted(items)
    out = ""
    for i, (x, ch) in enumerate(items):
        if i:
            px, pch = items[i - 1]
            advance = 7.9 * len(pch) if ord(pch[-1]) > 0x2E80 else 4.6 * len(pch)
            if x - px > advance + 1.8:
                out += " "
        out += ch
    return out


def parse(data: bytes) -> list:
    """포털 시간표 PDF → [{day, start, end, title, place, prof}]. 포털 형식이 아니면 빈 목록."""
    try:
        chars, boxes = _read(data)
    except Exception:
        return []
    if not chars or not boxes:
        return []
    lines = _lines(chars)

    # 1) 교시별 시간: "09:00~09:50" 이 적힌 줄의 높이
    periods = []
    for y, items in lines:
        m = TIME_RE.search(_join(items).replace(" ", ""))
        if m:
            periods.append((y, m.group(1).zfill(5), m.group(2).zfill(5)))
    if len(periods) < 3:
        return []
    top_period_y = max(p[0] for p in periods)

    # 2) 요일 머리글: 첫 교시보다 위, 홀로 떨어져 있는 '월 화 수 …' 한 글자가 가장 많은 줄
    days: dict = {}
    for y, items in lines:
        if y <= top_period_y:
            continue
        found = {}
        for x, ch in items:
            c = ch.strip()
            alone = all(abs(x - ox) > 14 for ox, _ in items if ox != x)
            if len(c) == 1 and c in DAY_CHARS and alone:
                found.setdefault(DAY_CHARS.index(c), x + 4)  # 글자 가운데쯤
        if len(found) > len(days):
            days = found
    if len(days) < 3:
        return []

    # 3) 글자가 들어 있는 칸 → 수업
    left_edge = min(days.values()) - 60
    out, seen = [], set()
    for x0, y0, x1, y1 in boxes:
        if y1 > top_period_y + 14 or x1 - x0 < 30 or x0 < left_edge:
            continue  # 머리글·교시 열
        day = next((d for d, cx in days.items() if x0 - 1 <= cx <= x1 + 1), None)
        if day is None:
            continue
        inside = [(y, [(x, ch) for x, ch in items if x0 - 1 <= x <= x1 + 1])
                  for y, items in lines if y0 - 1 <= y <= y1 + 1]
        text_lines = [_join(items) for _, items in inside if items]
        if not text_lines:
            continue
        covered = sorted((p for p in periods if y0 - 1 <= p[0] <= y1 + 1), key=lambda p: -p[0])
        if not covered:
            continue
        full = "\n".join(text_lines)
        m = CELL_RE.match(full)
        if m:
            title = re.sub(r"\s*\n\s*", "", m.group(1)).strip()
            prof = re.sub(r"\s+", "", m.group(3))
            place = re.sub(r"\s*\n\s*", " ", m.group(4)).strip()
        else:
            title, prof, place = text_lines[0].strip(), "", " ".join(text_lines[1:]).strip()
        key = (day, covered[0][1], title)
        if not title or key in seen:
            continue
        seen.add(key)
        out.append({"day": day, "start": covered[0][1], "end": covered[-1][2],
                    "title": title, "place": place, "prof": prof})
    return sorted(out, key=lambda c: (c["day"], c["start"]))
