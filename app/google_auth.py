"""구글 계정 연동 (OAuth). 사용자의 PC에서 브라우저 로그인 → 토큰을 data/token.json에 저장."""
import json
import logging
import threading
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",    # 메일 읽기
    "https://www.googleapis.com/auth/gmail.compose",     # 답장 '초안' 작성
    "https://www.googleapis.com/auth/calendar.events",   # 일정 조회·등록
    "https://www.googleapis.com/auth/drive.readonly",    # 드라이브 파일 읽기
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_state = {"running": False, "error": "", "offline": False, "retry_at": 0.0}
_creds_cache = {"creds": None}
_lock = threading.RLock()
log = logging.getLogger("inha")
RETRY_SEC = 60          # 갱신에 실패하면 이 시간 동안은 다시 시도하지 않음 (화면이 매번 기다리지 않게)
REFRESH_TIMEOUT = 10    # 토큰 갱신 요청 제한 시간(초). 기본값 120초는 화면이 멈춘 것처럼 보임
EXPIRED_MSG = "구글 로그인이 만료됐어요(테스트 모드 앱은 7일마다 만료). '구글 로그인'을 다시 눌러주세요."
OFFLINE_MSG = "인터넷 연결이 불안정해 구글에 접속하지 못했어요. 연결되면 자동으로 다시 이어져요."


class _QuickRequest(Request):
    """토큰 갱신용 요청: 제한 시간을 짧게."""
    def __call__(self, url, method="GET", body=None, headers=None, timeout=REFRESH_TIMEOUT, **kwargs):
        return super().__call__(url, method=method, body=body, headers=headers,
                                timeout=min(timeout or REFRESH_TIMEOUT, REFRESH_TIMEOUT), **kwargs)


def validate_client_file(raw: bytes) -> str:
    """업로드한 JSON이 '데스크톱 앱' 유형 OAuth 클라이언트인지 확인. 문제 있으면 안내 문구 반환."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return "JSON 파일이 아니에요. 구글 클라우드에서 다운로드한 client_secret_….json 파일을 올려주세요."
    if "web" in data:
        return "'웹 애플리케이션' 유형으로 만들어졌어요. 클라이언트를 새로 만들 때 애플리케이션 유형을 '데스크톱 앱'으로 골라주세요."
    if "installed" not in data:
        return "OAuth 클라이언트 파일이 아니에요. (서비스 계정 키나 API 키 파일을 올리신 건 아닌지 확인해주세요.)"
    inst = data["installed"]
    if not inst.get("client_id") or not inst.get("client_secret"):
        return "client_id 또는 client_secret이 비어 있어요. 파일을 다시 다운로드해주세요."
    return ""


def _needs_relogin(e: Exception) -> bool:
    """갱신 실패가 '다시 로그인해야 하는' 종류인지 (만료·취소·클라이언트 삭제)."""
    msg = str(e).lower()
    return any(x in msg for x in ("invalid_grant", "invalid_client", "unauthorized_client", "deleted_client",
                                  "expired or revoked", "account has been deleted"))


def get_credentials():
    """유효한 구글 자격 증명을 반환. 없거나, 만료돼 다시 로그인해야 하거나, 지금 인터넷이 안 되면 None.
    어떤 경우에도 예외를 던지지 않습니다 (화면 상태 조회·스케줄러가 이 함수 때문에 멈추지 않게)."""
    expired = False
    with _lock:
        creds = _creds_cache["creds"]
        if creds is None and config.TOKEN_PATH.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), SCOPES)
            except Exception:
                creds = None
        if creds is None:
            return None
        if creds.valid:
            _creds_cache["creds"] = creds
            return creds
        if not (creds.expired and creds.refresh_token):
            return None
        if time.time() < _state["retry_at"]:
            return None
        try:
            creds.refresh(_QuickRequest())
        except Exception as e:
            if isinstance(e, RefreshError) and _needs_relogin(e):
                # 테스트 모드 앱은 7일마다 토큰이 만료됨 → 다시 로그인 필요
                disconnect(keep_client=True)
                _state.update(error=EXPIRED_MSG, offline=False, retry_at=0.0)
                expired = True
                log.warning("구글 로그인 만료: %s", str(e)[:200])
            else:
                # 와이파이 끊김·구글 일시 오류: 토큰은 그대로 두고 잠시 뒤 다시 시도
                _creds_cache["creds"] = creds
                _state.update(error=OFFLINE_MSG, offline=True, retry_at=time.time() + RETRY_SEC)
                log.warning("구글 토큰 갱신 실패(일시적): %s: %s", type(e).__name__, str(e)[:200])
                return None
        else:
            try:
                config.write_atomic(config.TOKEN_PATH, creds.to_json())
            except OSError:
                pass
            if _state["offline"]:
                _state.update(error="", offline=False)
            _state["retry_at"] = 0.0
            _creds_cache["creds"] = creds
            return creds
    if expired:
        _notify_expired()
    return None


def _notify_expired():
    """만료를 사용자에게 한 번 알림 — 알려주지 않으면 메일 일정 찾기가 조용히 멈춘 채로 남음."""
    try:
        from . import notify
        notify.add("system", "구글 로그인이 만료됐어요",
                   "메일·캘린더·드라이브 기능이 잠시 멈췄어요. 화면 왼쪽 아래 **설정 > 구글**에서 "
                   "「구글 로그인」을 한 번만 다시 눌러주세요.\n\n"
                   "(테스트 모드 앱은 7일마다 만료돼요. 구글 클라우드에서 앱을 '프로덕션'으로 바꾸면 사라져요.)")
    except Exception:
        pass


def status() -> dict:
    creds = get_credentials()
    email = _read_email_cache() if creds is not None else ""
    return {
        "client_uploaded": config.CREDENTIALS_PATH.exists(),
        "connected": creds is not None,
        "email": email,
        "login_in_progress": _state["running"],
        "error": _state["error"],
        "offline": bool(_state["offline"] and creds is None),   # 로그인은 돼 있는데 지금 접속만 안 되는 상태
    }


def _read_email_cache() -> str:
    return config.load().get("google_email", "")


def _fetch_email(creds) -> str:
    try:
        from googleapiclient.discovery import build
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return svc.userinfo().get().execute().get("email", "")
    except Exception:
        return ""


# ---------- 로그인 완료 화면 (구글 로그인 후 브라우저에 뜨는 페이지) ----------
SCOPE_LABELS = [("gmail.readonly", "Gmail 읽기"), ("gmail.compose", "답장 초안 작성"),
                ("calendar.events", "캘린더 일정"), ("drive.readonly", "드라이브 읽기")]

_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · 인하 AI 비서</title>
<style>
:root{{--bg:#f5f7fb;--card:#fff;--text:#18212f;--muted:#5d6b80;--line:#e2e7ef;--accent:#1f5fd1;--ok:#1a9b5c;--bad:#d23b3b;--chip:#eef3fc}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f141c;--card:#171e29;--text:#e4e9f1;--muted:#97a3b6;--line:#273142;--accent:#5b93ff;--chip:#1c2b45}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;background:var(--bg);color:var(--text);
font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}}
.card{{width:min(440px,100%);background:var(--card);border:1px solid var(--line);border-radius:20px;padding:36px 32px;
text-align:center;box-shadow:0 12px 40px rgba(15,30,60,.10)}}
.brand{{display:inline-flex;align-items:center;gap:8px;color:var(--muted);font-size:14px;font-weight:600;margin-bottom:22px}}
.logo{{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#4f8cff,#7a5cff);color:#fff;
display:grid;place-items:center;font-weight:800;font-size:12px}}
.icon{{width:72px;height:72px;border-radius:50%;margin:0 auto 18px;display:grid;place-items:center;
background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c);animation:pop .45s cubic-bezier(.2,1.4,.4,1)}}
.icon svg{{width:38px;height:38px}}
@keyframes pop{{from{{transform:scale(.4);opacity:0}}to{{transform:scale(1);opacity:1}}}}
h1{{font-size:22px;margin:0 0 8px}}
p{{color:var(--muted);margin:0 0 20px;line-height:1.6}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin:0 0 24px}}
.chip{{font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;background:var(--chip);color:var(--accent)}}
a.btn{{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;font-weight:700;padding:12px 22px;border-radius:12px}}
small{{display:block;margin-top:16px;color:var(--muted);font-size:13px}}
</style></head><body><main class="card" style="--c:{color}">
<div class="brand"><span class="logo">AI</span>인하 AI 비서</div>
<div class="icon">{icon}</div>
<h1>{title}</h1><p>{message}</p>{chips}
<a class="btn" href="{app_url}">AI 비서로 돌아가기</a>
<small>이 탭은 닫아도 돼요.</small>
</main></body></html>"""

_ICON_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
_ICON_BAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M7 7l10 10M17 7L7 17"/></svg>'


def render_result_page(query: dict) -> str:
    """구글이 돌려준 주소의 query로 성공/실패 화면을 만듦."""
    import html
    app_url = f"http://{config.HOST}:{config.PORT}/"
    error = (query.get("error") or [""])[0]
    if error or not query.get("code"):
        if error == "access_denied":
            msg = ("권한 허용을 취소했거나, 테스트 사용자로 등록되지 않은 계정이에요.<br>"
                   "구글 클라우드 ‘대상’ 화면에서 내 Gmail을 테스트 사용자로 추가했는지 확인한 뒤 다시 시도해주세요.")
        else:
            msg = f"구글 로그인이 완료되지 않았어요. ({html.escape(error or '응답 없음')})<br>AI 비서 화면에서 다시 시도해주세요."
        return _PAGE.format(title="연동하지 못했어요", message=msg, icon=_ICON_BAD, color="#d23b3b",
                            chips="", app_url=app_url)
    granted = " ".join(query.get("scope", [""]))
    chips = "".join(f'<span class="chip">{label}</span>' for key, label in SCOPE_LABELS if key in granted)
    return _PAGE.format(title="구글 연동 완료!", message="이제 비서가 메일·일정·드라이브를 도와줄 수 있어요.",
                        icon=_ICON_OK, color="#1a9b5c",
                        chips=f'<div class="chips">{chips}</div>' if chips else "", app_url=app_url)


def _install_pretty_page():
    """google_auth_oauthlib의 기본 완료 화면(흰 바탕 글자 한 줄)을 꾸민 HTML로 바꿈."""
    import wsgiref.util
    from urllib.parse import parse_qs, urlparse

    from google_auth_oauthlib import flow as _flow
    base = getattr(_flow, "_RedirectWSGIApp", None)
    if base is None or getattr(base, "_inha_pretty", False):
        return

    class PrettyRedirectApp(base):
        _inha_pretty = True

        def __call__(self, environ, start_response):
            self.last_request_uri = wsgiref.util.request_uri(environ)
            query = parse_qs(urlparse(self.last_request_uri).query)
            start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
            return [render_result_page(query).encode("utf-8")]

    _flow._RedirectWSGIApp = PrettyRedirectApp


def _run_flow():
    try:
        try:
            _install_pretty_page()
        except Exception:
            pass  # 라이브러리 구조가 바뀌어도 로그인 자체는 되도록
        flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            authorization_prompt_message="",
            success_message="연동 완료! 이 창을 닫고 AI 비서 화면으로 돌아가세요.",  # 꾸민 화면을 못 쓸 때의 대체 문구
            timeout_seconds=300,
        )
        config.write_atomic(config.TOKEN_PATH, creds.to_json())
        _creds_cache["creds"] = creds
        config.save({"google_email": _fetch_email(creds)})
        _state.update(error="", offline=False, retry_at=0.0)
    except Exception as e:  # 사용자가 창을 닫거나 시간 초과 등
        msg = str(e)
        if "access_denied" in msg or "403" in msg:
            msg = ("접근이 거부됐어요. 구글 클라우드 '대상(Audience)' 화면에서 내 Gmail 주소를 "
                   "'테스트 사용자'로 추가했는지 확인해주세요.")
        elif "timed out" in msg.lower() or "timeout" in msg.lower():
            msg = "5분 안에 로그인이 끝나지 않았어요. 다시 시도해주세요."
        _state["error"] = msg or "로그인 중 오류가 발생했어요."
    finally:
        _state["running"] = False


def start_login() -> dict:
    if not config.CREDENTIALS_PATH.exists():
        return {"ok": False, "error": "먼저 OAuth 클라이언트 파일(JSON)을 올려주세요."}
    if _state["running"]:
        return {"ok": True}
    _state["running"] = True
    _state["error"] = ""
    threading.Thread(target=_run_flow, daemon=True).start()
    return {"ok": True}


def disconnect(keep_client: bool = True):
    _creds_cache["creds"] = None
    _state.update(offline=False, retry_at=0.0)
    if config.TOKEN_PATH.exists():
        config.TOKEN_PATH.unlink()
    if not keep_client and config.CREDENTIALS_PATH.exists():
        config.CREDENTIALS_PATH.unlink()
    config.save({"google_email": ""})
