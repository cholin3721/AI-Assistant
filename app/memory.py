"""비서의 기억: 사용자가 알려준 사실(사람·연락처·선호 등)을 저장하고 매 대화에 참고합니다."""
import time
import uuid

from . import store
from .tools import ToolError, tool

DEFAULT = {"facts": []}
MAX_FACTS = 100
BLOCKED = ("비밀번호", "password", "계좌번호", "카드번호", "주민등록", "주민번호", "otp", "인증번호")


def facts() -> list:
    return store.load("memory", DEFAULT)["facts"]


def add_fact(text: str) -> dict:
    text = " ".join(text.split())[:300]
    if not text:
        raise ToolError("기억할 내용이 비어 있어요.")
    if any(b in text.lower() for b in BLOCKED):
        raise ToolError("비밀번호·계좌·주민번호 같은 민감한 정보는 기억하지 않아요.")
    item = {"id": uuid.uuid4().hex[:8], "text": text, "created": time.time()}

    def fn(d):
        if any(f["text"] == text for f in d["facts"]):
            return None
        d["facts"].append(item)
        del d["facts"][:-MAX_FACTS]
        return item
    return store.update("memory", DEFAULT, fn) or {"text": text, "duplicate": True}


def delete_fact(fid: str) -> bool:
    def fn(d):
        before = len(d["facts"])
        d["facts"] = [f for f in d["facts"] if f["id"] != fid]
        return len(d["facts"]) < before
    return store.update("memory", DEFAULT, fn)


def prompt_block() -> str:
    fs = facts()
    if not fs:
        return "- (아직 없음)"
    return "\n".join(f"- {f['text']}" for f in fs[-60:])


@tool("기억하기")
def remember(fact: str) -> dict:
    """사용자에 대해 앞으로도 알아야 할 사실을 기억합니다.
    사용자가 "기억해", "앞으로 ~라고 불러" 라고 하거나, 사람·연락처·지도교수·팀원·선호처럼 다음에도 쓸 정보를 알려줬을 때 사용하세요.
    비밀번호·계좌번호 같은 민감정보는 저장하지 마세요.

    Args:
        fact: 한 문장으로 정리한 사실. 예) "김철수 교수님은 캡스톤 지도교수, 메일 kim@inhatc.ac.kr"
    """
    item = add_fact(fact)
    return {"saved": not item.get("duplicate"), "fact": item["text"]}


@tool("기억 지우기")
def forget(keyword: str) -> dict:
    """기억한 사실 중 keyword가 들어간 것을 지웁니다. 사용자가 잊으라고 할 때 사용하세요.

    Args:
        keyword: 지울 기억에 들어 있는 단어.
    """
    kw = keyword.strip().lower()
    if not kw:
        raise ToolError("지울 단어를 알려주세요.")
    removed = []

    def fn(d):
        keep = []
        for f in d["facts"]:
            (removed if kw in f["text"].lower() else keep).append(f)
        d["facts"] = keep
    store.update("memory", DEFAULT, fn)
    return {"removed": [f["text"] for f in removed]}
