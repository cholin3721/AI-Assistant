"""디스코드 봇: 디스코드 DM으로 알림 받기 + DM으로 비서와 대화.

연결 순서
  1) 개발자 포털에서 앱 만들기 → Bot 메뉴에서 토큰 복사 → 화면에 붙여넣기
  2) 화면의 「내 서버에 봇 초대하기」로 내 서버에 초대 (디스코드는 같은 서버에 있어야 DM을 주고받을 수 있음)
  3) 봇이 서버 주인에게 인사 DM을 보냄 → 화면에 보이는 6자리 연결 코드를 답장 → 그 사람만 '주인'으로 등록
DM만 사용하므로 특수 권한(Message Content Intent)을 켤 필요가 없습니다. 주인 외의 메시지에는 응답하지 않습니다.
"""
import asyncio
import random
import re
import threading
import time

import requests

from . import config

API = "https://discord.com/api/v10"
LIMIT = 1900   # 디스코드 메시지 최대 2000자
EMPTY = {"token": "", "bot_username": "", "app_id": "", "owner_id": None, "owner_name": "", "link_code": ""}
_state = {"gen": 0, "error": "", "ready": False, "loop": None, "client": None}

HELP = ("인하 AI 비서예요. 여기서 그냥 말 걸면 돼요.\n"
        "예) 오늘 일정 알려줘 / 안 읽은 메일 요약해줘 / 장학금 공지 있어?\n\n"
        "`브리핑` 지금 브리핑 받기 · `할 일` 할 일 보기 · `도움말` 이 안내")


class DiscordError(Exception):
    pass


def _cfg() -> dict:
    return config.load().get("discord") or {}


def _rest(token: str, path: str) -> dict:
    try:
        r = requests.get(API + path, headers={"Authorization": f"Bot {token}"}, timeout=15)
    except requests.RequestException as e:
        raise DiscordError(f"디스코드 서버에 접속하지 못했어요 ({type(e).__name__})")
    if r.status_code == 401:
        raise DiscordError("봇 토큰이 올바르지 않아요. 개발자 포털 Bot 메뉴에서 「Reset Token」 후 새 토큰을 복사해주세요.")
    if not r.ok:
        raise DiscordError(f"디스코드 오류 ({r.status_code})")
    return r.json()


# ---------- 설정 ----------
def clean_token(token: str) -> str:
    token = token.strip().strip('"').strip("'")
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    return token


def setup_token(token: str) -> dict:
    token = clean_token(token)
    if not re.fullmatch(r"[\w-]{20,}\.[\w-]{4,}\.[\w-]{20,}", token):
        raise DiscordError("토큰 형식이 아니에요. Bot 메뉴의 「Reset Token」으로 받은 긴 토큰 전체를 붙여넣어 주세요. "
                           "(General Information의 Application ID나 Public Key가 아니에요)")
    app = _rest(token, "/oauth2/applications/@me")
    me = _rest(token, "/users/@me")
    code = f"{random.randint(0, 999999):06d}"
    config.save({"discord": {**EMPTY, "token": token, "bot_username": me.get("username", ""),
                             "app_id": str(app.get("id") or me.get("id") or ""), "link_code": code}})
    restart()
    return status()


def status() -> dict:
    d = _cfg()
    linked = bool(d.get("owner_id"))
    app_id = d.get("app_id")
    return {
        "token_set": bool(d.get("token")),
        "bot_username": d.get("bot_username", ""),
        "linked": linked,
        "owner_name": d.get("owner_name", ""),
        "invite_url": f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions=0" if app_id else "",
        "link_code": "" if linked else d.get("link_code", ""),
        "online": _state["ready"],
        "error": _state["error"],
    }


def disconnect():
    config.save({"discord": dict(EMPTY)})
    restart()


# ---------- 메시지 처리 (디스코드와 무관한 순수 로직 — 테스트 가능) ----------
def chunks(text: str, size: int = LIMIT):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def handle_dm(author_id, author_name: str, text: str):
    """DM 한 통 처리 → (바로 보낼 답장 목록, 후속 작업 None|'agent'|'brief')."""
    d = _cfg()
    owner = d.get("owner_id")
    text = (text or "").strip()

    if not owner:  # 아직 주인 없음 → 연결 코드 확인
        code = d.get("link_code")
        if code and code in re.sub(r"\s", "", text):
            config.save({"discord": {**d, "owner_id": str(author_id), "owner_name": author_name, "link_code": ""}})
            return ["연결됐어요! 이제 여기서 알림을 받고, 비서에게 바로 말 걸 수 있어요.\n\n" + HELP], None
        return ["AI 비서 화면(설정 > 메신저)에 보이는 **6자리 연결 코드**를 보내주세요."], None

    if str(author_id) != str(owner):
        return ["이 비서는 주인만 사용할 수 있어요."], None
    if not text:
        return ["지금은 글자 메시지만 이해할 수 있어요."], None
    low = text.lower().lstrip("/!")
    if low in ("help", "도움말", "start"):
        return [HELP], None
    if low in ("brief", "브리핑"):
        return ["브리핑을 만드는 중이에요. 잠시만요…"], "brief"
    if low in ("todo", "할 일", "할일"):
        from . import todos
        items = todos.items(include_done=False)
        lines = [f"• {x['title']}" + (f" (~{x['due'][5:].replace('-', '/')})" if x.get("due") else "") for x in items]
        return ["**할 일**\n" + ("\n".join(lines) if lines else "남은 할 일이 없어요.")], None
    return [], "agent"


def _agent_reply(text: str) -> str:
    from . import agent
    try:
        res = agent.chat("discord", text)
    except Exception as e:
        return str(e)
    reply = res["reply"]
    used = sorted({x["label"] for x in res.get("tools", [])})
    return reply + ("\n\n-# " + " · ".join(used) if used else "")


# ---------- 디스코드 연결 ----------
def _make_client():
    import discord
    intents = discord.Intents.none()
    intents.guilds = True        # 서버 초대 감지 (주인에게 인사 DM)
    intents.dm_messages = True   # DM 받기 — DM은 Message Content Intent 없이도 내용이 옴
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        _state["ready"], _state["error"] = True, ""

    @client.event
    async def on_disconnect():
        _state["ready"] = False

    @client.event
    async def on_guild_join(guild):
        d = _cfg()
        if d.get("owner_id") or not d.get("link_code"):
            return
        try:
            owner = guild.owner or await client.fetch_user(guild.owner_id)
            await owner.send("안녕하세요! 인하 AI 비서예요.\n연결을 마치려면 AI 비서 화면(설정 > 메신저)에 보이는 "
                             "**6자리 연결 코드**를 여기로 보내주세요.")
        except Exception as e:
            _state["error"] = f"인사 메시지를 보내지 못했어요. 서버 멤버 목록에서 봇을 눌러 직접 메시지를 보내주세요. ({e})"

    @client.event
    async def on_message(message):
        if message.author.bot or message.guild is not None:
            return  # 서버 채널 메시지는 무시, DM만 처리
        replies, action = handle_dm(message.author.id, message.author.display_name, message.content)
        for r in replies:
            for part in chunks(r):
                await message.channel.send(part, suppress_embeds=True)
        if action == "brief":
            from . import scheduler
            threading.Thread(target=scheduler.run_briefing, kwargs={"manual": True}, daemon=True).start()
        elif action == "agent":
            async with message.channel.typing():
                reply = await asyncio.get_running_loop().run_in_executor(None, _agent_reply, message.content.strip())
            for part in chunks(reply):
                await message.channel.send(part, suppress_embeds=True)

    return client


def _runner(gen: int, token: str):
    import discord
    while _state["gen"] == gen:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = _make_client()
        _state.update(loop=loop, client=client, ready=False)
        fatal = False
        try:
            loop.run_until_complete(client.start(token))   # 연결이 끊겨도 내부에서 자동 재접속
        except discord.LoginFailure:
            _state["error"] = "봇 토큰이 올바르지 않아요. 토큰을 다시 등록해주세요."
            fatal = True
        except Exception as e:
            if _state["gen"] == gen:
                _state["error"] = f"디스코드 연결 오류: {type(e).__name__}"
        finally:
            _state["ready"] = False
            try:
                if not client.is_closed():
                    loop.run_until_complete(client.close())
            except Exception:
                pass
            loop.close()
        if fatal:
            return
        time.sleep(30)   # 인터넷이 끊겼다면 30초 뒤 다시 시도


def restart():
    _state["gen"] += 1
    client, loop = _state.get("client"), _state.get("loop")
    if client is not None and loop is not None and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(client.close(), loop)
        except RuntimeError:
            pass
    _state.update(error="", ready=False, client=None, loop=None)
    token = _cfg().get("token")
    if token:
        threading.Thread(target=_runner, args=(_state["gen"], token), daemon=True).start()


# ---------- 보내기 (다른 스레드에서 호출) ----------
def send(text: str):
    owner = _cfg().get("owner_id")
    if not owner:
        raise DiscordError("디스코드가 연결되지 않았어요.")
    loop, client = _state.get("loop"), _state.get("client")
    if not (loop and client and _state["ready"]):
        raise DiscordError("디스코드 봇이 아직 접속 중이에요. 잠시 후 다시 시도해주세요.")

    async def go():
        user = client.get_user(int(owner)) or await client.fetch_user(int(owner))
        for part in chunks(text):
            await user.send(part, suppress_embeds=True)
    asyncio.run_coroutine_threadsafe(go(), loop).result(timeout=30)


def send_safe(text: str):
    """알림용: 연결 안 돼 있거나 실패해도 조용히 넘어감."""
    if not _cfg().get("owner_id"):
        return
    try:
        send(text)
    except Exception as e:
        _state["error"] = f"알림 전송 실패: {e}"
