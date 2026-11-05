"""날짜·금액 표기 읽기.

파일에 적힌 글자를 날짜·숫자로 읽는 규칙만 모아 둔 곳입니다. 검증 단계(validation.py)가 이 함수를 써서
읽고, 읽지 못하거나 해석이 모호한 값은 경고·오류로 알립니다. 원본 값은 바꾸지 않습니다.

원칙: 해석이 둘 이상 가능한 값은 프로그램이 고르지 않습니다.
- 03/04/2025처럼 월·일 순서가 모호한 날짜는 사용자가 '날짜 순서'를 골라야 읽습니다.
- 1.000,50 같은 유럽식 표기는 사용자가 '숫자 표기'를 유럽식으로 골라야 읽습니다.
"""

import re
from datetime import date, datetime

import pandas as pd

# ---------------------------------------------------------------- 날짜
DAY_ORDER_MDY = "월/일/연도"
DAY_ORDER_DMY = "일/월/연도"
DAY_ORDERS = [DAY_ORDER_MDY, DAY_ORDER_DMY]

_TIME = r"(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6}))?)?)?"
_TZ = r"\s*(Z|[+-]\d{2}:?\d{2})?"
# 연도가 앞: 2025-03-31, 2025/3/31, 2025.03.31, 2025. 3. 31. (시간이 뒤에 붙을 수 있음)
_YMD = re.compile(r"^(\d{4})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{1,2})\.?" + _TIME + r"$")
# ISO 8601: 2025-03-31T14:05:30, 2025-03-31T14:05:30.123+09:00, 2025-03-31 14:05:30Z
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6}))?)?" + _TZ + r"$")
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")  # 20250331
# 연도가 뒤: 03/31/2025, 31.03.2025 → 월·일 순서가 모호
_AMBIGUOUS = re.compile(r"^(\d{1,2})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{4})\.?" + _TIME + r"$")


class AmbiguousDate(ValueError):
    """월·일 순서를 정해야 읽을 수 있는 날짜(예: 03/04/2025)."""


def _is_blank(value):
    if value is None or value is pd.NaT:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def _build(y, m, d, hh=None, mm=None, ss=None, frac=None):
    micro = int((frac or "0").ljust(6, "0")[:6])
    return pd.Timestamp(datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0), micro))


def read_date(value, day_order=None):
    """날짜로 읽습니다. 반환값: (Timestamp 또는 빈 값이면 None, 메모 set).

    메모 "timezone": 시간대 표시(Z, +09:00)가 있었음. 적힌 시각을 그대로 쓰고 시간대는 환산하지 않습니다.
    읽을 수 없으면 ValueError, 월·일 순서가 모호한데 day_order를 고르지 않았으면 AmbiguousDate.
    존재하지 않는 날짜(2025-02-30)도 ValueError입니다.
    """
    if _is_blank(value):
        return None, set()
    if isinstance(value, pd.Timestamp):
        return value, set()
    if isinstance(value, (datetime, date)):
        return pd.Timestamp(value), set()
    text = str(value).strip()
    try:
        m = _YMD.match(text)
        if m:
            y, _, mo, d, hh, mi, ss, frac = m.groups()
            return _build(y, mo, d, hh, mi, ss, frac), set()
        m = _ISO.match(text)
        if m:
            y, mo, d, hh, mi, ss, frac, tz = m.groups()
            return _build(y, mo, d, hh, mi, ss, frac), ({"timezone"} if tz else set())
        m = _COMPACT.match(text)
        if m:
            return _build(*m.groups()), set()
        m = _AMBIGUOUS.match(text)
        if m:
            a, _, b, y, hh, mi, ss, frac = m.groups()
            if day_order == DAY_ORDER_MDY:
                return _build(y, a, b, hh, mi, ss, frac), set()
            if day_order == DAY_ORDER_DMY:
                return _build(y, b, a, hh, mi, ss, frac), set()
            raise AmbiguousDate(text)
    except AmbiguousDate:
        raise
    except (ValueError, OverflowError):
        raise ValueError(text) from None
    raise ValueError(text)


def is_ambiguous_date(value):
    """월·일 순서를 정해야 읽을 수 있는 표기(예: 03/04/2025)인지. 날짜 셀(Excel)은 해당하지 않습니다."""
    return isinstance(value, str) and bool(_AMBIGUOUS.match(value.strip()))


def count_ambiguous_dates(series):
    """열에서 월·일 순서가 모호한 표기의 값 수."""
    return int(series.map(is_ambiguous_date).sum()) if series is not None else 0


# ---------------------------------------------------------------- 금액
NOTATION_STANDARD = "기본 (1,234.56)"
NOTATION_EUROPEAN = "유럽식 (1.234,56)"
NOTATIONS = [NOTATION_STANDARD, NOTATION_EUROPEAN]

# 읽지 못한 이유 (화면 문구는 validation.py가 정합니다)
BAD_INVALID = "숫자 아님"
BAD_EUROPEAN = "유럽식 표기로 보임"
BAD_STANDARD = "기본 표기로 보임"
BAD_GROUPING = "천 단위 구분 위치 오류"
BAD_CURRENCY = "원화가 아닌 통화"
BAD_EXPONENT = "지수 표기"

_WON = re.compile(r"(?i)krw|₩|원|\s")  # 원화 표기와 공백: 지우고 읽음
_OTHER_CURRENCY = re.compile(r"[$€¥£]|(?<![A-Za-z])(?:USD|EUR|JPY|CNY|GBP|HKD|SGD|AUD|CAD|CHF|VND)(?![A-Za-z])", re.I)
_EXPONENT = re.compile(r"^\d+(?:\.\d+)?[eE][+-]?\d+$")
_PLAIN = r"\d+(?:{dec}\d+)?|{dec}\d+"
_STANDARD = re.compile(r"^(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|" + _PLAIN.format(dec=r"\.") + r")$")
_EUROPEAN = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|" + _PLAIN.format(dec=",") + r")$")
_DOT3 = re.compile(r"^\d{1,3}\.\d{3}$")    # 1.000: 기본 표기로는 1, 유럽식으로는 1000
_COMMA3 = re.compile(r"^\d{1,3},\d{3}$")   # 1,000: 기본 표기로는 1000, 유럽식으로는 1


class AmountError(ValueError):
    """금액으로 읽을 수 없음. reason은 BAD_* 중 하나."""

    def __init__(self, text, reason):
        super().__init__(text)
        self.text, self.reason = text, reason


def read_amount(value, notation=NOTATION_STANDARD):
    """금액으로 읽습니다. 반환값: (float, 메모 set). 빈 값은 0입니다.

    메모
    - "tidied": 쉼표·공백·'원'·₩·KRW·괄호를 지우고 읽음(유럽식이면 천 단위 점도)
    - "sign_notation": 뒤 마이너스(1,000-)나 △·▲를 음수로 읽음
    - "check_separator": 1.000(기본 표기)이나 1,000(유럽식)처럼 다른 표기로 읽으면 값이 1,000배 달라지는 값
    부호는 적힌 그대로입니다(괄호·마이너스·△·▲는 음수). 읽을 수 없으면 AmountError.
    """
    if _is_blank(value):
        return 0.0, set()
    if isinstance(value, bool):
        raise AmountError(str(value), BAD_INVALID)
    if isinstance(value, (int, float)):
        number = float(value)
        if pd.isna(number) or number in (float("inf"), float("-inf")):
            raise AmountError(str(value), BAD_INVALID)
        return number, set()
    text = str(value).strip()
    notes = set()
    if _OTHER_CURRENCY.search(text):
        raise AmountError(text, BAD_CURRENCY)
    body = _WON.sub("", text)

    negative = parenthesized = False
    if body.startswith("(") and body.endswith(")"):
        negative, parenthesized, body = True, True, body[1:-1]
    elif body[:1] in ("-", "+"):
        negative, body = body[0] == "-", body[1:]
    elif body[:1] in ("△", "▲"):
        negative, body = True, body[1:]
        notes.add("sign_notation")
    elif body.endswith("-"):
        negative, body = True, body[:-1]
        notes.add("sign_notation")

    european = notation == NOTATION_EUROPEAN
    if _EXPONENT.match(body):
        raise AmountError(text, BAD_EXPONENT)
    if european:
        if not _EUROPEAN.match(body):
            raise AmountError(text, BAD_STANDARD if _STANDARD.match(body) else
                              (BAD_GROUPING if re.fullmatch(r"[\d.,]+", body) else BAD_INVALID))
        if _COMMA3.match(body):
            notes.add("check_separator")
        number = float(body.replace(".", "").replace(",", "."))
    else:
        if not _STANDARD.match(body):
            raise AmountError(text, BAD_EUROPEAN if _EUROPEAN.match(body) and ("," in body or body.count(".") > 1)
                              else (BAD_GROUPING if re.fullmatch(r"[\d.,]+", body) else BAD_INVALID))
        if _DOT3.match(body):
            notes.add("check_separator")
        number = float(body.replace(",", ""))
    if _WON.search(text) or parenthesized or "," in body or (european and "." in body):
        notes.add("tidied")
    return (-number if negative else number), notes
