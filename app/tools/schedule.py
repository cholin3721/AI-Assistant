from . import tool, ToolError


@tool("메일에서 일정 찾기")
def find_events_in_emails(days: int = 7) -> dict:
    """최근 메일을 읽고 회의·발표·행사·마감일 같은 일정을 찾아 화면의 '일정 후보' 카드로 만듭니다.
    캘린더에 바로 넣지 않습니다. 사용자가 카드에서 확인하고 「캘린더에 추가」를 눌러야 등록됩니다.
    "메일 보고 일정 잡아줘", "메일에 있는 일정 정리해줘"처럼 메일 내용에서 일정을 찾으라는 요청에 사용하세요.

    Args:
        days: 최근 며칠 메일을 볼지 (1~30, 기본 7).
    """
    from ..schedule_finder import ScanError, list_candidates, scan
    try:
        res = scan(days=max(1, min(int(days), 30)))
    except ScanError as e:
        raise ToolError(str(e))
    pending = list_candidates("pending")
    slim = lambda c: {k: c[k] for k in ("title", "start", "end", "location", "mail_subject", "confidence")}
    return {
        "result": res["message"],
        "new_candidates": [slim(c) for c in res["new"]],
        "all_pending_candidates": [slim(c) for c in pending],
        "how_to_confirm": "화면 왼쪽 '일정 후보'에서 확인 후 「캘린더에 추가」",
    }


@tool("공지에서 일정 찾기")
def find_events_in_notices(days: int = 7) -> dict:
    """최근 학교 공지 중 사용자에게 맞는 것(공모전·장학·특강·학사)을 골라 본문과 첨부를 읽고,
    신청 마감일·행사 일시를 화면의 '일정 후보' 카드로 만듭니다. 캘린더에 바로 넣지 않습니다.
    "공지 보고 마감일 챙겨줘", "신청할 만한 공모전 일정 잡아줘" 같은 요청에 사용하세요.

    Args:
        days: 최근 며칠 공지를 볼지 (1~30, 기본 7).
    """
    from ..schedule_finder import ScanError, list_candidates, scan_notices
    try:
        res = scan_notices(days=max(1, min(int(days), 30)))
    except ScanError as e:
        raise ToolError(str(e))
    slim = lambda c: {k: c.get(k) for k in ("title", "start", "end", "mail_subject", "mail_link", "confidence")}
    return {
        "result": res["message"],
        "new_candidates": [slim(c) for c in res["new"]],
        "all_pending_candidates": [slim(c) for c in list_candidates("pending")],
        "how_to_confirm": "화면 왼쪽 '일정 후보'에서 확인 후 「캘린더에 추가」",
    }
