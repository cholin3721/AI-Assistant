"""텔레그램 봇: 폰으로 알림 받기 + 폰에서 비서와 대화.

연결 순서: BotFather에서 봇 만들기 → 토큰 입력 → 화면의 '텔레그램에서 연결하기' 버튼(딥링크)으로
봇에 /start <코드> 전송 → 그 대화(chat_id)만 '주인'으로 등록. 다른 사람이 보낸 메시지는 무시합니다.
"""
import html
import random
import re
import threading
import time

import requests

from . import config

API = "https://api.telegram.org/bot{token}/{method}"
_state = {"gen": 0, "error": "", "last_ok": 0.0}


class TelegramError(Exception):
    pass


def _cfg() -> dict:
    return config.load().get("telegram") or {}


def _call(token: str, method: str, http_timeout: int = 15, **params):
    try:
        r = requests.post(API.format(token=token, method=method), json=params, timeout=http_timeout)
        data = r.json()
    except requests.RequestException as e:
        raise TelegramError(f"텔레그램 서버에 접속하지 못했어요 ({type(e).__name__})")
    except ValueError:
        raise TelegramError("텔레그램 응답을 읽지 못했어요.")
    if not data.get("ok"):
        desc = data.get("description", "")
        if data.get("error_code") == 401:
            raise TelegramError("봇 토큰이 올바르지 않아요. BotFather가 준 토큰을 다시 복사해주세요.")
        raise TelegramError(desc or "텔레그램 오류")
    return data["result"]


# ---------- 설정 ----------
def setup_token(token: str) -> dict:
    token = token.strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        raise TelegramError("토큰 형식이 아니에요. '123456789:AA…' 처럼 생긴 전체 토큰을 붙여넣어 주세요.")
    me = _call(token, "getMe")
    code = f"{random.randint(0, 999999):06d}"
    config.save({"telegram": {"token": token, "bot_username": me.get("username", ""),
                              "chat_id": None, "link_code": code, "owner_name": ""}})
    restart()
    return status()


def status() -> dict:
    t = _cfg()
    linked = bool(t.get("chat_id"))
    user = t.get("bot_username", "")
    return {
        "token_set": bool(t.get("token")),
        "bot_username": user,
        "linked": linked,
        "owner_name": t.get("owner_name", ""),
        "link_url": f"https://t.me/{user}?start={t.get('link_code')}" if user and not linked and t.get("link_code") else "",
        "error": _state["error"],
    }


def disconnect():
    config.save({"telegram": {"token": "", "bot_username": "", "chat_id": None, "link_code": "", "owner_name": ""}})
    restart()


# ---------- 보내기 ----------
def md_to_html(text: str) -> str:
    """비서 답변(마크다운) → 텔레그램 HTML."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        line = html.escape(line, quote=False)
        line = re.sub(r"^#{1,3}\s+(.*)$", r"<b>\1</b>", line)
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', line)
        out.append(line)
    return "\n".join(out)


def _chunks(text: str, size: int = 3800):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def send(text: str, chat_id=None):
    t = _cfg()
    chat_id = chat_id or t.get("chat_id")
    if not t.get("token") or not chat_id:
        raise TelegramError("텔레그램이 연결되지 않았어요.")
    for part in _chunks(text):
        try:
            _call(t["token"], "sendMessage", chat_id=chat_id, text=md_to_html(part),
                  parse_mode="HTML", disable_web_page_preview=True)
        except TelegramError:
            _call(t["token"], "sendMessage", chat_id=chat_id, text=part, disable_web_page_preview=True)


def send_safe(text: str):
    """알림용: 연결 안 돼 있거나 실패해도 조용히 넘어감."""
    if not _cfg().get("chat_id"):
        return
    try:
        send(text)
    except Exception as e:
        _state["error"] = f"알림 전송 실패: {e}"


# ---------- 받기 (롱폴링) ----------
HELP = ("인하 AI 비서예요. 그냥 말 걸면 돼요.\n"
        "예) 오늘 일정 알려줘 / 안 읽은 메일 요약해줘 / 장학금 공지 있어?\n\n"
        "/brief 지금 브리핑 받기\n/todo 할 일 보기\n/help 도움말")


def _reply_with_agent(chat_id, text):
    from . import agent
    t = _cfg()
    try:
        _call(t["token"], "sendChatAction", chat_id=chat_id, action="typing")
        res = agent.chat("telegram", text)
        reply = res["reply"]
        used = sorted({x["label"] for x in res.get("tools", [])})
        if used:
            reply += "\n\n· " + " · ".join(used)
    except Exception as e:
        reply = str(e)
    try:
        send(reply, chat_id)
    except Exception as e:
        _state["error"] = str(e)


def _handle(msg: dict):
    t = _cfg()
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    token = t.get("token")
    if not chat_id or not token:
        return
    owner = t.get("chat_id")

    if not owner:  # 아직 주인 없음 → 연결 코드 확인
        code = t.get("link_code")
        if code and text.startswith("/start") and code in text:
            name = msg.get("from", {}).get("first_name", "")
            config.save({"telegram": {**t, "chat_id": chat_id, "link_code": "", "owner_name": name}})
            send("연결됐어요! 이제 여기서 알림을 받고, 비서에게 바로 말 걸 수 있어요.\n\n" + HELP, chat_id)
        else:
            _call(token, "sendMessage", chat_id=chat_id,
                  text="AI 비서 화면의 「텔레그램에서 연결하기」 버튼으로 시작해주세요.")
        return

    if chat_id != owner:
        _call(token, "sendMessage", chat_id=chat_id, text="이 비서는 주인만 사용할 수 있어요.")
        return
    if not text:
        send("지금은 글자 메시지만 이해할 수 있어요.", chat_id)
        return
    if text in ("/start", "/help"):
        send(HELP, chat_id)
    elif text == "/brief":
        from . import scheduler
        threading.Thread(target=scheduler.run_briefing, kwargs={"manual": True}, daemon=True).start()
        send("브리핑을 만드는 중이에요. 잠시만요…", chat_id)
    elif text == "/todo":
        from . import todos
        items = todos.items(include_done=False)
        lines = [f"• {x['title']}" + (f" (~{x['due'][5:].replace('-', '/')})" if x.get("due") else "") for x in items]
        send("할 일\n" + ("\n".join(lines) if lines else "남은 할 일이 없어요."), chat_id)
    else:
        threading.Thread(target=_reply_with_agent, args=(chat_id, text), daemon=True).start()


def _loop(gen: int):
    offset = None
    while _state["gen"] == gen:
        token = _cfg().get("token")
        if not token:
            return
        try:
            params = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            updates = _call(token, "getUpdates", http_timeout=35, **params)
            _state["error"], _state["last_ok"] = "", time.time()
            for u in updates:
                offset = u["update_id"] + 1
                if _state["gen"] != gen:
                    return
                if "message" in u:
                    try:
                        _handle(u["message"])
                    except Exception as e:
                        _state["error"] = str(e)
        except TelegramError as e:
            _state["error"] = str(e)
            time.sleep(10)
        except Exception as e:
            _state["error"] = str(e)
            time.sleep(10)


def restart():
    _state["gen"] += 1
    _state["error"] = ""
    if _cfg().get("token"):
        threading.Thread(target=_loop, args=(_state["gen"],), daemon=True).start()
