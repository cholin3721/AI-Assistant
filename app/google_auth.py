"""구글 계정 연동 (OAuth). 사용자의 PC에서 브라우저 로그인 → 토큰을 data/token.json에 저장."""
import json
import threading

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

_state = {"running": False, "error": ""}
_creds_cache = {"creds": None}


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


def get_credentials():
    """유효한 구글 자격 증명을 반환. 없거나 만료돼 갱신 실패하면 None."""
    creds = _creds_cache["creds"]
    if creds is None and config.TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), SCOPES)
        except Exception:
            creds = None
    if creds is None:
        return None
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                config.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            except RefreshError:
                # 테스트 모드 앱은 7일마다 토큰이 만료됨 → 다시 로그인 필요
                disconnect(keep_client=True)
                _state["error"] = "구글 로그인이 만료됐어요(테스트 모드 앱은 7일마다 만료). '구글 로그인'을 다시 눌러주세요."
                return None
        else:
            return None
    _creds_cache["creds"] = creds
    return creds


def status() -> dict:
    creds = get_credentials()
    email = _read_email_cache() if creds is not None else ""
    return {
        "client_uploaded": config.CREDENTIALS_PATH.exists(),
        "connected": creds is not None,
        "email": email,
        "login_in_progress": _state["running"],
        "error": _state["error"],
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


def _run_flow():
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            authorization_prompt_message="",
            success_message="연동 완료! 이 창을 닫고 AI 비서 화면으로 돌아가세요.",
            timeout_seconds=300,
        )
        config.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        _creds_cache["creds"] = creds
        config.save({"google_email": _fetch_email(creds)})
        _state["error"] = ""
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
    if config.TOKEN_PATH.exists():
        config.TOKEN_PATH.unlink()
    if not keep_client and config.CREDENTIALS_PATH.exists():
        config.CREDENTIALS_PATH.unlink()
    config.save({"google_email": ""})
