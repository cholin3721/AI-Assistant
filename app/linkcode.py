"""메신저 연결 코드(6자리) 확인 — 디스코드·텔레그램 공통.

봇에게 말을 걸 수 있는 사람이면 누구나 코드를 '시도'할 수 있으므로:
  - 코드는 암호용 난수(secrets)로 만들고
  - 메시지 안의 숫자가 '정확히 그 코드 하나'일 때만 맞는 것으로 보고 (긴 숫자열에 코드가 섞여 있는 경우는 거절)
  - 몇 번 틀리면 코드를 새로 만들어 화면에서 다시 확인하게 합니다 (무작위 대입 방지).
"""
import hmac
import re
import secrets

MAX_WRONG = 5


def new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def digits_in(text: str) -> list:
    return re.findall(r"\d+", text or "")


def matches(code: str, text: str) -> bool:
    """메시지에 들어 있는 숫자가 코드 하나뿐일 때만 True. 예) "코드는 123456 입니다" ○, "000000123456" ×"""
    nums = digits_in(text)
    return bool(code) and len(nums) == 1 and hmac.compare_digest(nums[0], code)


class Attempts:
    """틀린 시도 세기 (프로그램이 켜져 있는 동안). 숫자가 없는 메시지("안녕")는 시도로 세지 않습니다."""
    def __init__(self):
        self.wrong = 0

    def check(self, code: str, text: str) -> str:
        """'ok' | 'wrong' | 'rotate'(너무 많이 틀림 → 새 코드를 만들어야 함) | 'none'(숫자 없음)"""
        if matches(code, text):
            self.wrong = 0
            return "ok"
        if not digits_in(text):
            return "none"
        self.wrong += 1
        if self.wrong >= MAX_WRONG:
            self.wrong = 0
            return "rotate"
        return "wrong"
