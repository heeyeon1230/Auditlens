"""분개장 데이터 검증 (설계서 5절 V1~V11).

검증 결과는 두 등급입니다.
- 오류: 분석을 시작할 수 없습니다. 파일이나 열 연결을 고쳐야 합니다.
- 경고: 분석은 진행하되, 문제가 있는 행을 알려 줍니다. 읽을 수 없는 날짜·금액이나
        필수 값이 빈 행은 분석에서 제외하고 그 사실을 표시합니다.
- 정보: 문제는 아니지만 확인해 두면 좋은 내용(분석 기간, 합계 등)입니다.

프로그램은 원본 값을 임의로 고치지 않습니다. 금액의 쉼표·'원' 같은 표기만 숫자로 읽기 위해 제거하고,
그 사실을 정보로 알립니다.
"""

from dataclasses import dataclass, field

import pandas as pd

from auditlens.formats import (
    BAD_CURRENCY, BAD_EUROPEAN, BAD_EXPONENT, BAD_GROUPING, BAD_INVALID, BAD_STANDARD, DAY_ORDERS, NOTATION_EUROPEAN,
    NOTATION_STANDARD, AmountError, count_ambiguous_dates, read_amount, read_date,
)
from auditlens.schema import COLUMN_LABELS, REQUIRED_COLUMNS, SINGLE_AMOUNT_LABELS

ERROR = "오류"
WARNING = "경고"
INFO = "정보"

MAX_EXAMPLES = 5  # 메시지에 보여 줄 예시 행 수
DEBIT_SIDE, CREDIT_SIDE = "차변", "대변"  # 금액 한 열 형식에서 구분값을 연결할 쪽
BALANCE_TOLERANCE = 1  # 전표별 차대 차이 허용액(원). 단수 차이 1원까지는 같은 것으로 봅니다.


@dataclass
class ValidationResult:
    issues: list = field(default_factory=list)
    clean: pd.DataFrame = None  # 표준 열 이름, 변환된 자료형, 제외 행이 빠진 표
    excluded_rows: list = field(default_factory=list)  # 분석에서 제외한 파일 행 번호
    excluded_detail: pd.DataFrame = None  # 제외한 행의 파일 행 번호·전표번호·계정코드·계정과목명·사유
    summary: dict = field(default_factory=dict)

    @property
    def has_errors(self):
        return any(i["등급"] == ERROR for i in self.issues)

    def issues_table(self):
        columns = ["등급", "코드", "항목", "문제", "해당 행 수", "행 번호(예시)", "수정 방법"]
        if not self.issues:
            return pd.DataFrame(columns=columns)
        order = {ERROR: 0, WARNING: 1, INFO: 2}
        table = pd.DataFrame(self.issues)[columns]
        table["해당 행 수"] = table["해당 행 수"].astype("Int64")
        return table.sort_values("등급", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)


def file_row_number(index):
    """DataFrame 행 번호(0부터) → 파일에서 보이는 행 번호(머리글이 1행이므로 +2)."""
    return int(index) + 2


def _label(col):
    return f"{COLUMN_LABELS[col]}({col})"


def _name(col):
    """표준 열 또는 금액 한 열 형식 항목의 한글 이름."""
    return COLUMN_LABELS.get(col) or SINGLE_AMOUNT_LABELS[col]


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if value is pd.NaT:
        return True
    return isinstance(value, str) and value.strip() == ""


def parse_date(value, day_order=None):
    """날짜로 읽을 수 있으면 Timestamp, 빈 값이면 None, 읽을 수 없으면 ValueError(모호하면 AmbiguousDate).

    읽는 규칙은 auditlens/formats.py의 read_date에 있습니다.
    """
    return read_date(value, day_order)[0]


def parse_amount(value, notation=NOTATION_STANDARD):
    """금액으로 읽을 수 있으면 float, 빈 값이면 0, 읽을 수 없으면 ValueError(AmountError).

    반환값: (금액, 표기 정리 여부). 쉼표·공백·'원'을 지웠으면 표기 정리 여부가 True입니다.
    괄호 표기 (1,000)은 음수 -1000으로 읽습니다. 읽는 규칙은 auditlens/formats.py의 read_amount에 있습니다.
    """
    number, notes = read_amount(value, notation)
    return number, "tidied" in notes


def _normalize_code(value):
    """계정코드를 문자열로 통일합니다. Excel이 103을 103.0으로 읽은 경우도 '103'으로 바꿉니다."""
    if _is_blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def dc_flag_key(value):
    """차대 구분 값을 비교용 글자로 바꿉니다. 앞뒤 공백만 빼고, Excel이 1을 1.0으로 읽은 경우만 '1'로 맞춥니다.

    대소문자나 표기는 바꾸지 않습니다(어떤 값이 차변인지는 사용자가 정합니다). 빈 값은 ''입니다.
    """
    return _normalize_code(value)


def dc_flag_values(raw: pd.DataFrame, column):
    """차대 구분 열에 실제로 있는 값과 행 수(많은 순). 빈 값은 넣지 않습니다. 반환값: [(값, 행 수), ...]"""
    if column not in raw:
        return []
    keys = raw[column].map(dc_flag_key)
    counts = keys[keys != ""].value_counts(sort=False).sort_values(ascending=False, kind="stable")
    return [(k, int(n)) for k, n in counts.items()]


def _examples(indices, values=None):
    shown = []
    for n, idx in enumerate(list(indices)[:MAX_EXAMPLES]):
        row = f"{file_row_number(idx)}행"
        if values is not None:
            row += f" '{values[n]}'"
        shown.append(row)
    more = len(indices) - len(shown)
    return ", ".join(shown) + (f" 외 {more}개" if more > 0 else "")


def _add(result, level, code, item, problem, fix, rows=None, examples=""):
    result.issues.append({
        "등급": level, "코드": code, "항목": item, "문제": problem,
        "해당 행 수": len(rows) if rows is not None else None,
        "행 번호(예시)": examples, "수정 방법": fix,
    })


def validate_journal(raw: pd.DataFrame, mapping: dict, single_amount: dict = None,
                     formats: dict = None) -> ValidationResult:
    """업로드한 표와 열 연결 정보를 받아 검증하고, 분석용 표를 만듭니다.

    mapping: {표준 열 이름: 파일 열 이름 또는 None}
    single_amount: 금액 한 열 형식일 때만 넣습니다. {"amount": 금액 열, "dc_flag": 차대 구분 열,
                   "sides": {구분값: "차변" 또는 "대변"}}. 이때 mapping의 차변금액·대변금액 연결은 쓰지 않고,
                   구분값이 가리키는 쪽에 금액을 그대로 옮겨 차변금액·대변금액 두 열을 만듭니다(부호는 바꾸지 않음).
                   구분값이 비었거나 차변·대변으로 지정하지 않은 행은 경고(V12)와 함께 분석에서 제외합니다.
    formats: 사용자가 고른 표기. {"day_order": None 또는 formats.DAY_ORDERS 중 하나,
             "notation": formats.NOTATION_STANDARD(기본) 또는 NOTATION_EUROPEAN}. 넣지 않으면 기본 표기,
             날짜 순서는 고르지 않은 것으로 봅니다(03/04/2025 같은 값이 있으면 V3 오류).
    """
    result = ValidationResult()
    formats = formats or {}
    day_order = formats.get("day_order") if formats.get("day_order") in DAY_ORDERS else None
    notation = NOTATION_EUROPEAN if formats.get("notation") == NOTATION_EUROPEAN else NOTATION_STANDARD
    single = single_amount is not None
    if single:
        mapping = {k: (None if k in ("debit", "credit") else v) for k, v in mapping.items()}
    # V4 등 금액 메시지에 쓸 이름. 금액 한 열 형식이면 사용자가 연결한 '금액' 열 기준으로 적습니다.
    amount_names = ({"debit": "금액(차변으로 지정한 행)", "credit": "금액(대변으로 지정한 행)"} if single
                    else {"debit": COLUMN_LABELS["debit"], "credit": COLUMN_LABELS["credit"]})

    # V1 필수 열 존재, 같은 파일 열을 두 번 연결했는지
    required = [c for c in REQUIRED_COLUMNS if not (single and c in ("debit", "credit"))]
    missing = [c for c in required if not mapping.get(c)]
    for col in missing:
        _add(result, ERROR, "V1", _label(col),
             f"필수 열 '{COLUMN_LABELS[col]}'이(가) 연결되지 않았습니다.",
             f"파일에 '{COLUMN_LABELS[col]}' 열을 추가하거나, 열 연결 화면에서 어느 열이 "
             f"'{COLUMN_LABELS[col]}'인지 선택해 주세요.")
    if single:
        for key, label in SINGLE_AMOUNT_LABELS.items():
            if not single_amount.get(key):
                _add(result, ERROR, "V1", label,
                     f"금액 한 열 형식의 필수 항목 '{label}'이(가) 연결되지 않았습니다.",
                     f"열 연결 화면에서 어느 열이 '{label}'인지 선택해 주세요. 차변·대변이 두 열로 나뉜 파일이면 "
                     "금액 형식을 '차변·대변 두 열'로 바꿔 주세요.")
    used = {}
    linked = list(mapping.items()) + ([(k, single_amount.get(k)) for k in SINGLE_AMOUNT_LABELS] if single else [])
    for std_col, file_col in linked:
        if file_col:
            used.setdefault(file_col, []).append(std_col)
    for file_col, std_cols in used.items():
        if len(std_cols) > 1:
            names = ", ".join(f"'{_name(c)}'" for c in std_cols)
            _add(result, ERROR, "V1", f"파일 열 '{file_col}'",
                 f"파일의 '{file_col}' 열 하나가 {names}에 동시에 연결되어 있습니다.",
                 "열 연결 화면에서 각 항목에 서로 다른 파일 열을 선택해 주세요.")
    absent = [f for f in used if f not in raw.columns]
    for file_col in absent:
        _add(result, ERROR, "V1", f"파일 열 '{file_col}'",
             f"연결한 열 '{file_col}'이(가) 파일에 없습니다.",
             "파일을 다시 올리거나 열 연결을 다시 선택해 주세요.")

    # V12 차대 구분 값 연결 (금액 한 열 형식). 어느 값이 차변이고 어느 값이 대변인지는 사용자가 정합니다.
    sides = {}
    if single and single_amount.get("dc_flag"):
        sides = {str(k): v for k, v in (single_amount.get("sides") or {}).items() if v in (DEBIT_SIDE, CREDIT_SIDE)}
        for side in (DEBIT_SIDE, CREDIT_SIDE):
            if side not in sides.values():
                _add(result, ERROR, "V12", "차대 구분 값",
                     f"'{side}'으로 지정한 차대 구분 값이 없습니다.",
                     f"열 연결 화면의 '차대 구분 값 연결'에서 파일의 값 중 어느 것이 '{side}'인지 골라 주세요.")

    # V2 빈 파일
    if len(raw) == 0:
        _add(result, ERROR, "V2", "파일 전체", "머리글(열 이름)만 있고 데이터 행이 없습니다.",
             "분개 데이터가 들어 있는 파일인지, Excel이라면 올바른 시트를 선택했는지 확인해 주세요.")

    # V3 월·일 순서가 모호한 날짜(예: 03/04/2025). 프로그램이 순서를 추측하지 않고, 사용자가 고를 때까지 멈춥니다.
    if day_order is None and not result.has_errors:
        for col in ("posting_date", "entry_date"):
            file_col = mapping.get(col)
            n = count_ambiguous_dates(raw[file_col]) if file_col in raw else 0
            if n:
                _add(result, ERROR, "V3", _label(col),
                     f"월·일 순서를 알 수 없는 날짜(예: 03/04/2025)가 {n}개 행에 있습니다. 3월 4일인지 4월 3일인지 "
                     "프로그램이 정하지 않습니다.",
                     "열 연결 화면의 '날짜 순서'에서 이 파일의 날짜가 월/일/연도인지 일/월/연도인지 골라 주세요.")

    if result.has_errors:
        return result

    # 표준 열 이름으로 옮긴 작업용 표 (원본은 그대로 둡니다)
    work = pd.DataFrame(index=raw.index)
    for std_col, file_col in mapping.items():
        if file_col:
            work[std_col] = raw[file_col]
    excluded = set()
    reasons = {}  # DataFrame 행 번호 → 제외 사유 목록

    def exclude(indices, texts):
        excluded.update(indices)
        for i, text in zip(indices, texts):
            reasons.setdefault(i, []).append(text)

    # 금액 한 열 형식: 구분값이 가리키는 쪽에 금액(파일 값 그대로)을 옮겨 차변금액·대변금액 두 열을 만듭니다.
    # 금액을 숫자로 읽는 일과 그 뒤의 검사(V4~V8)는 두 열 형식과 똑같이 진행합니다.
    if single:
        keys = raw[single_amount["dc_flag"]].map(dc_flag_key)
        side = keys.map(sides)
        amounts = raw[single_amount["amount"]]
        work["debit"] = [a if s == DEBIT_SIDE else "" for a, s in zip(amounts, side)]
        work["credit"] = [a if s == CREDIT_SIDE else "" for a, s in zip(amounts, side)]
        work = work[[c for c in COLUMN_LABELS if c in work]].copy()  # 열 순서를 두 열 형식과 같게(표준 열 순서)
        blank_idx = [i for i, k in keys.items() if k == ""]
        if blank_idx:
            exclude(blank_idx, ["차대 구분 없음"] * len(blank_idx))
            _add(result, WARNING, "V12", f"차대 구분({single_amount['dc_flag']})",
                 f"차대 구분 값이 비어 있는 행이 {len(blank_idx)}개 있습니다.",
                 "해당 행에 차변·대변 구분을 채워 주세요. 이 행들은 차변인지 대변인지 알 수 없어 분석에서 제외됩니다.",
                 blank_idx, _examples(blank_idx))
        unknown_idx = [i for i, k in keys.items() if k != "" and pd.isna(side[i])]
        if unknown_idx:
            values = [keys[i] for i in unknown_idx]
            exclude(unknown_idx, [f"차대 구분 값 '{v}'을(를) 차변·대변으로 지정하지 않음" for v in values])
            kinds = ", ".join(f"'{v}'({n}행)" for v, n in pd.Series(values).value_counts(sort=False).items())
            _add(result, WARNING, "V12", f"차대 구분({single_amount['dc_flag']})",
                 f"차변·대변으로 지정하지 않은 구분 값이 있는 행이 {len(unknown_idx)}개 있습니다: {kinds}",
                 "열 연결 화면의 '차대 구분 값 연결'에서 이 값을 차변 또는 대변으로 지정하거나, 파일의 값을 고쳐 "
                 "주세요. 지정하지 않은 값의 행은 분석에서 제외됩니다.",
                 unknown_idx, _examples(unknown_idx, values))

    # V5 필수 값 결측 (전표번호, 회계처리일, 계정코드는 없으면 분석에서 제외)
    for col in ["je_id", "posting_date", "account_code"]:
        blank_idx = [i for i, v in work[col].items() if _is_blank(v)]
        if blank_idx:
            exclude(blank_idx, [f"{COLUMN_LABELS[col]} 없음"] * len(blank_idx))
            _add(result, WARNING, "V5", _label(col),
                 f"'{COLUMN_LABELS[col]}' 값이 비어 있는 행이 {len(blank_idx)}개 있습니다.",
                 f"해당 행에 '{COLUMN_LABELS[col]}'을(를) 채워 주세요. 이 행들은 분석에서 제외됩니다.",
                 blank_idx, _examples(blank_idx))
    blank_names = [i for i, v in work["account_name"].items() if _is_blank(v)]
    if blank_names:
        _add(result, WARNING, "V5", _label("account_name"),
             f"계정과목명이 비어 있는 행이 {len(blank_names)}개 있습니다.",
             "계정과목명을 채워 주세요. 분석은 계정코드 기준으로 진행되므로 이 행들은 제외하지 않습니다.",
             blank_names, _examples(blank_names))

    # V3 날짜 형식 (읽는 규칙: auditlens/formats.py read_date)
    def read_dates(col):
        parsed, bad_idx, bad_vals, tz_idx = {}, [], [], []
        for i, v in work[col].items():
            try:
                parsed[i], notes = read_date(v, day_order)
                if "timezone" in notes:
                    tz_idx.append(i)
            except ValueError as exc:
                bad_idx.append(i)
                bad_vals.append(str(exc))
                parsed[i] = None
        if tz_idx:
            _add(result, INFO, "V3", _label(col),
                 f"시간대 표시(Z, +09:00 등)가 붙은 {COLUMN_LABELS[col]}이(가) {len(tz_idx)}개 행에 있습니다. 적힌 날짜·시각을 "
                 "그대로 쓰고 시간대는 환산하지 않았습니다.",
                 "파일의 시각이 회사 현지 시각이 아니라면(예: UTC) 날짜가 하루 달라질 수 있으니 확인해 주세요.",
                 tz_idx, _examples(tz_idx))
        return parsed, bad_idx, bad_vals

    parsed_dates, bad_idx, bad_vals = read_dates("posting_date")
    if bad_idx:
        exclude(bad_idx, [f"회계처리일 형식 오류('{v}')" for v in bad_vals])
        _add(result, WARNING, "V3", _label("posting_date"),
             f"날짜로 읽을 수 없는 회계처리일이 {len(bad_idx)}개 행에 있습니다.",
             "YYYY-MM-DD 형식(예: 2025-03-31)으로 고쳐 주세요. 존재하지 않는 날짜(예: 2025-02-30)도 "
             "오류로 처리됩니다. 이 행들은 분석에서 제외됩니다.",
             bad_idx, _examples(bad_idx, bad_vals))
    work["posting_date"] = pd.to_datetime(pd.Series(parsed_dates))

    if "entry_date" in work:
        parsed_entry, bad_idx, bad_vals = read_dates("entry_date")
        if bad_idx:
            _add(result, WARNING, "V3", _label("entry_date"),
                 f"날짜·시간으로 읽을 수 없는 입력일시가 {len(bad_idx)}개 행에 있습니다.",
                 "YYYY-MM-DD HH:MM 형식(예: 2025-03-31 14:05)으로 고쳐 주세요. "
                 "이 행들은 입력일시를 쓰는 규칙에서만 빠지고 나머지 분석에는 포함됩니다.",
                 bad_idx, _examples(bad_idx, bad_vals))
        work["entry_date"] = pd.to_datetime(pd.Series(parsed_entry))

    # V4 금액 형식 (읽는 규칙: auditlens/formats.py read_amount). 읽지 못한 이유별로 따로 알립니다.
    other = "기본" if notation == NOTATION_EUROPEAN else "유럽식"
    bad_messages = {
        BAD_INVALID: ("숫자로 읽을 수 없는", "숫자만 입력해 주세요(예: 1500000 또는 1,500,000)."),
        BAD_GROUPING: ("천 단위 구분 기호의 위치가 맞지 않는",
                       "천 단위 구분 기호는 세 자리마다 있어야 합니다(예: 1,500,000). 잘못 입력한 값인지 확인해 주세요."),
        BAD_EUROPEAN: ("유럽식 표기(예: 1.000,50)로 보이는",
                       "파일이 유럽식 표기라면 열 연결 화면의 '숫자 표기'를 '유럽식'으로 바꿔 주세요. 기본 표기로 읽으면 "
                       "값이 달라질 수 있어 읽지 않았습니다."),
        BAD_STANDARD: ("기본 표기(예: 1,000.50)로 보이는",
                       "파일이 기본 표기라면 열 연결 화면의 '숫자 표기'를 '기본'으로 바꿔 주세요. 유럽식으로 읽으면 "
                       "값이 달라질 수 있어 읽지 않았습니다."),
        BAD_CURRENCY: ("원화가 아닌 통화 기호($, USD, € 등)가 붙은",
                       "이 프로그램은 환율 환산을 하지 않습니다. 원화 금액으로 바꾼 파일을 올려 주세요."),
        BAD_EXPONENT: ("지수 표기(예: 1.5E+06)로 적힌",
                       "Excel에서 셀 서식을 '숫자'로 바꾸어 전체 자릿수가 보이게 저장해 주세요."),
    }
    tidied_rows, sign_rows, check_rows, check_vals = set(), set(), [], []
    for col in ["debit", "credit"]:
        values = {}
        bad = {}
        for i, v in work[col].items():
            try:
                values[i], notes = read_amount(v, notation)
                if "tidied" in notes:
                    tidied_rows.add(i)
                if "sign_notation" in notes:
                    sign_rows.add(i)
                if "check_separator" in notes:
                    check_rows.append(i)
                    check_vals.append(str(v).strip())
            except AmountError as exc:
                bad.setdefault(exc.reason, []).append((i, exc.text))
                values[i] = 0.0
        item = (f"금액({single_amount['amount']}) · {'차변' if col == 'debit' else '대변'}으로 지정한 행" if single
                else _label(col))
        for reason, (what, fix) in bad_messages.items():
            if reason not in bad:
                continue
            idx = [i for i, _ in bad[reason]]
            vals = [t for _, t in bad[reason]]
            suffix = "" if reason == BAD_INVALID else f", {reason}"
            exclude(idx, [f"{amount_names[col]} 형식 오류('{t}'{suffix})" for t in vals])
            _add(result, WARNING, "V4", item,
                 f"{what} {amount_names[col]}이 {len(idx)}개 행에 있습니다.",
                 f"{fix} 이 행들은 분석에서 제외됩니다.", idx, _examples(idx, vals))
        work[col] = pd.Series(values, dtype=float)
    if check_rows:
        _add(result, WARNING, "V4", "금액 표기 확인",
             f"구분 기호 뒤에 세 자리만 있어 표기에 따라 1,000배 달라지는 금액이 {len(check_rows)}개 행에 있습니다. "
             f"'{notation.split(' ')[0]}' 표기로 읽었습니다(예: {check_vals[0]} → {read_amount(check_vals[0], notation)[0]:,g}).",
             f"파일이 {other} 표기라면 열 연결 화면의 '숫자 표기'를 바꿔 주세요. 맞게 읽었다면 조치할 필요는 없습니다. "
             "이 행들은 분석에 포함됩니다.",
             check_rows, _examples(check_rows, check_vals))
    if sign_rows:
        rows = sorted(sign_rows)
        _add(result, INFO, "V4", "음수 표기",
             f"뒤 마이너스(1,000-)나 △·▲ 표기를 음수로 읽은 행이 {len(rows)}개 있습니다.",
             "조치할 필요는 없습니다. 부호는 바꾸지 않으며, 음수 금액은 아래 V7에서 따로 알립니다.", rows, _examples(rows))
    if tidied_rows:
        rows = sorted(tidied_rows)
        _add(result, INFO, "V4", "금액 표기",
             f"금액의 쉼표·공백·'원' 표기를 지우고 숫자로 읽은 행이 {len(rows)}개 있습니다.",
             "조치할 필요는 없습니다. 원본 파일은 바뀌지 않습니다.", rows, _examples(rows))

    # V6 차변·대변 동시 기재 또는 둘 다 0
    both = [i for i in work.index if work.at[i, "debit"] != 0 and work.at[i, "credit"] != 0]
    neither = [i for i in work.index if work.at[i, "debit"] == 0 and work.at[i, "credit"] == 0
               and i not in excluded]
    if both:
        _add(result, WARNING, "V6", "차변·대변",
             f"한 행에 차변과 대변이 모두 적힌 행이 {len(both)}개 있습니다.",
             "한 행에는 차변 또는 대변 중 하나만 적고, 다른 쪽은 0 또는 빈칸으로 두세요.",
             both, _examples(both))
    if neither and single:
        _add(result, WARNING, "V6", "금액",
             f"금액이 0이거나 빈칸인 행이 {len(neither)}개 있습니다.",
             "금액이 빠졌는지 확인해 주세요. 금액이 없는 행이라면 삭제해도 됩니다.",
             neither, _examples(neither))
    elif neither:
        _add(result, WARNING, "V6", "차변·대변",
             f"차변과 대변이 모두 0이거나 빈칸인 행이 {len(neither)}개 있습니다.",
             "금액이 빠졌는지 확인해 주세요. 금액이 없는 행이라면 삭제해도 됩니다.",
             neither, _examples(neither))

    # V7 음수 금액
    negative = [i for i in work.index if work.at[i, "debit"] < 0 or work.at[i, "credit"] < 0]
    if negative and single:
        _add(result, WARNING, "V7", "금액",
             f"음수 금액이 있는 행이 {len(negative)}개 있습니다.",
             "역분개를 음수로 표시하는 회사도 있으니 의도한 것인지 확인해 주세요. 프로그램은 부호를 바꾸지 않고, 차대 "
             "구분이 가리키는 쪽에 음수 그대로 둡니다(예: 구분 '차변', 금액 -1,000 → 차변 -1,000).",
             negative, _examples(negative))
    elif negative:
        _add(result, WARNING, "V7", "차변·대변",
             f"음수 금액이 있는 행이 {len(negative)}개 있습니다.",
             "역분개를 음수로 표시하는 회사도 있으니 의도한 것인지 확인해 주세요. "
             "의도하지 않았다면 반대편(차변↔대변)에 양수로 적어 주세요.",
             negative, _examples(negative))

    work["account_code"] = work["account_code"].map(_normalize_code)
    work["je_id"] = work["je_id"].map(lambda v: "" if _is_blank(v) else str(v).strip())
    result.excluded_detail = pd.DataFrame(
        [{"file_row": file_row_number(i), "je_id": work.at[i, "je_id"],
          "account_code": work.at[i, "account_code"],
          "account_name": "" if _is_blank(work.at[i, "account_name"]) else str(work.at[i, "account_name"]).strip(),
          "사유": ", ".join(reasons[i])}
         for i in sorted(reasons)],
        columns=["file_row", "je_id", "account_code", "account_name", "사유"],
    )

    # V9 완전히 같은 행 (원본 파일의 모든 열 기준)
    dup_mask = raw.astype(str).duplicated(keep="first")
    dup_idx = list(raw.index[dup_mask])
    if dup_idx:
        _add(result, WARNING, "V9", "행 전체",
             f"다른 행과 모든 열의 값이 똑같은 행이 {len(dup_idx)}개 있습니다.",
             "파일을 합치거나 복사하다가 행이 두 번 들어갔는지 확인해 주세요. "
             "프로그램은 중복 행을 지우지 않고 그대로 분석합니다(차대 균형 검사에 영향이 있을 수 있음).",
             dup_idx, _examples(dup_idx))

    clean = work.drop(index=sorted(excluded))
    clean.insert(0, "file_row", [file_row_number(i) for i in clean.index])
    clean = clean.reset_index(drop=True)

    if len(clean) == 0:
        _add(result, ERROR, "V3", "파일 전체",
             "분석할 수 있는 행이 하나도 없습니다. 모든 행이 날짜·금액 형식 오류나 필수 값 누락으로 제외되었습니다.",
             "위의 경고 내용을 참고해 파일을 고친 뒤 다시 올려 주세요.")
        result.excluded_rows = [file_row_number(i) for i in sorted(excluded)]
        return result

    # V8 전표별 차대 균형. 제외된 행이 하나라도 있는 전표는 균형 여부를 확정할 수 없으므로 '검증 불가'로 따로 셉니다.
    unverifiable = sorted(set(result.excluded_detail["je_id"]) - {""})
    totals = clean.groupby("je_id")[["debit", "credit"]].sum()
    diff = (totals["debit"] - totals["credit"]).abs()
    unbalanced = diff[(diff > BALANCE_TOLERANCE) & ~diff.index.isin(unverifiable)]
    if len(unbalanced):
        shown = ", ".join(f"{je}(차이 {d:,.0f}원)" for je, d in unbalanced.head(MAX_EXAMPLES).items())
        more = len(unbalanced) - min(len(unbalanced), MAX_EXAMPLES)
        _add(result, WARNING, "V8", "전표별 차대 균형",
             f"차변 합계와 대변 합계가 다른 전표가 {len(unbalanced)}개 있습니다(분개 오류 의심 - 검토 필요): "
             f"{shown}{f' 외 {more}개' if more else ''}",
             "해당 전표의 라인이 빠졌거나 금액이 잘못 입력되었는지 확인해 주세요.",
             list(unbalanced.index), "")
    if unverifiable:
        shown = ", ".join(unverifiable[:MAX_EXAMPLES])
        more = len(unverifiable) - min(len(unverifiable), MAX_EXAMPLES)
        _add(result, WARNING, "V8", "전표별 차대 균형 (검증 불가)",
             f"분석에서 제외된 행이 포함된 전표가 {len(unverifiable)}개 있어 차대 균형을 확인할 수 없습니다: "
             f"{shown}{f' 외 {more}개' if more else ''}",
             "제외된 행의 날짜·금액 형식이나 빠진 값을 고치면 균형 여부를 확인할 수 있습니다. "
             "이 전표들은 불균형 전표 수에 넣지 않습니다.",
             unverifiable, "")

    # V11 계정코드 하나에 계정과목명이 여러 개
    names = clean.groupby("account_code")["account_name"].nunique()
    conflicted = names[names > 1]
    for code in conflicted.index[:MAX_EXAMPLES]:
        variants = clean.loc[clean["account_code"] == code, "account_name"].dropna().unique()
        _add(result, WARNING, "V11", f"계정코드 {code}",
             f"계정코드 {code}에 계정과목명이 {len(variants)}개 있습니다: {', '.join(map(str, variants))}",
             "계정과목명 표기를 하나로 통일해 주세요. 분석은 계정코드 기준으로 진행됩니다.")

    # V10 분석 기간과 합계 (정보)
    start, end = clean["posting_date"].min(), clean["posting_date"].max()
    months = clean["posting_date"].dt.to_period("M").nunique()
    total_dr, total_cr = clean["debit"].sum(), clean["credit"].sum()
    _add(result, INFO, "V10", "분석 기간",
         f"회계처리일 범위: {start:%Y-%m-%d} ~ {end:%Y-%m-%d} ({months}개월), "
         f"차변 합계 {total_dr:,.0f}원 / 대변 합계 {total_cr:,.0f}원",
         "기대한 기간과 다르면 올바른 파일·시트인지 확인해 주세요. "
         "시산표와의 대사(모집단 완전성 확인)는 이 프로그램 범위 밖이므로 별도로 수행해 주세요.")

    result.clean = clean
    result.excluded_rows = [file_row_number(i) for i in sorted(excluded)]
    result.summary = {
        "전체 행": len(raw),
        "분석 대상 행": len(clean),
        "제외 행": len(excluded),
        "전표 수": clean["je_id"].nunique(),
        "계정 수": clean["account_code"].nunique(),
        "기간 시작": start,
        "기간 종료": end,
    }
    return result
