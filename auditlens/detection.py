"""탐지 규칙 R1~R3: 감사인이 먼저 살펴볼 '검토 대상 거래'를 고릅니다.

입력은 analysis.prepare()가 만든 분석용 표입니다(검증에서 제외된 행은 들어 있지 않음).
규칙의 정의는 docs/설계서.md 6절을 따릅니다. 탐지 결과는 오류나 부정의 결론이 아니며,
모든 결과에 판단 근거(비교 대상, 기준값, 실제 값)를 함께 돌려줍니다.

공통 기준
- 금액은 라인 금액(차변 + 대변, 원)입니다. 0원 라인은 비교에서 뺍니다.
- 방향은 차변 금액이 있으면 '차변', 아니면 '대변'입니다.
- 계정은 계정코드로 구분합니다. 특정 업종의 계정코드나 계정명에 의존하지 않습니다.
- 모든 기준값은 DetectionSettings의 기본값이며 화면에서 바꿀 수 있습니다.
"""

import calendar
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

REVIEW_LABEL = "검토 대상 거래"
# R2(b)는 개별 거래가 아니라 계정-월(기간)에 대한 판단이므로 다른 표시를 씁니다.
PERIOD_LABEL = "검토 대상 기간 (계정-월)"

RULES = {
    "R1": {
        "이름": "계정별 금액 분포 이탈",
        "단위": "분개 라인",
        "목적": "같은 금액이라도 계정마다 평범한 정도가 다르므로, 금액 자체가 아니라 '그 계정에서 드문 금액'을 찾습니다.",
        "조건": "같은 계정·같은 방향(차변/대변) 라인의 금액에 로그(log10)를 취해 사분위수를 구하고, "
                "금액이 10^(Q3 + 배수 × IQR)을 넘는 라인. 계정·방향별 라인이 최소 건수보다 적으면 검사하지 않습니다.",
        "한계": "연 1회 성과급·설비 취득처럼 원래 크고 드문 정상 거래도 탐지됩니다. 한 계정에 성격이 다른 거래가 섞이면 "
                "기준이 넓어져 놓칠 수 있습니다. 최소 건수 미만인 계정은 검사하지 않습니다. 상한만 보며 작은 금액은 보지 않습니다. 로그는 0보다 큰 금액에만 쓸 수 "
                "있어 0원·음수(역분개 등)·차대 동시 기재 라인은 비교에서 빼고, 금액을 바꾸지 않은 채 사유와 함께 따로 표시합니다.",
    },
    "R2(a)": {
        "이름": "거래처 이전 거래 대비 이례적 금액",
        "단위": "분개 라인",
        "목적": "이 거래처와 평소 하던 거래보다 훨씬 큰 금액을 찾습니다(R1이 계정 전체와 비교한다면 R2(a)는 거래처별로 비교).",
        "조건": "같은 거래처·계정·방향에서 이 라인보다 회계처리일이 앞선 거래(같은 날 거래 제외)가 최소 건수 이상이고, "
                "금액이 그 중앙값 × 배수를 초과하는 라인.",
        "한계": "'과거'는 올린 파일 안의 앞선 거래뿐입니다. 거래처명 표기가 다르면 다른 거래처로 봅니다. 거래처가 빈 라인, "
                "이전 거래가 최소 건수 미만인 라인(거래처와의 첫 거래들), 0원·음수 라인은 비교하지 않으며 그 건수와 목록을 "
                "따로 표시합니다.",
    },
    "R2(b)": {
        "이름": "계정별 월 건수·금액 이상 기간",
        "단위": "계정-월 (개별 거래가 아님)",
        "목적": "특정 달에 거래가 몰리거나 빠진 계정을 찾습니다. 개별 전표의 이상 여부가 아니라 기간에 대한 징후입니다.",
        "조건": "계정별 월 라인 수 또는 월 금액이 같은 계정 다른 달 중앙값의 상한 배수 이상(급증)이거나 하한 배수 이하(급감)인 "
                "달. 거래가 없는 달은 0으로 봅니다.",
        "한계": "계절성(연말 매출 증가 등)을 구분하지 못합니다. 파일이 월 중간에 시작·끝나면 그 달이 적게 보일 수 있습니다. "
                "다른 달 중앙값이 0인 계정-월은 비교하지 않습니다. 월 금액은 음수 라인을 포함한 합계입니다.",
    },
    "R3": {
        "이름": "기말·결산 전후 및 비정상 시점 분개",
        "단위": "전표",
        "목적": "기말·결산 후 분개는 실적을 맞추려는 조정이 들어갈 수 있는 시점이고, 주말 입력은 평소 통제 흐름 밖일 수 있습니다.",
        "조건": "(a) 회계처리일이 결산일 전후 n영업일 이내(기본). (a-2) 매월 말일 전후 n영업일 이내(보조 옵션, 기본 꺼짐, "
                "결산일과 같은 말일은 (a)로 봄). (b) 회계처리일은 결산일 이전인데 입력일이 결산일 이후(결산 후 입력). "
                "(c) 입력일이 토·일요일.",
        "한계": "기말에는 감가상각·충당금 같은 정상 결산 분개가 몰립니다. 영업일은 월~금이며 공휴일은 반영하지 않습니다. "
                "자동 배치 전표는 주말에도 정상적으로 생길 수 있습니다. 입력일시 열이 없으면 (b)·(c)는 검사하지 않습니다.",
    },
}


@dataclass
class DetectionSettings:
    r1_multiplier: float = 3.0      # Q3 + 배수 × IQR (로그 금액 기준)
    r1_min_lines: int = 30          # 계정·방향별 최소 라인 수
    r2_ratio: float = 5.0           # (a) 이전 거래 중앙값의 배수
    r2_min_prior: int = 5           # (a) 최소 이전 거래 수
    r2_month_high: float = 3.0      # (b) 다른 달 중앙값 대비 이 배수 이상
    r2_month_low: float = 1 / 3     # (b) 다른 달 중앙값 대비 이 배수 이하
    r3_window: int = 3              # (a) 결산일 전후 영업일 수
    r3_month_end: bool = False      # (a-2) 매월 말일 전후 보조 옵션 (기본 꺼짐)
    closing_month: int = 12         # 결산일(회계연도 말일)의 월
    closing_day: int = 31           # 결산일의 일

    def as_table(self):
        labels = {
            "r1_multiplier": "R1 IQR 배수", "r1_min_lines": "R1 계정·방향별 최소 라인 수",
            "r2_ratio": "R2(a) 이전 거래 중앙값 대비 배수", "r2_min_prior": "R2(a) 최소 이전 거래 수",
            "r2_month_high": "R2(b) 다른 달 중앙값 대비 상한 배수", "r2_month_low": "R2(b) 다른 달 중앙값 대비 하한 배수",
            "r3_window": "R3(a) 전후 영업일 수", "r3_month_end": "R3(a-2) 매월 말일 보조 옵션",
            "closing_month": "결산월", "closing_day": "결산일",
        }
        return pd.DataFrame([{"기준": labels[k], "적용값": v} for k, v in asdict(self).items()])


def _lines(df):
    """규칙 공통 라인 표: 방향, 금액, 거래처(없으면 빈 문자열)를 붙입니다."""
    lines = df.copy()
    both = (lines["debit"] != 0) & (lines["credit"] != 0)
    lines["direction"] = np.where(both, "차변·대변", np.where(lines["debit"] != 0, "차변", "대변"))
    lines["amount"] = lines["debit"] + lines["credit"]
    # 금액 비교(로그·중앙값)에 쓸 수 없는 라인. 값을 바꾸거나 버리지 않고 사유를 붙여 따로 보여 줍니다.
    lines["amount_issue"] = np.select(
        [both, lines["amount"] == 0, lines["amount"] < 0],
        ["한 행에 차변과 대변이 모두 있어 방향을 정할 수 없음",
         "0원 라인: 로그로 변환할 수 없어 금액 비교에서 제외",
         "음수 금액(역분개 등): 로그로 변환할 수 없어 금액 비교에서 제외(금액은 바꾸지 않음)"],
        default="")
    if "vendor" not in lines:
        lines["vendor"] = ""
    lines["vendor"] = lines["vendor"].fillna("").astype(str).str.strip()
    if "file_row" not in lines:
        lines["file_row"] = pd.NA
    return lines


LINE_COLUMNS = ["전표번호", "회계처리일", "계정코드", "계정과목명", "방향", "금액", "거래처", "파일 행 번호"]
EXCLUDED_COLUMNS = LINE_COLUMNS + ["제외 사유"]


def _excluded_lines(lines, reasons):
    """비교에서 뺀 라인과 사유. reasons는 lines와 같은 순서의 사유 목록입니다."""
    rows = [{**_line_info(r), "제외 사유": why} for r, why in zip(lines.itertuples(index=False), reasons)]
    return pd.DataFrame(rows, columns=EXCLUDED_COLUMNS)


def _line_info(row):
    return {"전표번호": row.je_id, "회계처리일": row.posting_date, "계정코드": row.account_code,
            "계정과목명": row.account_name, "방향": row.direction, "금액": row.amount,
            "거래처": row.vendor, "파일 행 번호": row.file_row}


# ---------------------------------------------------------------- R1
R1_COLUMNS = ["판정"] + LINE_COLUMNS + ["비교 라인 수", "계정 중앙값", "상한 금액", "중앙값 대비 배수", "탐지 이유"]


def r1_amount_outliers(df, settings=None):
    """R1. 반환값: (탐지 표, 검사하지 않은 계정·방향 표, 금액 문제로 비교에서 뺀 라인 표).

    로그는 0보다 큰 금액에만 쓸 수 있으므로 0원·음수·차대 동시 기재 라인은 비교에서 빼고,
    빠진 라인을 사유와 함께 돌려줍니다(조용히 버리거나 절대값으로 바꾸지 않음).
    """
    s = settings or DetectionSettings()
    skipped_cols = ["계정코드", "계정과목명", "방향", "라인 수", "사유"]
    if len(df) == 0:
        return pd.DataFrame(columns=R1_COLUMNS), pd.DataFrame(columns=skipped_cols), \
            pd.DataFrame(columns=EXCLUDED_COLUMNS)
    lines = _lines(df)
    bad = lines["amount_issue"] != ""
    excluded = _excluded_lines(lines[bad], lines.loc[bad, "amount_issue"])
    lines = lines[~bad]
    hits, skipped = [], []
    for (code, direction), part in lines.groupby(["account_code", "direction"], sort=True):
        name = part["account_name"].mode().iloc[0]
        if len(part) < s.r1_min_lines:
            skipped.append({"계정코드": code, "계정과목명": name, "방향": direction, "라인 수": len(part),
                            "사유": f"라인이 {s.r1_min_lines}건 미만이라 비교 기준을 정할 수 없음"})
            continue
        logs = np.log10(part["amount"].to_numpy(dtype=float))
        q1, q3 = np.quantile(logs, [0.25, 0.75])
        upper = 10 ** (q3 + s.r1_multiplier * (q3 - q1))
        median = float(np.median(part["amount"]))
        for row in part[part["amount"] > upper].itertuples(index=False):
            ratio = row.amount / median
            hits.append({"판정": REVIEW_LABEL, **_line_info(row), "비교 라인 수": len(part),
                         "계정 중앙값": median, "상한 금액": upper, "중앙값 대비 배수": ratio,
                         "탐지 이유": f"{code} {name} {direction} 라인 {len(part):,}건의 중앙값은 {median:,.0f}원이고 "
                                   f"기준 상한은 {upper:,.0f}원인데, 이 라인은 {row.amount:,.0f}원"
                                   f"(중앙값의 {ratio:,.1f}배)입니다."})
    table = pd.DataFrame(hits, columns=R1_COLUMNS)
    return table.sort_values(["중앙값 대비 배수"], ascending=False).reset_index(drop=True), \
        pd.DataFrame(skipped, columns=skipped_cols), excluded


# ---------------------------------------------------------------- R2
R2A_COLUMNS = ["판정"] + LINE_COLUMNS + ["이전 거래 수", "이전 거래 기간", "이전 거래 중앙값", "중앙값 대비 배수",
                                       "탐지 이유"]


def r2_vendor_history(df, settings=None):
    """R2(a). 같은 거래처·계정·방향의 앞선 거래(회계처리일이 더 이른 거래)와 비교합니다.

    적용 순서: ① 금액 문제(0원·음수·차대 동시) 라인과 거래처가 빈 라인은 비교하지 않음 → ② 같은 거래처·계정·방향
    묶음에서 이 라인보다 회계처리일이 앞선 거래를 셈(같은 날 거래는 '이전 거래'로 보지 않음) → ③ 그 수가 최소 건수
    미만이면 비교하지 않음 → ④ 이전 거래 금액의 중앙값 × 배수를 '초과'하면 탐지(같으면 탐지하지 않음).

    반환값: (탐지 표, 처리 현황 표, 비교하지 않은 라인 표).
    처리 현황 표는 {"구분", "라인 수", "설명"}이며 모든 라인이 어느 한 구분에 들어갑니다.
    """
    s = settings or DetectionSettings()
    status_cols = ["구분", "라인 수", "설명"]
    if len(df) == 0:
        return pd.DataFrame(columns=R2A_COLUMNS), pd.DataFrame(columns=status_cols), \
            pd.DataFrame(columns=EXCLUDED_COLUMNS)
    lines = _lines(df)
    total = len(lines)
    bad = lines["amount_issue"] != ""
    no_vendor = (lines["vendor"] == "") & ~bad
    skipped = [_excluded_lines(lines[bad], lines.loc[bad, "amount_issue"]),
               _excluded_lines(lines[no_vendor], ["거래처가 비어 있어 거래처별 비교를 할 수 없음"] * int(no_vendor.sum()))]
    lines = lines[~bad & ~no_vendor]
    hits, too_few, compared = [], [], 0
    for (vendor, code, direction), part in lines.groupby(["vendor", "account_code", "direction"], sort=True):
        part = part.sort_values(["posting_date", "file_row"], kind="stable")
        dates = part["posting_date"].to_numpy()
        amounts = part["amount"].to_numpy(dtype=float)
        for i, row in enumerate(part.itertuples(index=False)):
            prior_mask = dates < dates[i]
            n_prior = int(prior_mask.sum())
            if n_prior < s.r2_min_prior:
                too_few.append((row, f"이전 거래 {n_prior}건: 기준 {s.r2_min_prior}건 미만이라 비교하지 않음"))
                continue
            compared += 1
            median = float(np.median(amounts[prior_mask]))
            if row.amount > s.r2_ratio * median:
                prior_dates = dates[prior_mask]
                period = f"{pd.Timestamp(prior_dates.min()):%Y-%m-%d} ~ {pd.Timestamp(prior_dates.max()):%Y-%m-%d}"
                ratio = row.amount / median
                hits.append({"판정": REVIEW_LABEL, **_line_info(row), "이전 거래 수": n_prior,
                             "이전 거래 기간": period, "이전 거래 중앙값": median, "중앙값 대비 배수": ratio,
                             "탐지 이유": f"{vendor}와의 {code} {row.account_name} {direction} 이전 거래 {n_prior}건"
                                       f"({period}, 기준 {s.r2_min_prior}건 이상)의 중앙값은 {median:,.0f}원이고 "
                                       f"기준은 그 {s.r2_ratio:g}배({s.r2_ratio * median:,.0f}원) 초과인데, 이 라인은 "
                                       f"{row.amount:,.0f}원(중앙값의 {ratio:,.1f}배)입니다."})
    if too_few:
        rows, why = zip(*too_few)
        skipped.append(_excluded_lines(pd.DataFrame(list(rows)), list(why)))
    skipped = pd.concat([t for t in skipped if len(t)], ignore_index=True) if any(len(t) for t in skipped) \
        else pd.DataFrame(columns=EXCLUDED_COLUMNS)
    status = pd.DataFrame([
        {"구분": "전체 라인", "라인 수": total, "설명": "분석에 포함된 모든 라인"},
        {"구분": "금액 문제로 비교하지 않음", "라인 수": int(bad.sum()),
         "설명": "0원, 음수, 한 행에 차변·대변이 모두 있는 라인"},
        {"구분": "거래처 없음", "라인 수": int(no_vendor.sum()),
         "설명": "거래처 열이 없거나 값이 비어 있어 거래처별 비교 불가" + (" (거래처 열 자체가 없음)"
                                                               if "vendor" not in df else "")},
        {"구분": "이전 거래 부족", "라인 수": len(too_few),
         "설명": f"앞선 거래가 {s.r2_min_prior}건 미만(거래처와의 첫 거래들 포함). 미탐 가능성이 있음"},
        {"구분": "비교함", "라인 수": compared, "설명": f"이전 거래 {s.r2_min_prior}건 이상, 중앙값의 {s.r2_ratio:g}배 초과 여부 확인"},
        {"구분": "그중 탐지", "라인 수": len(hits), "설명": REVIEW_LABEL},
    ], columns=status_cols)
    table = pd.DataFrame(hits, columns=R2A_COLUMNS)
    return table.sort_values("중앙값 대비 배수", ascending=False).reset_index(drop=True), status, skipped


R2B_COLUMNS = ["판정", "계정코드", "계정과목명", "연월", "항목", "이 달 값", "다른 달 중앙값", "비교한 달 수",
               "중앙값 대비 배수", "구분", "탐지 이유"]


def r2_monthly_pattern(df, settings=None):
    """R2(b). 계정별 월 라인 수와 월 금액을 같은 계정의 다른 달 중앙값과 비교합니다.

    비교 기간은 파일의 첫 달부터 마지막 달까지이며, 거래가 없는 달은 0으로 봅니다.
    """
    s = settings or DetectionSettings()
    if len(df) == 0:
        return pd.DataFrame(columns=R2B_COLUMNS)
    lines = _lines(df)
    months = pd.period_range(lines["posting_date"].min(), lines["posting_date"].max(), freq="M")
    if len(months) < 2:
        return pd.DataFrame(columns=R2B_COLUMNS)
    labels = [str(m) for m in months]
    lines["ym"] = lines["posting_date"].dt.strftime("%Y-%m")
    rows = []
    for code, part in lines.groupby("account_code", sort=True):
        name = part["account_name"].mode().iloc[0]
        stats = part.groupby("ym").agg(count=("amount", "size"), total=("amount", "sum")).reindex(labels, fill_value=0)
        for measure, col, unit in [("라인 수", "count", "건"), ("금액", "total", "원")]:
            values = stats[col].to_numpy(dtype=float)
            for i, ym in enumerate(labels):
                others = np.delete(values, i)
                median = float(np.median(others))
                if median <= 0:
                    continue
                ratio = values[i] / median
                if ratio >= s.r2_month_high:
                    kind = "급증"
                elif ratio <= s.r2_month_low:
                    kind = "급감"
                else:
                    continue
                rows.append({"판정": PERIOD_LABEL, "계정코드": code, "계정과목명": name, "연월": ym,
                             "항목": measure, "이 달 값": values[i], "다른 달 중앙값": median,
                             "비교한 달 수": len(others), "중앙값 대비 배수": ratio, "구분": kind,
                             "탐지 이유": f"{code} {name}의 {ym} {measure}는 {values[i]:,.0f}{unit}로, 다른 "
                                       f"{len(others)}개월 중앙값 {median:,.0f}{unit}의 {ratio:,.2f}배({kind})입니다."})
    return pd.DataFrame(rows, columns=R2B_COLUMNS)


# ---------------------------------------------------------------- R3
def closing_date(year, month, day):
    """해당 연도의 결산일. 그 달에 없는 날(예: 2월 30일)은 그 달 말일로 맞춥니다."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def business_days_between(a, b):
    """두 날짜 사이의 영업일(월~금) 수. 이른 날짜 다음 날부터 늦은 날짜까지 셉니다. 같은 날이면 0."""
    a, b = np.datetime64(a, "D"), np.datetime64(b, "D")
    lo, hi = min(a, b), max(a, b)
    one = np.timedelta64(1, "D")
    return int(np.busday_count(lo + one, hi + one))


def _closing_for(posting, s):
    """회계처리일이 속한 회계연도의 결산일(회계처리일 이후 가장 가까운 결산일)."""
    c = closing_date(posting.year, s.closing_month, s.closing_day)
    return c if posting <= c else closing_date(posting.year + 1, s.closing_month, s.closing_day)


R3_COLUMNS = ["판정", "전표번호", "회계처리일", "입력일시", "해당 조건", "포함 계정", "전표 금액", "파일 행 번호",
              "전표 원천", "탐지 이유"]


def r3_timing(df, settings=None):
    """R3. 전표 단위로 (a) 결산일·월말 전후, (b) 결산 후 입력, (c) 주말 입력을 봅니다.

    반환값: (탐지 표, 처리 내역 목록, 조건별 전표 수 표). 한 전표의 라인 회계처리일이 여러 날이면 가장 이른 날,
    입력일시가 여러 개면 가장 늦은 값을 씁니다.
    (a-2) 매월 말일 조건은 옵션이 꺼져 있어도 계산해서 조건별 전표 수 표에 '옵션을 켜면 해당하는 전표 수'로
    보여 주고, 탐지 표에는 옵션이 켜졌을 때만 넣습니다.
    """
    s = settings or DetectionSettings()
    notes = []
    count_cols = ["조건", "해당 전표 수", "탐지 결과 포함"]
    if len(df) == 0:
        return pd.DataFrame(columns=R3_COLUMNS), notes, pd.DataFrame(columns=count_cols)
    lines = _lines(df)
    has_entry = "entry_date" in lines and lines["entry_date"].notna().any()
    if not has_entry:
        notes.append("입력일시 열이 없거나 모두 비어 있어 R3 (b) 결산 후 입력, (c) 주말 입력은 검사하지 않았습니다.")
        lines["entry_date"] = pd.NaT
    agg = {"posting_date": ("posting_date", "min"), "entry_date": ("entry_date", "max"),
           "amount": ("debit", "sum")}
    vouchers = lines.sort_values("file_row").groupby("je_id", sort=False).agg(**agg)
    detail = {}
    for je, part in lines.sort_values("file_row").groupby("je_id", sort=False):
        accounts = ", ".join(dict.fromkeys(f"{c} {n}".strip() for c, n in zip(part["account_code"], part["account_name"])))
        src = ", ".join(dict.fromkeys(str(v) for v in part["source"].dropna())) if "source" in part else ""
        detail[je] = (accounts, ", ".join(str(r) for r in part["file_row"]), src)
    if has_entry:
        missing_entry = int(vouchers["entry_date"].isna().sum())
        if missing_entry:
            notes.append(f"입력일시가 없는 전표 {missing_entry:,}개는 (b)·(c)를 검사하지 않았습니다.")

    weekday = ["월", "화", "수", "목", "금", "토", "일"]
    def near(posting, anchors, kind):
        """가장 가까운 기준일까지 영업일 수가 기준 이내이면 사유 문장을, 아니면 None을 돌려줍니다."""
        if not anchors:
            return None
        n, anchor = min((business_days_between(posting, a), a) for a in anchors)
        if n > s.r3_window:
            return None
        where = "당일" if posting == anchor else f"{'전' if posting < anchor else '후'} {n}영업일"
        return f"회계처리일 {posting:%Y-%m-%d}가 {kind} {anchor:%Y-%m-%d} {where}입니다(기준 {s.r3_window}영업일 이내)."

    rows, counts = [], {"(a) 결산일 전후": 0, "(a-2) 매월 말일 전후": 0, "(b) 결산 후 입력": 0, "(c) 주말 입력": 0}
    for je, v in vouchers.iterrows():
        posting = v["posting_date"].date()
        closing = _closing_for(posting, s)
        prev_closing = closing_date(closing.year - 1, s.closing_month, s.closing_day)
        conditions, reasons = [], []

        # (a) 결산일 전후 n영업일 (직전·직후 결산일 기준)
        why = near(posting, [closing, prev_closing], "결산일")
        if why:
            counts["(a) 결산일 전후"] += 1
            conditions.append("(a) 결산일 전후")
            reasons.append(why)
        # (a-2) 매월 말일 전후 n영업일 (보조 옵션). 결산일과 같은 말일은 (a)에서 보므로 빼고,
        # (a)에 이미 해당하는 전표는 중복해서 세지 않습니다.
        month_ends = [closing_date(posting.year, posting.month, 31), posting.replace(day=1) - timedelta(days=1)]
        why = None if conditions else near(posting, [e for e in month_ends if e not in (closing, prev_closing)], "월말")
        if why:
            counts["(a-2) 매월 말일 전후"] += 1
            if s.r3_month_end:
                conditions.append("(a-2) 매월 말일 전후")
                reasons.append(why)

        entry = v["entry_date"]
        if pd.notna(entry):
            entry_day = entry.date()
            # (b) 회계처리일은 결산일 이전, 입력일은 결산일 이후
            if entry_day > closing:
                counts["(b) 결산 후 입력"] += 1
                conditions.append("(b) 결산 후 입력")
                reasons.append(f"결산일 {closing:%Y-%m-%d} 이전 회계처리일({posting:%Y-%m-%d})인데 "
                               f"{entry_day:%Y-%m-%d}에 입력했습니다({(entry_day - closing).days}일 뒤).")
            # (c) 주말 입력
            if entry.weekday() >= 5:
                counts["(c) 주말 입력"] += 1
                conditions.append("(c) 주말 입력")
                reasons.append(f"입력일시 {entry:%Y-%m-%d %H:%M}는 {weekday[entry.weekday()]}요일입니다.")
        if conditions:
            accounts, file_rows, src = detail[je]
            rows.append({"판정": REVIEW_LABEL, "전표번호": je, "회계처리일": v["posting_date"],
                         "입력일시": entry, "해당 조건": ", ".join(conditions), "포함 계정": accounts,
                         "전표 금액": v["amount"], "파일 행 번호": file_rows, "전표 원천": src,
                         "탐지 이유": " ".join(reasons)})
    included = {"(a) 결산일 전후": "포함", "(a-2) 매월 말일 전후": "포함 (보조 옵션 켜짐)" if s.r3_month_end
                else "제외 (보조 옵션 꺼짐: 켜면 이 전표들이 추가됨)",
                "(b) 결산 후 입력": "포함" if has_entry else "검사 안 함 (입력일시 없음)",
                "(c) 주말 입력": "포함" if has_entry else "검사 안 함 (입력일시 없음)"}
    count_table = pd.DataFrame([{"조건": k, "해당 전표 수": v, "탐지 결과 포함": included[k]} for k, v in counts.items()],
                               columns=count_cols)
    return pd.DataFrame(rows, columns=R3_COLUMNS), notes, count_table


# ---------------------------------------------------------------- 종합
SUMMARY_COLUMNS = ["전표번호", "판정", "해당 규칙 수", "해당 규칙", "회계처리일", "포함 계정", "전표 금액", "탐지 이유"]
REASON_COLUMNS = ["전표번호", "규칙", "대상", "탐지 이유"]


@dataclass
class DetectionResult:
    settings: DetectionSettings
    r1: pd.DataFrame
    r1_skipped: pd.DataFrame
    r1_excluded: pd.DataFrame
    r2a: pd.DataFrame
    r2a_status: pd.DataFrame
    r2a_skipped: pd.DataFrame
    r2b: pd.DataFrame
    r3: pd.DataFrame
    r3_notes: list
    r3_counts: pd.DataFrame
    summary: pd.DataFrame
    reasons: pd.DataFrame  # 전표별 탐지 사유를 규칙·라인마다 한 행씩 (종합 화면의 상세용)


def summarize(df, r1, r2a, r3):
    """전표 단위 종합표와 사유 표. 해당 규칙 수가 많은 순서로 정렬합니다.

    R2(b)는 계정-월(기간) 단위 판단이라 전표 종합표에 넣지 않습니다. 전표 금액은 그 전표의 차변 합계(원)입니다.
    반환값: (종합표, 사유 표). 사유 표에는 한 전표가 여러 규칙·여러 라인에서 탐지된 사유가 모두 한 행씩 들어갑니다.
    """
    found, reason_rows = {}, []
    for rule, table in [("R1", r1), ("R2(a)", r2a), ("R3", r3)]:
        for row in table.to_dict("records"):
            item = found.setdefault(row["전표번호"], {"rules": [], "reasons": [], "date": row["회계처리일"]})
            if rule not in item["rules"]:
                item["rules"].append(rule)
            item["reasons"].append(f"[{rule}] {row['탐지 이유']}")
            target = (f"라인 {row['파일 행 번호']}행 · {row['계정코드']} {row['계정과목명']} {row['방향']}"
                      if rule != "R3" else f"전표 · {row['해당 조건']}")
            reason_rows.append({"전표번호": row["전표번호"], "규칙": rule, "대상": target, "탐지 이유": row["탐지 이유"]})
    part = df[df["je_id"].isin(found)] if found else df.iloc[0:0]
    totals = part.groupby("je_id")["debit"].sum().to_dict()
    accounts = {je: ", ".join(dict.fromkeys(f"{c} {n}".strip() for c, n in zip(g["account_code"], g["account_name"])))
                for je, g in part.groupby("je_id")}
    rows = [{"전표번호": je, "판정": REVIEW_LABEL, "해당 규칙 수": len(v["rules"]), "해당 규칙": ", ".join(v["rules"]),
             "회계처리일": v["date"], "포함 계정": accounts.get(je, ""), "전표 금액": totals.get(je, 0.0),
             "탐지 이유": " ".join(v["reasons"])}
            for je, v in found.items()]
    table = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    table = table.sort_values(["해당 규칙 수", "회계처리일", "전표번호"], ascending=[False, True, True]) \
        .reset_index(drop=True)
    return table, pd.DataFrame(reason_rows, columns=REASON_COLUMNS)


def run_detection(df, settings=None):
    s = settings or DetectionSettings()
    r1, r1_skipped, r1_excluded = r1_amount_outliers(df, s)
    r2a, r2a_status, r2a_skipped = r2_vendor_history(df, s)
    r2b = r2_monthly_pattern(df, s)
    r3, r3_notes, r3_counts = r3_timing(df, s)
    summary, reasons = summarize(df, r1, r2a, r3)
    return DetectionResult(s, r1, r1_skipped, r1_excluded, r2a, r2a_status, r2a_skipped, r2b, r3, r3_notes,
                           r3_counts, summary, reasons)
