"""AI가 호출할 수 있는 도구 모음. 호출 기록을 남겨 화면에 '무엇을 했는지' 보여줍니다."""
import contextvars
import functools

_call_log: contextvars.ContextVar = contextvars.ContextVar("call_log", default=None)

LABELS = {}


def start_log():
    _call_log.set([])


def get_log():
    return _call_log.get() or []


class ToolError(Exception):
    pass


def tool(label: str):
    """도구 함수 데코레이터: 호출 기록 + 오류를 AI가 이해할 수 있는 dict로 변환."""
    def deco(fn):
        LABELS[fn.__name__] = label

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = _call_log.get()
            entry = {"name": fn.__name__, "label": label, "args": kwargs, "ok": True}
            if log is not None:
                log.append(entry)
            try:
                return fn(*args, **kwargs)
            except ToolError as e:
                entry["ok"] = False
                return {"error": str(e)}
            except Exception as e:  # 네트워크·권한 오류 등
                entry["ok"] = False
                return {"error": f"{type(e).__name__}: {e}"}
        return wrapper
    return deco


def google_service(name: str, version: str):
    from googleapiclient.discovery import build
    from .. import google_auth
    creds = google_auth.get_credentials()
    if creds is None:
        raise ToolError("구글 계정이 연동되지 않았어요. 사용자에게 설정 > 구글 연동을 먼저 하라고 안내하세요.")
    return build(name, version, credentials=creds, cache_discovery=False)
