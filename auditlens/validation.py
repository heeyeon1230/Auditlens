"""분개장 데이터 검증 (설계서 5절 V1~V11).

검증 결과는 두 등급입니다.
- 오류: 분석을 시작할 수 없습니다. 파일이나 열 연결을 고쳐야 합니다.
- 경고: 분석은 진행하되, 문제가 있는 행을 알려 줍니다. 읽을 수 없는 날짜·금액이나
        필수 값이 빈 행은 분석에서 제외하고 그 사실을 표시합니다.
- 정보: 문제는 아니지만 확인해 두면 좋은 내용(분석 기간, 합계 등)입니다.

프로그램은 원본 값을 임의로 고치지 않습니다. 금액의 쉼표·'원' 같은 표기만 숫자로 읽기 위해 제거하고,
그 사실을 정보로 알립니다.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from auditlens.schema import COLUMN_LABELS, REQUIRED_COLUMNS

ERROR = "오류"
WARNING = "경고"
INFO = "정보"

MAX_EXAMPLES = 5  # 메시지에 보여 줄 예시 행 수
BALANCE_TOLERANCE = 1  # 전표별 차대 차이 허용액(원). 단수 차이 1원까지는 같은 것으로 봅니다.

DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S"]


@dataclass
class ValidationResult:
    issues: list = field(default_factory=list)
    clean: pd.DataFrame = None  # 표준 열 이름, 변환된 자료형, 제외 행이 빠진 표
    excluded_rows: list = field(default_factory=list)  # 분석에서 제외한 파일 행 번호
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


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if value is pd.NaT:
        return True
    return isinstance(value, str) and value.strip() == ""


def parse_date(value):
    """날짜로 읽을 수 있으면 Timestamp, 빈 값이면 None, 읽을 수 없으면 ValueError."""
    if _is_blank(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, (datetime, date)):
        return pd.Timestamp(value)
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return pd.Timestamp(datetime.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError(text)


_AMOUNT_NOISE = re.compile(r"[,\s원₩]")


def parse_amount(value):
    """금액으로 읽을 수 있으면 float, 빈 값이면 0, 읽을 수 없으면 ValueError.

    반환값: (금액, 표기 정리 여부). 쉼표·공백·'원'을 지웠으면 표기 정리 여부가 True입니다.
    괄호 표기 (1,000)은 음수 -1000으로 읽습니다.
    """
    if _is_blank(value):
        return 0.0, False
    if isinstance(value, bool):
        raise ValueError(str(value))
    if isinstance(value, (int, float)):
        return float(value), False
    text = str(value).strip()
    cleaned = _AMOUNT_NOISE.sub("", text)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        number = float(cleaned)
    except ValueError:
        raise ValueError(text) from None
    if pd.isna(number) or number in (float("inf"), float("-inf")):
        raise ValueError(text)
    return (-number if negative else number), cleaned != text


def _normalize_code(value):
    """계정코드를 문자열로 통일합니다. Excel이 103을 103.0으로 읽은 경우도 '103'으로 바꿉니다."""
    if _is_blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


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


def validate_journal(raw: pd.DataFrame, mapping: dict) -> ValidationResult:
    """업로드한 표와 열 연결 정보를 받아 검증하고, 분석용 표를 만듭니다.

    mapping: {표준 열 이름: 파일 열 이름 또는 None}
    """
    result = ValidationResult()

    # V1 필수 열 존재, 같은 파일 열을 두 번 연결했는지
    missing = [c for c in REQUIRED_COLUMNS if not mapping.get(c)]
    for col in missing:
        _add(result, ERROR, "V1", _label(col),
             f"필수 열 '{COLUMN_LABELS[col]}'이(가) 연결되지 않았습니다.",
             f"파일에 '{COLUMN_LABELS[col]}' 열을 추가하거나, 열 연결 화면에서 어느 열이 "
             f"'{COLUMN_LABELS[col]}'인지 선택해 주세요.")
    used = {}
    for std_col, file_col in mapping.items():
        if file_col:
            used.setdefault(file_col, []).append(std_col)
    for file_col, std_cols in used.items():
        if len(std_cols) > 1:
            names = ", ".join(f"'{COLUMN_LABELS[c]}'" for c in std_cols)
            _add(result, ERROR, "V1", f"파일 열 '{file_col}'",
                 f"파일의 '{file_col}' 열 하나가 {names}에 동시에 연결되어 있습니다.",
                 "열 연결 화면에서 각 항목에 서로 다른 파일 열을 선택해 주세요.")
    absent = [f for f in used if f not in raw.columns]
    for file_col in absent:
        _add(result, ERROR, "V1", f"파일 열 '{file_col}'",
             f"연결한 열 '{file_col}'이(가) 파일에 없습니다.",
             "파일을 다시 올리거나 열 연결을 다시 선택해 주세요.")

    # V2 빈 파일
    if len(raw) == 0:
        _add(result, ERROR, "V2", "파일 전체", "머리글(열 이름)만 있고 데이터 행이 없습니다.",
             "분개 데이터가 들어 있는 파일인지, Excel이라면 올바른 시트를 선택했는지 확인해 주세요.")

    if result.has_errors:
        return result

    # 표준 열 이름으로 옮긴 작업용 표 (원본은 그대로 둡니다)
    work = pd.DataFrame(index=raw.index)
    for std_col, file_col in mapping.items():
        if file_col:
            work[std_col] = raw[file_col]
    excluded = set()

    # V5 필수 값 결측 (전표번호, 회계처리일, 계정코드는 없으면 분석에서 제외)
    for col in ["je_id", "posting_date", "account_code"]:
        blank_idx = [i for i, v in work[col].items() if _is_blank(v)]
        if blank_idx:
            excluded.update(blank_idx)
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

    # V3 날짜 형식
    parsed_dates = {}
    bad_idx, bad_vals = [], []
    for i, v in work["posting_date"].items():
        try:
            parsed_dates[i] = parse_date(v)
        except ValueError as exc:
            bad_idx.append(i)
            bad_vals.append(str(exc))
            parsed_dates[i] = None
    if bad_idx:
        excluded.update(bad_idx)
        _add(result, WARNING, "V3", _label("posting_date"),
             f"날짜로 읽을 수 없는 회계처리일이 {len(bad_idx)}개 행에 있습니다.",
             "YYYY-MM-DD 형식(예: 2025-03-31)으로 고쳐 주세요. 존재하지 않는 날짜(예: 2025-02-30)도 "
             "오류로 처리됩니다. 이 행들은 분석에서 제외됩니다.",
             bad_idx, _examples(bad_idx, bad_vals))
    work["posting_date"] = pd.to_datetime(pd.Series(parsed_dates))

    if "entry_date" in work:
        parsed_entry = {}
        bad_idx, bad_vals = [], []
        for i, v in work["entry_date"].items():
            try:
                parsed_entry[i] = parse_date(v)
            except ValueError as exc:
                bad_idx.append(i)
                bad_vals.append(str(exc))
                parsed_entry[i] = None
        if bad_idx:
            _add(result, WARNING, "V3", _label("entry_date"),
                 f"날짜·시간으로 읽을 수 없는 입력일시가 {len(bad_idx)}개 행에 있습니다.",
                 "YYYY-MM-DD HH:MM 형식(예: 2025-03-31 14:05)으로 고쳐 주세요. "
                 "이 행들은 입력일시를 쓰는 규칙에서만 빠지고 나머지 분석에는 포함됩니다.",
                 bad_idx, _examples(bad_idx, bad_vals))
        work["entry_date"] = pd.to_datetime(pd.Series(parsed_entry))

    # V4 금액 형식
    cleaned_notation = []
    for col in ["debit", "credit"]:
        values = {}
        bad_idx, bad_vals = [], []
        for i, v in work[col].items():
            try:
                values[i], tidied = parse_amount(v)
                if tidied:
                    cleaned_notation.append(i)
            except ValueError as exc:
                bad_idx.append(i)
                bad_vals.append(str(exc))
                values[i] = 0.0
        if bad_idx:
            excluded.update(bad_idx)
            _add(result, WARNING, "V4", _label(col),
                 f"숫자로 읽을 수 없는 {COLUMN_LABELS[col]}이 {len(bad_idx)}개 행에 있습니다.",
                 "숫자만 입력해 주세요(예: 1500000 또는 1,500,000). 이 행들은 분석에서 제외됩니다.",
                 bad_idx, _examples(bad_idx, bad_vals))
        work[col] = pd.Series(values, dtype=float)
    if cleaned_notation:
        rows = sorted(set(cleaned_notation))
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
    if neither:
        _add(result, WARNING, "V6", "차변·대변",
             f"차변과 대변이 모두 0이거나 빈칸인 행이 {len(neither)}개 있습니다.",
             "금액이 빠졌는지 확인해 주세요. 금액이 없는 행이라면 삭제해도 됩니다.",
             neither, _examples(neither))

    # V7 음수 금액
    negative = [i for i in work.index if work.at[i, "debit"] < 0 or work.at[i, "credit"] < 0]
    if negative:
        _add(result, WARNING, "V7", "차변·대변",
             f"음수 금액이 있는 행이 {len(negative)}개 있습니다.",
             "역분개를 음수로 표시하는 회사도 있으니 의도한 것인지 확인해 주세요. "
             "의도하지 않았다면 반대편(차변↔대변)에 양수로 적어 주세요.",
             negative, _examples(negative))

    work["account_code"] = work["account_code"].map(_normalize_code)
    work["je_id"] = work["je_id"].map(lambda v: "" if _is_blank(v) else str(v).strip())

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

    # V8 전표별 차대 균형
    totals = clean.groupby("je_id")[["debit", "credit"]].sum()
    diff = (totals["debit"] - totals["credit"]).abs()
    unbalanced = diff[diff > BALANCE_TOLERANCE]
    if len(unbalanced):
        shown = ", ".join(f"{je}(차이 {d:,.0f}원)" for je, d in unbalanced.head(MAX_EXAMPLES).items())
        more = len(unbalanced) - min(len(unbalanced), MAX_EXAMPLES)
        _add(result, WARNING, "V8", "전표별 차대 균형",
             f"차변 합계와 대변 합계가 다른 전표가 {len(unbalanced)}개 있습니다: "
             f"{shown}{f' 외 {more}개' if more else ''}",
             "해당 전표의 라인이 빠졌거나 금액이 잘못 입력되었는지 확인해 주세요. "
             "형식 오류로 제외된 행 때문에 생긴 차이일 수도 있습니다.",
             list(unbalanced.index), "")

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
