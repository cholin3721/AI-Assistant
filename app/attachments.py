"""첨부파일 텍스트 추출: 한글(hwp, hwpx), PDF, 워드(docx), 텍스트.

학교 공지 첨부는 대부분 한글 파일이라 별도 프로그램 없이 직접 읽도록 구현했습니다.
- hwp(5.0): OLE 복합 파일 → BodyText/SectionN 스트림(zlib 압축) → 문단 텍스트 레코드(태그 67)
- hwpx / docx: zip 안의 XML에서 글자만 추출
"""
import io
import re
import struct
import zipfile
import zlib
from xml.etree import ElementTree

MAX_CHARS = 8000


def _clean(text: str) -> str:
    text = text.replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t　]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ---------- HWP 5.0 ----------
_CHAR_CTRL = {0, 10, 13} | set(range(24, 32))   # 1글자 크기 제어문자
HWPTAG_PARA_TEXT = 67


def _hwp_para_text(buf: bytes) -> str:
    out, i, n = [], 0, len(buf) // 2
    chars = struct.unpack(f"<{n}H", buf[: n * 2])
    while i < n:
        c = chars[i]
        if c < 32:
            if c in _CHAR_CTRL:
                if c in (10, 13):
                    out.append("\n")
                i += 1
            else:  # 확장/인라인 제어문자: 8글자 크기
                if c == 9:
                    out.append("\t")
                i += 8
        else:
            out.append(chr(c))
            i += 1
    return "".join(out)


def _hwp_records(data: bytes):
    pos, n = 0, len(data)
    while pos + 4 <= n:
        header = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield tag, data[pos:pos + size]
        pos += size


def hwp_text(data: bytes) -> str:
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        header = ole.openstream("FileHeader").read()
        flags = struct.unpack_from("<I", header, 36)[0]
        compressed, encrypted = bool(flags & 1), bool(flags & 2)
        if encrypted:
            raise ValueError("암호가 걸린 한글 파일이라 읽을 수 없어요.")
        sections = sorted(
            ["/".join(p) for p in ole.listdir() if len(p) == 2 and p[0] == "BodyText" and p[1].startswith("Section")],
            key=lambda s: int(re.sub(r"\D", "", s) or 0))
        parts = []
        for sec in sections:
            raw = ole.openstream(sec).read()
            if compressed:
                raw = zlib.decompress(raw, -15)
            for tag, payload in _hwp_records(raw):
                if tag == HWPTAG_PARA_TEXT:
                    parts.append(_hwp_para_text(payload))
                    parts.append("\n")
        text = _clean("".join(parts))
        if not text and ole.exists("PrvText"):  # 배포용 문서 등: 미리보기 텍스트라도
            text = _clean(ole.openstream("PrvText").read().decode("utf-16-le", errors="ignore"))
        return text
    finally:
        ole.close()


# ---------- zip 기반 (hwpx, docx) ----------
def _xml_text(xml: bytes, para_tag: str, text_tag: str) -> str:
    root = ElementTree.fromstring(xml)
    paras = []
    for p in root.iter():
        if p.tag.endswith("}" + para_tag) or p.tag == para_tag:
            t = "".join((e.text or "") for e in p.iter() if e.tag.endswith("}" + text_tag) or e.tag == text_tag)
            if t.strip():
                paras.append(t)
    return "\n".join(paras)


def zip_text(data: bytes) -> tuple[str, str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    secs = sorted([n for n in names if re.match(r"Contents/section\d+\.xml$", n)],
                  key=lambda s: int(re.sub(r"\D", "", s)))
    if secs:
        return "hwpx", _clean("\n".join(_xml_text(z.read(s), "p", "t") for s in secs))
    if "word/document.xml" in names:
        return "docx", _clean(_xml_text(z.read("word/document.xml"), "p", "t"))
    raise ValueError("지원하지 않는 압축 형식이에요 (hwpx·docx만 읽을 수 있어요).")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return _clean("\n".join((page.extract_text() or "") for page in reader.pages[:30]))


def ocr_with_gemini(data: bytes, mime: str) -> str:
    """스캔 PDF·이미지 글자 추출. API 키가 없거나 실패하면 빈 문자열."""
    from . import config
    cfg = config.load()
    if not cfg.get("gemini_api_key") or len(data) > 15_000_000:
        return ""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=cfg["gemini_api_key"])
        resp = client.models.generate_content(
            model=cfg.get("model") or "gemini-3.8-flash",
            contents=[types.Part.from_bytes(data=data, mime_type=mime),
                      "이 스캔 문서/이미지의 글자를 모두 뽑아줘. 표는 왼쪽→오른쪽, 위→아래 칸 순서로. "
                      "레이아웃은 유지하고, 장식이 아닌 실제 글자만."],
            config=types.GenerateContentConfig(temperature=0.1),
        )
        return _clean(resp.text or "")
    except Exception:
        return ""


def _image_mime(data: bytes, filename: str) -> str:
    name = filename.lower()
    if data[:8] == b"\x89PNG\r\n\x1a\n" or name.endswith(".png"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff" or name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" or name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return ""


def extract_text(data: bytes, filename: str = "", max_chars: int = MAX_CHARS) -> dict:
    """파일 내용(바이트)을 보고 형식을 판별해 텍스트를 추출. 파일 이름보다 실제 내용을 우선.
    스캔 PDF·이미지는 Gemini Vision으로 글자를 읽습니다."""
    name = filename.lower()
    ocr = False
    try:
        if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            kind, text = "hwp", hwp_text(data)
        elif data[:4] == b"PK\x03\x04":
            kind, text = zip_text(data)
        elif data[:5] == b"%PDF-":
            kind, text = "pdf", pdf_text(data)
            if not text:
                text = ocr_with_gemini(data, "application/pdf")
                ocr = bool(text)
                kind = "pdf-ocr" if ocr else kind
        elif name.endswith((".txt", ".csv", ".md")):
            kind, text = "text", _clean(data.decode("utf-8", errors="replace"))
        else:
            mime = _image_mime(data, filename)
            if mime:
                text = ocr_with_gemini(data, mime)
                kind, ocr = "image-ocr", bool(text)
            else:
                return {"kind": "unknown", "text": "", "error": "읽을 수 없는 형식이에요 (한글·PDF·워드·텍스트·이미지만 가능)."}
    except Exception as e:
        return {"kind": "error", "text": "", "error": f"파일을 읽지 못했어요: {e}"}
    if not text:
        return {"kind": kind, "text": "", "error": "글자를 찾지 못했어요 (암호가 걸려 있거나 빈 문서일 수 있어요)."}
    cut = len(text) > max_chars
    return {"kind": kind, "text": text[:max_chars] + ("\n…(이하 생략)" if cut else ""),
            "truncated": cut, "ocr": ocr}
