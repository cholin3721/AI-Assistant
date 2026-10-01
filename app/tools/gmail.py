import base64
import re
from email.mime.text import MIMEText
from email.utils import parseaddr

from . import tool, google_service, ToolError


def _header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload) -> str:
    """메일 본문에서 text/plain 우선, 없으면 HTML을 텍스트로."""
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            (plain if mime == "text/plain" else html if mime == "text/html" else plain).append(text)
        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    if plain:
        return "\n".join(plain)
    if html:
        from bs4 import BeautifulSoup
        return BeautifulSoup("\n".join(html), "html.parser").get_text("\n")
    return ""


@tool("메일 검색")
def search_emails(query: str = "is:unread", max_results: int = 10) -> dict:
    """사용자의 Gmail에서 메일을 검색합니다.

    Args:
        query: Gmail 검색어. 예) "is:unread", "from:교수님 newer_than:7d", "subject:과제", "is:important is:unread".
        max_results: 가져올 최대 개수 (1~25).
    """
    svc = google_service("gmail", "v1")
    max_results = max(1, min(int(max_results), 25))
    res = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    items = []
    for m in res.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()
        h = msg.get("payload", {}).get("headers", [])
        items.append({
            "id": m["id"],
            "from": _header(h, "From"),
            "subject": _header(h, "Subject"),
            "date": _header(h, "Date"),
            "snippet": msg.get("snippet", ""),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return {"count": len(items), "emails": items}


@tool("메일 읽기")
def read_email(message_id: str) -> dict:
    """메일 한 통의 전체 내용을 읽습니다.

    Args:
        message_id: search_emails 결과의 id.
    """
    svc = google_service("gmail", "v1")
    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = msg.get("payload", {}).get("headers", [])
    body = re.sub(r"\n{3,}", "\n\n", _decode_body(msg.get("payload", {}))).strip()
    attachments = []

    def walk(part):
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            attachments.append({"attachment_id": part["body"]["attachmentId"], "filename": part["filename"],
                                "mime_type": part.get("mimeType", ""), "size": part["body"].get("size", 0)})
        for p in part.get("parts", []) or []:
            walk(p)
    walk(msg.get("payload", {}))
    return {
        "id": message_id,
        "thread_id": msg.get("threadId"),
        "from": _header(h, "From"),
        "to": _header(h, "To"),
        "subject": _header(h, "Subject"),
        "date": _header(h, "Date"),
        "body": body[:6000] + ("\n…(이하 생략)" if len(body) > 6000 else ""),
        "attachments": attachments,
    }


@tool("답장 초안 작성")
def create_email_draft(to: str, subject: str, body: str, reply_to_message_id: str = "") -> dict:
    """Gmail '임시보관함'에 메일 초안을 만듭니다. 절대 바로 보내지 않습니다. 사용자가 Gmail에서 확인 후 직접 보냅니다.

    Args:
        to: 받는 사람 이메일 주소.
        subject: 제목.
        body: 본문 (일반 텍스트).
        reply_to_message_id: 답장일 경우 원본 메일 id (없으면 빈 문자열).
    """
    svc = google_service("gmail", "v1")
    if "@" not in parseaddr(to)[1]:
        raise ToolError("받는 사람 이메일 주소가 올바르지 않아요.")
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = to
    mime["Subject"] = subject
    draft_msg = {}
    if reply_to_message_id:
        orig = svc.users().messages().get(
            userId="me", id=reply_to_message_id, format="metadata",
            metadataHeaders=["Message-ID"]).execute()
        mid = _header(orig.get("payload", {}).get("headers", []), "Message-ID")
        if mid:
            mime["In-Reply-To"] = mid
            mime["References"] = mid
        draft_msg["threadId"] = orig.get("threadId")
    draft_msg["raw"] = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    d = svc.users().drafts().create(userId="me", body={"message": draft_msg}).execute()
    return {"draft_id": d.get("id"), "open_url": "https://mail.google.com/mail/u/0/#drafts",
            "note": "초안만 저장됨. 사용자가 Gmail에서 확인 후 직접 발송."}


@tool("메일 첨부 읽기")
def read_email_attachment(message_id: str, attachment_id: str) -> dict:
    """메일 첨부파일(한글 hwp·hwpx, PDF, 워드, 텍스트)의 내용을 읽습니다.

    Args:
        message_id: 메일 id.
        attachment_id: read_email 결과 attachments의 attachment_id.
    """
    from ..attachments import extract_text
    svc = google_service("gmail", "v1")
    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    name = ""

    def find(part):
        nonlocal name
        if part.get("body", {}).get("attachmentId") == attachment_id:
            name = part.get("filename", "")
        for p in part.get("parts", []) or []:
            find(p)
    find(msg.get("payload", {}))
    att = svc.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
    data = base64.urlsafe_b64decode(att.get("data", "") + "==")
    if len(data) > 15_000_000:
        raise ToolError("첨부파일이 너무 커요 (15MB 초과).")
    return {"filename": name, **extract_text(data, name)}
