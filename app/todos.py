"""할 일 목록 (내 PC에 저장). 공지 체크리스트·메일에서 나온 할 일을 모아둡니다."""
import time
import uuid
from datetime import datetime

from . import store
from .tools import ToolError, tool

DEFAULT = {"items": []}


def items(include_done: bool = False) -> list:
    xs = store.load("todos", DEFAULT)["items"]
    if not include_done:
        xs = [x for x in xs if not x["done"]]
    return sorted(xs, key=lambda x: (x["done"], x.get("due") or "9999", x["created"]))


def _valid_due(due: str) -> str:
    due = (due or "").strip()[:10]
    if not due:
        return ""
    try:
        datetime.strptime(due, "%Y-%m-%d")
    except ValueError:
        raise ToolError(f"마감일 형식이 잘못됐어요: {due} (예: 2026-11-04)")
    return due


def add(title: str, due: str = "", note: str = "", link: str = "") -> dict:
    title = " ".join(title.split())[:120]
    if not title:
        raise ToolError("할 일 내용이 비어 있어요.")
    item = {"id": uuid.uuid4().hex[:8], "title": title, "due": _valid_due(due), "note": note[:300],
            "link": link, "done": False, "created": time.time()}

    def fn(d):
        for x in d["items"]:
            if not x["done"] and x["title"] == title:
                return x
        d["items"].append(item)
        return item
    return store.update("todos", DEFAULT, fn)


def set_done(tid: str, done: bool = True) -> dict | None:
    def fn(d):
        for x in d["items"]:
            if x["id"] == tid:
                x["done"] = done
                x["done_at"] = time.time() if done else None
                return x
    return store.update("todos", DEFAULT, fn)


def delete(tid: str) -> bool:
    def fn(d):
        n = len(d["items"])
        d["items"] = [x for x in d["items"] if x["id"] != tid]
        return len(d["items"]) < n
    return store.update("todos", DEFAULT, fn)


@tool("할 일 추가")
def add_todo(title: str, due: str = "", note: str = "", link: str = "") -> dict:
    """할 일 목록에 항목을 추가합니다. 사용자가 할 일로 넣어달라고 하거나, 공지 체크리스트를 할 일로 저장할 때 사용하세요.

    Args:
        title: 할 일 (짧게). 예) "AID 공모전 참가신청서 작성"
        due: 마감일 "YYYY-MM-DD" (없으면 빈 문자열).
        note: 메모 (선택).
        link: 관련 공지·메일 링크 (선택).
    """
    return add(title, due, note, link)


@tool("할 일 보기")
def list_todos(include_done: bool = False) -> dict:
    """할 일 목록을 봅니다.

    Args:
        include_done: 완료한 일도 포함할지.
    """
    xs = items(include_done)
    return {"count": len(xs), "todos": [{k: x.get(k) for k in ("id", "title", "due", "done", "link")} for x in xs]}


@tool("할 일 완료")
def complete_todo(keyword: str) -> dict:
    """할 일을 완료 처리합니다. 제목에 keyword가 들어간 미완료 항목을 찾습니다.

    Args:
        keyword: 할 일 제목의 일부 또는 id.
    """
    kw = keyword.strip().lower()
    matches = [x for x in items() if x["id"] == kw or kw in x["title"].lower()]
    if not matches:
        raise ToolError("해당하는 할 일을 찾지 못했어요.")
    if len(matches) > 1:
        return {"ambiguous": True, "candidates": [x["title"] for x in matches], "note": "어느 것인지 사용자에게 물어보세요."}
    set_done(matches[0]["id"])
    return {"completed": matches[0]["title"]}
