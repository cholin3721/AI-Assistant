"""인하공업전문대학 홈페이지 공지사항 크롤러 (K2Web 게시판)."""
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import tool, ToolError

SITE = "https://www.inhatc.ac.kr"
KST = timezone(timedelta(hours=9))
BOARDS = {           # 공지사항 메뉴의 게시판 번호
    "학사": 11,
    "장학": 17,
    "행사": 18,
    "채용": 19,
    "일반": 33,
}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InhaAIAssistant/1.0"}
CACHE_SEC = 600
_cache: dict = {}
DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def _get(url: str, params=None) -> str:
    key = (url, tuple(sorted((params or {}).items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    _cache[key] = (time.time(), r.text)
    return r.text


def parse_list(html: str, board_name: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select('a[href*="artclView.do"]'):
        href = a.get("href", "")
        url = urljoin(SITE, href.split("?")[0])
        if url in seen:
            continue
        title = a.get_text(" ", strip=True)
        title = re.sub(r"\s*(새글|NEW|new)\s*$", "", title).strip()
        if not title:
            continue
        row = a.find_parent("tr") or a.find_parent("li") or a.parent
        row_text = row.get_text(" ", strip=True) if row else ""
        m = DATE_RE.search(row_text)
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
        pinned = bool(row and row.get("class") and any("headline" in c or "notice" in c for c in row.get("class")))
        seen.add(url)
        items.append({"board": board_name, "title": title, "date": date, "url": url, "pinned": pinned})
    return items


def fetch_board(board_name: str, page: int = 1) -> list:
    bid = BOARDS[board_name]
    html = _get(f"{SITE}/bbs/kr/{bid}/artclList.do", {"page": page})
    return parse_list(html, board_name)


@tool("학교 공지 확인")
def get_school_notices(category: str = "전체", keyword: str = "", days: int = 14, limit: int = 15) -> dict:
    """인하공업전문대학 홈페이지 공지사항 목록을 가져옵니다.

    Args:
        category: "전체", "학사", "장학", "행사", "채용", "일반" 중 하나. 공모전·특강·비교과는 주로 "행사", 장학금은 "장학".
        keyword: 제목 검색어 (예: "공모전", "장학", "AI"). 없으면 빈 문자열.
        days: 최근 며칠 이내 게시물만 (기본 14일).
        limit: 최대 개수 (1~40).
    """
    names = list(BOARDS) if category in ("", "전체", None) else [category]
    for n in names:
        if n not in BOARDS:
            raise ToolError(f"카테고리는 {', '.join(['전체'] + list(BOARDS))} 중 하나여야 해요.")
    cutoff = (datetime.now(KST) - timedelta(days=int(days))).strftime("%Y-%m-%d")
    results, errors = [], []
    for n in names:
        try:
            pages = (1, 2) if keyword else (1,)
            for p in pages:
                results.extend(fetch_board(n, p))
        except requests.RequestException as e:
            errors.append(f"{n}: {type(e).__name__}")
    if not results and errors:
        raise ToolError("학교 홈페이지에 접속하지 못했어요. 인터넷 연결을 확인해주세요. (" + ", ".join(errors) + ")")
    if keyword:
        kws = keyword.lower().split()
        results = [r for r in results if all(k in r["title"].lower() for k in kws)]
    results = [r for r in results if not r["date"] or r["date"] >= cutoff]
    uniq = {r["url"]: r for r in results}
    results = sorted(uniq.values(), key=lambda r: r["date"], reverse=True)[: max(1, min(int(limit), 40))]
    out = {"count": len(results), "since": cutoff, "notices": results}
    if errors:
        out["partial_errors"] = errors
    return out


@tool("공지 본문 읽기")
def read_school_notice(url: str) -> dict:
    """학교 공지 하나의 본문과 첨부파일 목록을 읽습니다. 신청 기간·대상·혜택을 확인할 때 사용하세요.

    Args:
        url: get_school_notices 결과의 url.
    """
    if not url.startswith(SITE):
        raise ToolError("인하공전 홈페이지 공지 주소만 읽을 수 있어요.")
    soup = BeautifulSoup(_get(url), "html.parser")
    title_el = soup.select_one(".artclViewTitle, .view-title, h2.artclViewTitle, .bbs-title")
    body_el = (soup.select_one(".artclView") or soup.select_one(".view-con")
               or soup.select_one(".artclViewContents") or soup.select_one("article") or soup.body)
    for bad in body_el.select("script, style"):
        bad.decompose()
    text = re.sub(r"\n{3,}", "\n\n", body_el.get_text("\n", strip=True))
    files = [{"name": a.get_text(strip=True), "url": urljoin(SITE, a.get("href", ""))}
             for a in soup.select('a[href*="download.do"]') if a.get_text(strip=True)]
    return {
        "title": title_el.get_text(" ", strip=True) if title_el else "",
        "url": url,
        "content": text[:6000] + ("\n…(이하 생략)" if len(text) > 6000 else ""),
        "attachments": files[:10],
    }


def _filename_from_headers(headers) -> str:
    from urllib.parse import unquote
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if not m:
        return ""
    name = unquote(m.group(1).strip())
    try:  # 일부 서버는 UTF-8 바이트를 latin-1로 보냄
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


@tool("공지 첨부파일 읽기")
def read_notice_attachment(url: str) -> dict:
    """학교 공지의 첨부파일(한글 hwp·hwpx, PDF, 워드)을 내려받아 내용을 읽습니다.
    신청 자격·제출 서류·양식 내용처럼 본문에 없는 정보가 필요할 때 read_school_notice의 attachments url로 사용하세요.

    Args:
        url: read_school_notice 결과 attachments의 url.
    """
    from ..attachments import extract_text
    data, name = download_attachment(url)
    res = extract_text(data, name)
    return {"filename": name, **res}


def download_attachment(url: str) -> tuple:
    """학교 공지 첨부파일을 내려받아 (바이트, 파일 이름)을 돌려줌."""
    if not url.startswith(SITE):
        raise ToolError("인하공전 홈페이지 첨부파일만 읽을 수 있어요.")
    r = requests.get(url, headers=HEADERS, timeout=20, stream=True)
    r.raise_for_status()
    data = b""
    for chunk in r.iter_content(65536):
        data += chunk
        if len(data) > 15_000_000:
            raise ToolError("첨부파일이 너무 커요 (15MB 초과).")
    return data, _filename_from_headers(r.headers)
