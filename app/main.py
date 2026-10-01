"""AI 비서 로컬 서버. 실행: python -m app.main  → 브라우저에서 http://127.0.0.1:8765"""
import re
import sys
import threading
import uuid
import webbrowser

from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agent, config, discord_bot, forms, google_auth, memory, notify, schedule_finder, scheduler,
               stats, telegram_bot, timetable, todos)
from .tools import ToolError

app = FastAPI(title="인하 AI 비서")
STATIC = config.BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class KeyIn(BaseModel):
    key: str


class SettingsIn(BaseModel):
    model: str | None = None
    profile: dict | None = None
    setup_done: bool | None = None
    auto_scan: bool | None = None
    automation: dict | None = None


class ChatIn(BaseModel):
    session_id: str
    message: str


class ScanIn(BaseModel):
    days: int = 7
    force: bool = False


class CandidateEdit(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None


class TextIn(BaseModel):
    text: str


class TodoIn(BaseModel):
    title: str
    due: str = ""
    note: str = ""


class TimetableIn(BaseModel):
    classes: list[dict]


class JobIn(BaseModel):
    job: str


class FeedbackIn(BaseModel):
    rating: str
    tools: list[str] = []
    reason: str = ""


class FormTextIn(BaseModel):
    text: str
    notes: str = ""
    use_profile: bool = True
    style: str = "report"


class FormNoticeIn(BaseModel):
    attachment_url: str
    notice_url: str = ""
    notes: str = ""
    use_profile: bool = True
    style: str = "report"


class FieldIn(BaseModel):
    value: str = ""
    instruction: str = ""


class DisconnectIn(BaseModel):
    remove_client: bool = False


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    cfg = config.load()
    return {
        "gemini": {"set": bool(cfg["gemini_api_key"]), "masked": config.mask(cfg["gemini_api_key"]),
                   "model": cfg.get("model", "")},
        "google": google_auth.status(),
        "profile": cfg["profile"],
        "setup_done": cfg.get("setup_done", False),
        "schedule": {"enabled": cfg.get("auto_scan", True), "running": schedule_finder.is_running(),
                     "pending": len(schedule_finder.list_candidates("pending")),
                     "last_result": scheduler.status()["results"].get("mail_scan", {}).get("message", "")},
        "automation": cfg["automation"],
        "telegram": telegram_bot.status(),
        "discord": discord_bot.status(),
        "notifications": {"unread": notify.unread()},
        "todos": {"open": len(todos.items())},
        "forms": {"drafts": len(forms.list_drafts())},
    }


@app.post("/api/gemini/key")
def set_key(body: KeyIn):
    key = body.key.strip()
    if not key:
        raise HTTPException(400, "키를 입력해주세요.")
    if not key.startswith("AIza") and not key.startswith("AQ."):
        # 형식이 달라도 시도는 하되, 흔한 실수를 먼저 알려줌
        pass
    try:
        info = agent.validate_key(key)
    except agent.AgentError as e:
        raise HTTPException(400, str(e))
    config.save({"gemini_api_key": key, "model": info["default"]})
    agent.reset_all()
    return {"ok": True, "model": info["default"], "models": info["models"], "masked": config.mask(key)}


@app.get("/api/gemini/models")
def models():
    cfg = config.load()
    if not cfg["gemini_api_key"]:
        return {"models": [], "current": ""}
    try:
        return {"models": agent.list_models(cfg["gemini_api_key"]), "current": cfg.get("model", "")}
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))


@app.post("/api/settings")
def settings(body: SettingsIn):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "automation" in upd:
        allowed = set(config.DEFAULTS["automation"])
        upd["automation"] = {k: v for k, v in upd["automation"].items() if k in allowed}
    if "profile" in upd:
        upd["profile"] = {k: str(v)[:200] for k, v in upd["profile"].items()
                          if k in ("name", "department", "grade", "interests")}
    config.save(upd)
    if "model" in upd:
        agent.reset_all()
    return {"ok": True}


@app.post("/api/google/client")
async def upload_client(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 100_000:
        raise HTTPException(400, "파일이 너무 커요. client_secret_….json 파일이 맞는지 확인해주세요.")
    problem = google_auth.validate_client_file(raw)
    if problem:
        raise HTTPException(400, problem)
    config.CREDENTIALS_PATH.write_bytes(raw)
    google_auth.disconnect(keep_client=True)
    return {"ok": True}


@app.post("/api/google/login")
def google_login():
    res = google_auth.start_login()
    if not res["ok"]:
        raise HTTPException(400, res["error"])
    return res


@app.post("/api/google/disconnect")
def google_disconnect(body: DisconnectIn):
    google_auth.disconnect(keep_client=not body.remove_client)
    agent.reset_all()
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn):
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "메시지를 입력해주세요.")
    try:
        return agent.chat(body.session_id or uuid.uuid4().hex, msg[:4000])
    except agent.AgentError as e:
        raise HTTPException(400, str(e))


@app.post("/api/chat/reset")
def chat_reset(body: ChatIn):
    agent.reset(body.session_id)
    return {"ok": True}


@app.on_event("startup")
def _startup():
    scheduler.start()
    telegram_bot.restart()
    discord_bot.restart()


def _bad(e: Exception):
    return HTTPException(400, str(e))


# ---------- 알림 ----------
@app.get("/api/notifications")
def notifications():
    return {"items": notify.items(), "unread": notify.unread()}


@app.post("/api/notifications/read")
def notifications_read():
    notify.mark_all_read()
    return {"ok": True}


# ---------- 자동화 ----------
@app.get("/api/automation")
def automation_status():
    return {"settings": config.load()["automation"], **scheduler.status()}


@app.post("/api/automation/run")
def automation_run(body: JobIn):
    try:
        return {"message": scheduler.run_now(body.job)}
    except ValueError:
        raise HTTPException(400, "알 수 없는 작업이에요.")
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))


# ---------- 텔레그램 ----------
@app.post("/api/telegram/token")
def telegram_token(body: TextIn):
    try:
        return telegram_bot.setup_token(body.text)
    except telegram_bot.TelegramError as e:
        raise _bad(e)


@app.post("/api/telegram/test")
def telegram_test():
    try:
        telegram_bot.send("테스트 메시지예요. 알림이 잘 도착했어요!")
    except telegram_bot.TelegramError as e:
        raise _bad(e)
    return {"ok": True}


@app.post("/api/telegram/disconnect")
def telegram_disconnect():
    telegram_bot.disconnect()
    return {"ok": True}


# ---------- 디스코드 ----------
@app.post("/api/discord/token")
def discord_token(body: TextIn):
    try:
        return discord_bot.setup_token(body.text)
    except discord_bot.DiscordError as e:
        raise _bad(e)


@app.post("/api/discord/test")
def discord_test():
    try:
        discord_bot.send("테스트 메시지예요. 알림이 잘 도착했어요!")
    except discord_bot.DiscordError as e:
        raise _bad(e)
    return {"ok": True}


@app.post("/api/discord/disconnect")
def discord_disconnect():
    discord_bot.disconnect()
    return {"ok": True}


# ---------- 사용 통계 ----------
@app.get("/api/stats")
def stats_summary(days: int = 30):
    return stats.summary(max(0, min(days, 365)))


@app.get("/api/stats/export")
def stats_export():
    from datetime import datetime
    name = f"ai-assistant-stats-{datetime.now():%Y%m%d}.json"
    return JSONResponse(stats.export(), headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/feedback")
def feedback(body: FeedbackIn):
    try:
        stats.feedback(body.rating, body.tools, body.reason)
    except ValueError:
        raise HTTPException(400, "평가 값이 올바르지 않아요.")
    return {"ok": True}


# ---------- 신청서 도우미 ----------
def _form_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (forms.FormError, ToolError) as e:
        raise _bad(e)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))


@app.get("/api/forms")
def forms_list():
    return {"drafts": forms.list_drafts()}


@app.get("/api/forms/notice_attachments")
def forms_notice_attachments(url: str):
    from .tools.notices import read_school_notice
    view = read_school_notice(url=url.strip())
    if "error" in view:
        raise HTTPException(400, view["error"])
    return {"title": view.get("title", ""), "attachments": view.get("attachments", [])}


@app.post("/api/forms/upload")
async def forms_upload(file: UploadFile = File(...), notes: str = Form(""), use_profile: bool = Form(True),
                       style: str = Form("report")):
    from .attachments import extract_text
    data = await file.read()
    if len(data) > 15_000_000:
        raise HTTPException(400, "15MB 이하 파일만 올릴 수 있어요.")
    res = extract_text(data, file.filename or "", max_chars=forms.MAX_FORM_CHARS)
    if not res.get("text"):
        raise HTTPException(400, res.get("error") or "양식을 읽지 못했어요.")
    return _form_call(forms.create, res["text"], {"type": "upload", "name": file.filename or "양식"},
                      notes, use_profile, style)


@app.post("/api/forms/from_text")
def forms_from_text(body: FormTextIn):
    return _form_call(forms.create, body.text, {"type": "text", "name": "붙여넣은 양식"},
                      body.notes, body.use_profile, body.style)


@app.post("/api/forms/from_notice")
def forms_from_notice(body: FormNoticeIn):
    return _form_call(forms.from_notice, body.attachment_url, body.notice_url, body.notes,
                      body.use_profile, body.style)


@app.get("/api/forms/{did}")
def forms_get(did: str):
    return _form_call(forms.get, did)


@app.put("/api/forms/{did}/fields/{fid}")
def forms_save_field(did: str, fid: str, body: FieldIn):
    return _form_call(forms.save_field, did, fid, body.value)


@app.post("/api/forms/{did}/fields/{fid}/rewrite")
def forms_rewrite(did: str, fid: str, body: FieldIn):
    return _form_call(forms.rewrite, did, fid, body.instruction)


@app.delete("/api/forms/{did}")
def forms_delete(did: str):
    return {"ok": forms.delete(did)}


@app.get("/api/forms/{did}/docx")
def forms_docx(did: str):
    d = _form_call(forms.get, did)
    name = re.sub(r'[\\/:*?"<>|]', "", d["title"])[:60] or "신청서"
    return Response(forms.to_docx(d), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename=\"draft.docx\"; filename*=UTF-8''{quote(name + '_초안.docx')}"})


# ---------- 할 일 ----------
@app.get("/api/todos")
def todo_list(include_done: bool = True):
    return {"items": todos.items(include_done)}


@app.post("/api/todos")
def todo_add(body: TodoIn):
    try:
        return todos.add(body.title, body.due, body.note)
    except ToolError as e:
        raise _bad(e)


@app.post("/api/todos/{tid}/toggle")
def todo_toggle(tid: str):
    cur = next((x for x in todos.items(True) if x["id"] == tid), None)
    if cur is None:
        raise HTTPException(404, "할 일을 찾을 수 없어요.")
    return todos.set_done(tid, not cur["done"])


@app.delete("/api/todos/{tid}")
def todo_delete(tid: str):
    return {"ok": todos.delete(tid)}


# ---------- 기억 ----------
@app.get("/api/memory")
def memory_list():
    return {"facts": memory.facts()}


@app.post("/api/memory")
def memory_add(body: TextIn):
    try:
        return memory.add_fact(body.text)
    except ToolError as e:
        raise _bad(e)


@app.delete("/api/memory/{fid}")
def memory_delete(fid: str):
    return {"ok": memory.delete_fact(fid)}


# ---------- 시간표 ----------
@app.get("/api/timetable")
def timetable_get():
    return {"classes": timetable.get()}


@app.put("/api/timetable")
def timetable_put(body: TimetableIn):
    return {"classes": timetable.replace(body.classes)}


@app.post("/api/timetable/extract")
async def timetable_extract(file: UploadFile = File(...)):
    data = await file.read()
    mime = file.content_type or "image/png"
    if not mime.startswith("image/") or len(data) > 10_000_000:
        raise HTTPException(400, "10MB 이하의 이미지 파일(png, jpg)을 올려주세요.")
    try:
        return {"classes": timetable.extract_from_image(data, mime)}
    except ToolError as e:
        raise _bad(e)
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))


@app.post("/api/schedule/scan_notices")
def schedule_scan_notices(body: ScanIn):
    try:
        res = schedule_finder.scan_notices(days=max(1, min(body.days, 30)), force=body.force)
    except schedule_finder.ScanError as e:
        raise _bad(e)
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))
    return {**res, "candidates": schedule_finder.list_candidates("pending")}


@app.get("/api/schedule/candidates")
def schedule_candidates(status: str = "pending"):
    return {"candidates": schedule_finder.list_candidates(status), "running": schedule_finder.is_running()}


@app.post("/api/schedule/scan")
def schedule_scan(body: ScanIn):
    try:
        res = schedule_finder.scan(days=max(1, min(body.days, 30)), force=body.force)
    except schedule_finder.ScanError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, agent.friendly_error(e))
    return {**res, "candidates": schedule_finder.list_candidates("pending")}


@app.post("/api/schedule/{cid}/add")
def schedule_add(cid: str, body: CandidateEdit):
    try:
        return schedule_finder.add_to_calendar(cid, body.model_dump())
    except schedule_finder.ScanError as e:
        raise HTTPException(400, str(e))


@app.post("/api/schedule/{cid}/ignore")
def schedule_ignore(cid: str):
    try:
        return schedule_finder.ignore(cid)
    except schedule_finder.ScanError as e:
        raise HTTPException(400, str(e))


def run():
    import uvicorn
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  인하 AI 비서가 실행됐어요 → {url}\n  (이 창을 닫으면 비서도 꺼집니다)\n")
    if "--silent" not in sys.argv:   # PC 시작 시 자동 실행일 때는 브라우저를 열지 않음
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    run()
