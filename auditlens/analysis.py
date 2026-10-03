"""기초 분석: 전표 현황, 계정별 분석, 월별 분석과 전기·당기 비교.

입력은 validation.validate_journal()이 만든 분석용 표(result.clean)입니다.
이 표에는 검증에서 제외되지 않은 행만 들어 있으므로, 이 모듈은 값을 새로 고치거나 채우지 않습니다.
그래도 날짜나 금액이 비어 있는 행이 들어오면 조용히 넘기지 않고, prepare()가 제외한 뒤
그 행 수와 처리 기준을 notes로 돌려줍니다.

계산 기준
- 금액 단위는 원입니다. 차변·대변 금액은 파일에 적힌 값(검증 단계에서 숫자로 읽은 값)을 그대로 더합니다.
- 라인 금액 = 차변 + 대변. 한 라인에는 보통 차변과 대변 중 하나만 있으므로 그 라인의 거래 금액이 됩니다.
- 전표 수는 전표번호 기준, 라인 수는 분개 행 기준입니다.
- 연도·월은 회계처리일 기준이며, 회계연도는 1월~12월(12월 결산)로 가정합니다.
"""

import pandas as pd

from auditlens.validation import BALANCE_TOLERANCE

CHART_UNIT = 1_000_000  # 차트는 백만원 단위로 표시
CHART_UNIT_LABEL = "백만원"

# 제조업 주요 계정 구분과 판단 기준. 위에서부터 차례로 확인해 처음 맞는 구분을 씁니다.
KEY_ACCOUNT_GROUPS = [
    ("원재료", "계정과목명이 '원재료'"),
    ("재공품", "계정과목명이 '재공품'"),
    ("제품", "계정과목명이 '제품'"),
    ("매출원가", "계정과목명에 '매출원가'가 들어 있음"),
    ("매출", "계정과목명이 '매출'로 끝남 (예: 제품매출)"),
    ("제조원가", "계정코드가 500~599 (국내에서 흔히 쓰는 계정코드 체계의 제조원가 범위)"),
]

MEASURES = ["전표 수", "라인 수", "차변 합계", "대변 합계"]


def prepare(clean: pd.DataFrame):
    """분석용 표를 준비합니다.

    반환값: (분석용 표, 처리 내역 목록)
    처리 내역은 {"항목", "행 수", "처리"} 사전의 목록이며, 제외한 행이 없으면 빈 목록입니다.
    """
    notes = []
    if clean is None or len(clean) == 0:
        return _empty_frame(), notes

    df = clean.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["posting_date"]):
        df["posting_date"] = pd.to_datetime(df["posting_date"], errors="coerce")
    for col in ["debit", "credit"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    bad_date = df["posting_date"].isna()
    if bad_date.any():
        notes.append({"항목": "회계처리일", "행 수": int(bad_date.sum()),
                      "처리": "날짜가 없거나 읽을 수 없어 분석에서 제외했습니다."})
    bad_amount = (df["debit"].isna() | df["credit"].isna()) & ~bad_date
    if bad_amount.any():
        notes.append({"항목": "차변·대변 금액", "행 수": int(bad_amount.sum()),
                      "처리": "금액이 없거나 숫자가 아니어서 분석에서 제외했습니다."})
    df = df[~(bad_date | bad_amount)].copy()

    df["line_amount"] = df["debit"] + df["credit"]
    df["year"] = df["posting_date"].dt.year
    df["month"] = df["posting_date"].dt.month
    df["year_month"] = df["posting_date"].dt.strftime("%Y-%m")
    return df.reset_index(drop=True), notes


def _empty_frame():
    return pd.DataFrame({
        "je_id": pd.Series(dtype=str), "posting_date": pd.Series(dtype="datetime64[ns]"),
        "account_code": pd.Series(dtype=str), "account_name": pd.Series(dtype=str),
        "debit": pd.Series(dtype=float), "credit": pd.Series(dtype=float),
        "line_amount": pd.Series(dtype=float), "year": pd.Series(dtype=int),
        "month": pd.Series(dtype=int), "year_month": pd.Series(dtype=str),
    })


# ---------------------------------------------------------------- 1. 전표 현황
def voucher_balance(df: pd.DataFrame):
    """전표번호별 차변·대변 합계와 차이. 차이 = 차변 합계 - 대변 합계."""
    if len(df) == 0:
        return pd.DataFrame(columns=["전표번호", "회계처리일", "라인 수", "차변 합계", "대변 합계", "차이"])
    table = df.groupby("je_id").agg(
        회계처리일=("posting_date", "min"), **{"라인 수": ("je_id", "size")},
        **{"차변 합계": ("debit", "sum"), "대변 합계": ("credit", "sum")},
    )
    table["차이"] = table["차변 합계"] - table["대변 합계"]
    return table.reset_index().rename(columns={"je_id": "전표번호"})


def unbalanced_vouchers(df: pd.DataFrame):
    """차변 합계와 대변 합계의 차이가 허용액(1원)을 넘는 전표."""
    table = voucher_balance(df)
    return table[table["차이"].abs() > BALANCE_TOLERANCE].reset_index(drop=True)


def overview(df: pd.DataFrame):
    """전표 현황 요약. 데이터가 없으면 건수·금액 0, 기간 None을 돌려줍니다."""
    total_dr = float(df["debit"].sum()) if len(df) else 0.0
    total_cr = float(df["credit"].sum()) if len(df) else 0.0
    unbalanced = unbalanced_vouchers(df)
    return {
        "라인 수": int(len(df)),
        "전표 수": int(df["je_id"].nunique()) if len(df) else 0,
        "계정 수": int(df["account_code"].nunique()) if len(df) else 0,
        "기간 시작": df["posting_date"].min() if len(df) else None,
        "기간 종료": df["posting_date"].max() if len(df) else None,
        "자료가 있는 월 수": int(df["year_month"].nunique()) if len(df) else 0,
        "총 차변": total_dr,
        "총 대변": total_cr,
        "차이": total_dr - total_cr,
        "합계 일치": abs(total_dr - total_cr) <= BALANCE_TOLERANCE,
        "불균형 전표 수": int(len(unbalanced)),
        "불균형 전표 차이 절대값 합계": float(unbalanced["차이"].abs().sum()) if len(unbalanced) else 0.0,
    }


# ---------------------------------------------------------------- 2. 계정별 분석
def key_account_group(code, name):
    """제조업 주요 계정 구분을 돌려줍니다. 해당하지 않으면 None (계정별 표에서는 빈칸)."""
    name = str(name).strip() if name is not None and not pd.isna(name) else ""
    if name in ("원재료", "재공품", "제품"):
        return name
    if "매출원가" in name:
        return "매출원가"
    if name.endswith("매출"):
        return "매출"
    code = str(code).strip()
    if code.isdigit() and 500 <= int(code) <= 599:
        return "제조원가"
    return None


def _account_names(df):
    """계정코드별 대표 계정과목명. 이름이 여러 개면 가장 많이 쓰인 이름 뒤에 '외 n개'를 붙입니다."""
    names = {}
    for code, series in df.groupby("account_code")["account_name"]:
        counts = series.dropna().astype(str).str.strip()
        counts = counts[counts != ""].value_counts()
        if len(counts) == 0:
            names[code] = ""
        elif len(counts) == 1:
            names[code] = counts.index[0]
        else:
            names[code] = f"{counts.index[0]} 외 {len(counts) - 1}개"
    return pd.Series(names, dtype=str)


def account_summary(df: pd.DataFrame):
    """계정별 건수, 차변·대변 합계, 라인 금액 요약 통계."""
    columns = ["계정코드", "계정과목명", "주요 계정 구분", "라인 수", "전표 수", "차변 합계", "대변 합계",
               "순액(차변-대변)", "라인 금액 평균", "라인 금액 중앙값", "라인 금액 최소", "라인 금액 최대"]
    if len(df) == 0:
        return pd.DataFrame(columns=columns)
    g = df.groupby("account_code")
    table = pd.DataFrame({
        "라인 수": g.size(),
        "전표 수": g["je_id"].nunique(),
        "차변 합계": g["debit"].sum(),
        "대변 합계": g["credit"].sum(),
        "라인 금액 평균": g["line_amount"].mean(),
        "라인 금액 중앙값": g["line_amount"].median(),
        "라인 금액 최소": g["line_amount"].min(),
        "라인 금액 최대": g["line_amount"].max(),
    })
    table["순액(차변-대변)"] = table["차변 합계"] - table["대변 합계"]
    table["계정과목명"] = _account_names(df)
    table = table.reset_index().rename(columns={"account_code": "계정코드"})
    table["주요 계정 구분"] = [key_account_group(c, n) or "" for c, n in zip(table["계정코드"], table["계정과목명"])]
    return table[columns].sort_values("계정코드", key=_code_sort_key).reset_index(drop=True)


def _code_sort_key(codes):
    """숫자 계정코드는 숫자 순서로, 나머지는 그 뒤에 글자 순서로 정렬합니다."""
    return codes.map(lambda c: (0, int(c), "") if str(c).isdigit() else (1, 0, str(c)))


def key_account_summary(df: pd.DataFrame):
    """제조업 주요 계정 구분별 합계. 데이터에 없는 구분도 0으로 표시해 '없음'을 드러냅니다."""
    accounts = account_summary(df)
    rows = []
    for group, basis in KEY_ACCOUNT_GROUPS:
        part = accounts[accounts["주요 계정 구분"] == group]
        rows.append({
            "구분": group,
            "해당 계정": ", ".join(f"{c} {n}" for c, n in zip(part["계정코드"], part["계정과목명"])) or "(해당 계정 없음)",
            "라인 수": int(part["라인 수"].sum()),
            "차변 합계": float(part["차변 합계"].sum()),
            "대변 합계": float(part["대변 합계"].sum()),
            "순액(차변-대변)": float(part["순액(차변-대변)"].sum()),
            "분류 기준": basis,
        })
    return pd.DataFrame(rows)


def accounts_in_group(df: pd.DataFrame, group):
    """주요 계정 구분에 속하는 계정코드 목록."""
    accounts = account_summary(df)
    return list(accounts.loc[accounts["주요 계정 구분"] == group, "계정코드"])


# ---------------------------------------------------------------- 3. 월별 분석
def _filter_accounts(df, account_codes):
    if account_codes is None:
        return df
    return df[df["account_code"].isin([str(c) for c in account_codes])]


def monthly_summary(df: pd.DataFrame, account_codes=None):
    """연월별 전표 수, 라인 수, 차변·대변 합계.

    첫 달부터 마지막 달까지 모든 달을 표시하고, 거래가 없는 달은 0으로 둡니다(비고에 '거래 없음').
    전표 수는 그 달에 라인이 있는 전표번호 수입니다. 여러 달에 걸친 전표는 각 달에 한 번씩 셉니다.
    """
    columns = ["연월", "연도", "월", "전표 수", "라인 수", "차변 합계", "대변 합계", "비고"]
    part = _filter_accounts(df, account_codes)
    if len(part) == 0:
        return pd.DataFrame(columns=columns)
    g = part.groupby("year_month")
    table = pd.DataFrame({
        "전표 수": g["je_id"].nunique(),
        "라인 수": g.size(),
        "차변 합계": g["debit"].sum(),
        "대변 합계": g["credit"].sum(),
    })
    months = pd.period_range(part["posting_date"].min(), part["posting_date"].max(), freq="M").strftime("%Y-%m")
    table = table.reindex(months)
    table["비고"] = ["거래 없음" if pd.isna(v) else "" for v in table["라인 수"]]
    table = table.fillna(0)
    table[["전표 수", "라인 수"]] = table[["전표 수", "라인 수"]].astype(int)
    table = table.rename_axis("연월").reset_index()
    table["연도"] = table["연월"].str[:4].astype(int)
    table["월"] = table["연월"].str[5:].astype(int)
    return table[columns]


def fiscal_years(df: pd.DataFrame):
    """데이터에 있는 회계연도(회계처리일의 연도) 목록, 오름차순."""
    if len(df) == 0:
        return []
    return sorted(int(y) for y in df["year"].unique())


def default_comparison_years(df: pd.DataFrame):
    """기본 비교 연도: 가장 최근 연도를 당기, 바로 앞 연도를 전기로 봅니다.

    바로 앞 연도의 자료가 없으면 (None, 당기)를 돌려줍니다.
    """
    years = fiscal_years(df)
    if not years:
        return None, None
    current = years[-1]
    prior = current - 1 if current - 1 in years else None
    return prior, current


def compare_years(df: pd.DataFrame, prior_year, current_year, measure="차변 합계", account_codes=None):
    """전기와 당기의 같은 달끼리 비교합니다 (1월~12월 + 합계 행).

    - 증감 = 당기 - 전기, 증감률(%) = 증감 / 전기 × 100
    - 전기 값이 0이면 증감률을 계산하지 않습니다(빈칸).
    - 어느 한 해에 그 달 자료가 아예 없으면 비고에 표시합니다. 예를 들어 당기 자료가 9월까지만 있으면
      10~12월은 '당기 자료 없음'으로 표시해, 거래가 줄어든 것과 자료가 없는 것을 구분합니다.
    """
    if measure not in MEASURES:
        raise ValueError(f"비교 항목은 {', '.join(MEASURES)} 중 하나여야 합니다: {measure}")
    columns = ["월", "전기", "당기", "증감", "증감률(%)", "비고"]
    part = _filter_accounts(df, account_codes)
    all_years = df[["year", "month"]].drop_duplicates() if len(df) else pd.DataFrame(columns=["year", "month"])

    rows = []
    for month in range(1, 13):
        values, missing = {}, []
        for label, year in (("전기", prior_year), ("당기", current_year)):
            sub = part[(part["year"] == year) & (part["month"] == month)]
            values[label] = _measure_value(sub, measure)
            has_data = ((all_years["year"] == year) & (all_years["month"] == month)).any()
            if not has_data:
                missing.append(f"{label} 자료 없음")
        rows.append({"월": f"{month}월", **values, "비고": ", ".join(missing)})
    total = {"월": "합계",
             "전기": _measure_value(part[part["year"] == prior_year], measure),
             "당기": _measure_value(part[part["year"] == current_year], measure),
             "비고": ""}
    table = pd.DataFrame(rows + [total])
    table["증감"] = table["당기"] - table["전기"]
    table["증감률(%)"] = [round(d / p * 100, 1) if p else None for d, p in zip(table["증감"], table["전기"])]
    return table[columns]


def _measure_value(sub, measure):
    if measure == "전표 수":
        return int(sub["je_id"].nunique())
    if measure == "라인 수":
        return int(len(sub))
    if measure == "차변 합계":
        return float(sub["debit"].sum())
    return float(sub["credit"].sum())
