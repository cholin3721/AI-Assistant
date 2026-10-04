"""AI가 호출할 수 있는 도구 모음. 호출 기록을 남겨 화면에 '무엇을 했는지' 보여주고,
진행 상황을 실시간(SSE)으로 흘려보낼 수 있게 콜백을 둡니다."""
import contextvars
import functools
import logging
import threading
import time

_call_log: contextvars.ContextVar = contextvars.ContextVar("call_log", default=None)
_progress: contextvars.ContextVar = contextvars.ContextVar("progress", default=None)
_tls = threading.local()

LABELS = {}

# 진행 표시에 붙일 인자 설명 (사람이 읽기 좋은 짧은 문구만)
_ARG_HINTS = {
    "category": "{}", "keyword": "‘{}’", "query": "‘{}’", "title": "‘{}’", "fact": "‘{}’",
    "days": "최근 {}일", "days_ahead": "{}일 뒤까지", "to": "→ {}", "duration_minutes": "{}분",
    "instruction": "‘{}’", "message": "‘{}’",
}


def start_log(on_event=None, count_stats: bool = True, should_stop=None):
    """한 번의 대화 처리 시작. on_event(dict)를 주면 도구 호출 시작·끝마다 호출됩니다.

    count_stats: 이 대화에서 쓴 도구를 사용 리포트(아낀 시간)에 셀지. 자동 브리핑처럼 따로 세는 작업은 False.
    should_stop: 사용자가 「중지」를 눌렀는지 알려주는 함수. True면 남은 도구 호출을 건너뜁니다.
    """
    log = []
    _call_log.set(log)
    _progress.set(on_event)
    _tls.log, _tls.on_event = log, on_event
    _tls.count_stats, _tls.should_stop, _tls.depth = count_stats, should_stop, 0


def end_log():
    """대화 처리 끝. 같은 스레드가 다른 요청에 다시 쓰여도 이전 대화의 기록·콜백이 남지 않게 비웁니다."""
    _call_log.set(None)
    _progress.set(None)
    _tls.log = _tls.on_event = _tls.should_stop = None
    _tls.count_stats, _tls.depth = False, 0


def get_log():
    return _call_log.get() or getattr(_tls, "log", None) or []


def emit(event: dict):
    """진행 이벤트 보내기 (콜백이 없으면 무시)."""
    cb = _progress.get() or getattr(_tls, "on_event", None)
    if cb is not None:
        try:
            cb(event)
        except Exception:
            pass


def stop_requested() -> bool:
    fn = getattr(_tls, "should_stop", None)
    try:
        return bool(fn and fn())
    except Exception:
        return False


def describe(name: str, kwargs: dict) -> str:
    """도구 호출을 '학교 공지 확인 · 행사 · ‘공모전’'처럼 한 줄로."""
    parts = []
    for k, v in kwargs.items():
        if k in _ARG_HINTS and v not in ("", None, 0, False):
            parts.append(_ARG_HINTS[k].format(str(v).replace("\n", " ")[:28]))
    return LABELS.get(name, name) + (" · " + " · ".join(parts[:2]) if parts else "")


class ToolError(Exception):
    pass


def tool(label: str):
    """도구 함수 데코레이터: 호출 기록 + 진행 이벤트 + 오류를 AI가 이해할 수 있는 dict로 변환."""
    def deco(fn):
        LABELS[fn.__name__] = label

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = _call_log.get() or getattr(_tls, "log", None)
            entry = {"name": fn.__name__, "label": label, "args": kwargs, "ok": True}
            if log is not None:
                log.append(entry)
            if stop_requested():   # 사용자가 「중지」를 누름 → 실제 호출 없이 바로 끝냄
                entry["ok"], entry["error"] = False, "사용자가 중지했어요."
                return {"error": "사용자가 중지했어요. 더 진행하지 말고 여기서 멈추세요."}
            text = describe(fn.__name__, kwargs)
            emit({"type": "tool_start", "name": fn.__name__, "label": label, "text": text})
            t0 = time.time()
            depth = getattr(_tls, "depth", 0)
            _tls.depth = depth + 1
            try:
                return fn(*args, **kwargs)
            except ToolError as e:
                entry["ok"] = False
                entry["error"] = str(e)
                return {"error": str(e)}
            except Exception as e:  # 네트워크·권한 오류 등
                entry["ok"] = False
                entry["error"] = f"{type(e).__name__}: {e}"
                logging.getLogger("inha").warning("도구 오류 %s: %s", fn.__name__, entry["error"][:300])
                return {"error": entry["error"]}
            finally:
                _tls.depth = depth
                emit({"type": "tool_end", "name": fn.__name__, "label": label, "text": text,
                      "ok": entry["ok"], "error": entry.get("error", ""), "ms": int((time.time() - t0) * 1000)})
                # 리포트에는 '사용자와의 대화에서 AI가 직접 고른 도구'만 셉니다.
                # (30분마다 도는 자동 확인이나, 도구 안에서 다시 부른 도구까지 세면 아낀 시간이 부풀려짐)
                if entry["ok"] and depth == 0 and getattr(_tls, "count_stats", False):
                    from .. import stats
                    stats.record(f"tool:{fn.__name__}")
        return wrapper
    return deco


def google_service(name: str, version: str):
    from googleapiclient.discovery import build
    from .. import google_auth
    creds = google_auth.get_credentials()
    if creds is None:
        raise ToolError("구글 계정이 연동되지 않았어요. 사용자에게 설정 > 구글 연동을 먼저 하라고 안내하세요.")
    return build(name, version, credentials=creds, cache_discovery=False)


def batch_execute(svc, requests: list) -> list:
    """구글 API 요청 여러 개를 한 번의 HTTP 왕복으로 처리 (Gmail 메시지 25통 → 1회).
    결과는 입력 순서대로, 실패한 항목은 None."""
    results = [None] * len(requests)
    if not requests:
        return results

    def cb(i):
        def _cb(_rid, resp, exc):
            if exc is None:
                results[i] = resp
        return _cb

    CHUNK = 50   # Gmail batch 한도는 100
    for s in range(0, len(requests), CHUNK):
        batch = svc.new_batch_http_request()
        for i in range(s, min(s + CHUNK, len(requests))):
            batch.add(requests[i], callback=cb(i))
        batch.execute()
    return results
