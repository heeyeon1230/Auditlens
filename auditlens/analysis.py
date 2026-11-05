"""기초 분석: 전표 현황, 계정별 분석, 월별 분석과 전기·당기 비교.

입력은 validation.validate_journal()이 만든 분석용 표(result.clean)입니다.
이 표에는 검증에서 제외되지 않은 행만 들어 있으므로, 이 모듈은 값을 새로 고치거나 채우지 않습니다.
그래도 날짜나 금액이 비어 있는 행이 들어오면 조용히 넘기지 않고, prepare()가 제외한 뒤
그 행 수와 처리 기준을 notes로 돌려줍니다.

계산 기준
- 금액 단위는 원입니다. 차변·대변 금액은 파일에 적힌 값(검증 단계에서 숫자로 읽은 값)을 그대로 더합니다.
- 라인 금액 = 차변 + 대변. 한 라인에는 보통 차변과 대변 중 하나만 있으므로 그 라인의 거래 금액이 됩니다.
- 전표 수는 전표번호 기준, 라인 수는 분개 행 기준입니다.
- 연도·월은 회계처리일 기준입니다. 회계연도는 결산월로 정하고, 끝나는 해의 이름으로 부릅니다.
  예: 3월 결산이면 2025년 4월 ~ 2026년 3월이 2026 회계연도입니다. 12월 결산이면 달력 연도와 같습니다.
  결산일(일자)은 탐지 규칙 R3·R4(c)에만 쓰고, 기초 분석은 월 단위로 나눕니다.
"""

import pandas as pd

from auditlens.validation import BALANCE_TOLERANCE

CHART_UNIT = 1_000_000  # 차트는 백만원 단위로 표시
CHART_UNIT_LABEL = "백만원"

MEASURES = ["전표 수", "라인 수", "차변 합계", "대변 합계"]


def fiscal_year_of(year, month, closing_month=12):
    """회계연도(끝나는 해). 결산월 다음 달부터 새 회계연도입니다."""
    return year if month <= closing_month else year + 1


def fiscal_month_order(closing_month=12):
    """회계연도 안의 월 순서. 예: 3월 결산 → [4, 5, ..., 12, 1, 2, 3]."""
    return [(closing_month + k - 1) % 12 + 1 for k in range(1, 13)]


def fiscal_year_range(fiscal_year, closing_month=12):
    """회계연도의 첫 달과 마지막 달(YYYY-MM). 예: (2026, 3) → ('2025-04', '2026-03')."""
    first = fiscal_month_order(closing_month)[0]
    start_year = fiscal_year if first == 1 else fiscal_year - 1
    return f"{start_year}-{first:02d}", f"{fiscal_year}-{closing_month:02d}"


def _closing(df, closing_month):
    if closing_month is None:
        closing_month = df.attrs.get("closing_month", 12)
    if not 1 <= int(closing_month) <= 12:
        raise ValueError(f"결산월은 1~12 사이여야 합니다: {closing_month}")
    return int(closing_month)


def prepare(clean: pd.DataFrame, closing_month=12):
    """분석용 표를 준비합니다.

    closing_month: 결산월(1~12). 회계연도(fiscal_year)와 회계연도 안의 월 순서(fiscal_month_no, 1~12)를 계산합니다.
    반환값: (분석용 표, 처리 내역 목록)
    처리 내역은 {"항목", "행 수", "처리"} 사전의 목록이며, 제외한 행이 없으면 빈 목록입니다.
    """
    notes = []
    closing_month = _closing(pd.DataFrame(), closing_month)
    if clean is None or len(clean) == 0:
        empty = _empty_frame()
        empty.attrs["closing_month"] = closing_month
        return empty, notes

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
    df["fiscal_year"] = df["year"].where(df["month"] <= closing_month, df["year"] + 1)
    df["fiscal_month_no"] = (df["month"] - closing_month - 1) % 12 + 1
    df = df.reset_index(drop=True)
    df.attrs["closing_month"] = closing_month
    return df, notes


def _empty_frame():
    return pd.DataFrame({
        "je_id": pd.Series(dtype=str), "posting_date": pd.Series(dtype="datetime64[ns]"),
        "account_code": pd.Series(dtype=str), "account_name": pd.Series(dtype=str),
        "debit": pd.Series(dtype=float), "credit": pd.Series(dtype=float),
        "line_amount": pd.Series(dtype=float), "year": pd.Series(dtype=int),
        "month": pd.Series(dtype=int), "year_month": pd.Series(dtype=str),
        "fiscal_year": pd.Series(dtype=int), "fiscal_month_no": pd.Series(dtype=int),
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


BALANCED, UNBALANCED, UNVERIFIABLE = "균형", "불균형", "검증 불가"
STATUS_COLUMNS = ["전표번호", "상태", "회계처리일", "라인 수", "제외 행 수", "차변 합계", "대변 합계", "차이", "제외 사유"]


def _excluded_by_voucher(excluded):
    """검증에서 제외한 행을 전표번호별로 모읍니다. 전표번호가 빈 행은 어느 전표인지 알 수 없어 빠집니다.

    excluded: validation 결과의 excluded_detail (file_row, je_id, 사유). None이면 제외 행이 없는 것으로 봅니다.
    반환값: {전표번호: (제외 행 수, "12행 회계처리일 형식 오류('2025-02-30') / ...")}
    """
    if excluded is None or len(excluded) == 0:
        return {}
    grouped = {}
    for row in excluded.itertuples(index=False):
        if row.je_id:
            grouped.setdefault(row.je_id, []).append(f"{row.file_row}행 {row.사유}")
    return {je: (len(items), " / ".join(items)) for je, items in grouped.items()}


def voucher_status(df: pd.DataFrame, excluded=None):
    """전표마다 균형·불균형·검증 불가를 판정합니다.

    - 검증 불가: 검증에서 제외된 행이 하나라도 있는 전표. 남은 행만으로는 균형 여부를 확정할 수 없으므로
      불균형으로 세지 않습니다. 모든 행이 제외된 전표도 여기에 들어갑니다(라인 수 0).
    - 불균형: 제외된 행이 없고, 차변 합계와 대변 합계의 차이가 허용액(1원)을 넘는 전표.
    - 균형: 그 밖의 전표.
    금액과 차이는 분석에 남은 행만으로 계산한 값입니다.
    """
    table = voucher_balance(df)
    by_voucher = _excluded_by_voucher(excluded)
    missing = sorted(set(by_voucher) - set(table["전표번호"]))
    if missing:
        empty = pd.DataFrame({"전표번호": missing, "회계처리일": pd.NaT, "라인 수": 0,
                              "차변 합계": 0.0, "대변 합계": 0.0, "차이": 0.0})
        table = pd.concat([table, empty], ignore_index=True) if len(table) else empty
    table["제외 행 수"] = [by_voucher.get(je, (0, ""))[0] for je in table["전표번호"]]
    table["제외 사유"] = [by_voucher.get(je, (0, ""))[1] for je in table["전표번호"]]
    table["상태"] = [
        UNVERIFIABLE if n else (UNBALANCED if abs(d) > BALANCE_TOLERANCE else BALANCED)
        for n, d in zip(table["제외 행 수"], table["차이"])
    ]
    return table[STATUS_COLUMNS].reset_index(drop=True)


REVIEW_LABEL = "분개 오류 의심 - 검토 필요"
UNVERIFIABLE_LABEL = "검증 불가 - 제외 행 수정 후 재확인"


def _voucher_line_summary(df):
    """전표번호별 포함 계정("131 원재료, 251 외상매입금")과 파일 행 번호("102, 103")."""
    if len(df) == 0 or "file_row" not in df:
        return {}, {}
    accounts, rows = {}, {}
    for je, part in df.sort_values("file_row").groupby("je_id", sort=False):
        pairs = dict.fromkeys(f"{c} {n}".strip() for c, n in zip(part["account_code"], part["account_name"]))
        accounts[je] = ", ".join(pairs)
        rows[je] = ", ".join(str(r) for r in part["file_row"])
    return accounts, rows


def unbalanced_vouchers(df: pd.DataFrame, excluded=None):
    """제외된 행이 없는데 차변 합계와 대변 합계의 차이가 허용액(1원)을 넘는 전표 (실제 불균형).

    판정은 오류 확정이 아니라 '분개 오류 의심 - 검토 필요'입니다. 포함 계정과 파일 행 번호를 함께 돌려줍니다.
    """
    table = voucher_status(df, excluded)
    table = table[table["상태"] == UNBALANCED].copy()
    accounts, rows = _voucher_line_summary(df)
    table["판정"] = REVIEW_LABEL
    table["포함 계정"] = [accounts.get(je, "") for je in table["전표번호"]]
    table["파일 행 번호"] = [rows.get(je, "") for je in table["전표번호"]]
    columns = ["전표번호", "판정", "회계처리일", "파일 행 번호", "라인 수", "차변 합계", "대변 합계", "차이", "포함 계정"]
    return table[columns].reset_index(drop=True)


def unverifiable_vouchers(df: pd.DataFrame, excluded=None):
    """검증에서 제외된 행이 포함되어 차대 균형을 확정할 수 없는 전표와 제외 사유."""
    table = voucher_status(df, excluded)
    table = table[table["상태"] == UNVERIFIABLE].copy()
    accounts, rows = _voucher_line_summary(df)
    table["판정"] = UNVERIFIABLE_LABEL
    table["분석에 남은 계정"] = [accounts.get(je, "") for je in table["전표번호"]]
    columns = ["전표번호", "제외 사유", "판정", "회계처리일", "라인 수", "제외 행 수", "차변 합계", "대변 합계", "차이",
               "분석에 남은 계정"]
    return table[columns].reset_index(drop=True)


def voucher_lines(df: pd.DataFrame, je_id, excluded=None):
    """전표 하나의 라인 상세. 분석에 포함된 행과 검증에서 제외된 행을 파일 행 번호 순서로 보여 줍니다.

    제외된 행은 금액을 읽을 수 없었을 수 있으므로 차변·대변을 비워 두고 제외 사유를 적습니다.
    """
    columns = ["파일 행 번호", "구분", "회계처리일", "계정코드", "계정과목명", "차변", "대변", "제외 사유"]
    rows = []
    part = df[df["je_id"] == je_id] if len(df) else df
    for r in part.itertuples(index=False):
        rows.append({"파일 행 번호": getattr(r, "file_row", None), "구분": "분석 포함",
                     "회계처리일": r.posting_date, "계정코드": r.account_code, "계정과목명": r.account_name,
                     "차변": r.debit, "대변": r.credit, "제외 사유": ""})
    if excluded is not None and len(excluded):
        for r in excluded[excluded["je_id"] == je_id].itertuples(index=False):
            rows.append({"파일 행 번호": r.file_row, "구분": "분석 제외", "회계처리일": pd.NaT,
                         "계정코드": r.account_code, "계정과목명": r.account_name,
                         "차변": float("nan"), "대변": float("nan"), "제외 사유": r.사유})
    table = pd.DataFrame(rows, columns=columns)
    return table.sort_values("파일 행 번호", kind="stable").reset_index(drop=True)


def overview(df: pd.DataFrame, excluded=None):
    """전표 현황 요약. 데이터가 없으면 건수·금액 0, 기간 None을 돌려줍니다.

    excluded를 주면 제외된 행이 포함된 전표를 '검증 불가'로 따로 세고 불균형 전표 수에서 뺍니다.
    """
    total_dr = float(df["debit"].sum()) if len(df) else 0.0
    total_cr = float(df["credit"].sum()) if len(df) else 0.0
    status = voucher_status(df, excluded)
    unbalanced = status[status["상태"] == UNBALANCED]
    unverifiable = status[status["상태"] == UNVERIFIABLE]
    no_voucher = 0 if excluded is None or len(excluded) == 0 else int((excluded["je_id"] == "").sum())
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
        "균형 전표 수": int((status["상태"] == BALANCED).sum()),
        "불균형 전표 수": int(len(unbalanced)),
        "불균형 전표 차이 절대값 합계": float(unbalanced["차이"].abs().sum()) if len(unbalanced) else 0.0,
        "검증 불가 전표 수": int(len(unverifiable)),
        "검증 불가 전표 차이 합계": float(unverifiable["차이"].sum()) if len(unverifiable) else 0.0,
        "전표번호 없는 제외 행 수": no_voucher,
    }


# ---------------------------------------------------------------- 2. 계정별 분석
def _account_names(df):
    """계정코드별 계정과목명.

    반환값: (표시용 이름, 대표 이름) 두 Series.
    대표 이름은 가장 많이 쓰인 이름이고, 횟수가 같으면 파일에서 먼저 나온 이름입니다.
    이름이 여러 개면 표시용 이름에만 '외 n개'를 붙입니다.
    """
    display, primary = {}, {}
    for code, series in df.groupby("account_code", sort=False)["account_name"]:
        names = series.dropna().astype(str).str.strip()
        counts = names[names != ""].value_counts(sort=False).sort_values(ascending=False, kind="stable")
        primary[code] = counts.index[0] if len(counts) else ""
        display[code] = primary[code] + (f" 외 {len(counts) - 1}개" if len(counts) > 1 else "")
    return pd.Series(display, dtype=str), pd.Series(primary, dtype=str)


ACCOUNT_COLUMNS = ["계정코드", "계정과목명", "라인 수", "전표 수", "차변 합계", "대변 합계", "순액(차변-대변)",
                   "라인 금액 합계", "금액 비중(%)", "라인 금액 평균", "라인 금액 중앙값", "라인 금액 최소", "라인 금액 최대"]


def account_summary(df: pd.DataFrame, groups=None):
    """계정별 건수, 차변·대변 합계, 라인 금액 요약 통계. 업로드한 파일에 실제로 있는 계정만, 계정코드 순서로 보여 줍니다.

    업종이나 계정코드 체계를 가정하지 않습니다(특정 계정코드 범위·계정명으로 분류하지 않음).
    - 라인 금액 합계 = 그 계정 라인 금액(차변 + 대변)의 합. 금액 비중(%) = 라인 금액 합계 ÷ 전체 라인 금액 합계 × 100.
    groups: {계정코드: 그룹 이름}을 주면 '계정 그룹' 열을 붙입니다(없는 계정은 빈칸). 사용자가 계정 그룹을 직접 정하는
            기능을 나중에 붙일 자리이며, 지금 화면에서는 쓰지 않습니다.
    """
    columns = ACCOUNT_COLUMNS[:2] + (["계정 그룹"] if groups is not None else []) + ACCOUNT_COLUMNS[2:]
    if len(df) == 0:
        return pd.DataFrame(columns=columns)
    g = df.groupby("account_code")
    table = pd.DataFrame({
        "라인 수": g.size(),
        "전표 수": g["je_id"].nunique(),
        "차변 합계": g["debit"].sum(),
        "대변 합계": g["credit"].sum(),
        "라인 금액 합계": g["line_amount"].sum(),
        "라인 금액 평균": g["line_amount"].mean(),
        "라인 금액 중앙값": g["line_amount"].median(),
        "라인 금액 최소": g["line_amount"].min(),
        "라인 금액 최대": g["line_amount"].max(),
    })
    table["순액(차변-대변)"] = table["차변 합계"] - table["대변 합계"]
    total = table["라인 금액 합계"].sum()
    table["금액 비중(%)"] = (table["라인 금액 합계"] / total * 100).round(2) if total else None
    display, _ = _account_names(df)
    table["계정과목명"] = display
    if groups is not None:
        lookup = {str(k): v for k, v in groups.items()}
        table["계정 그룹"] = [lookup.get(str(c), "") for c in table.index]
    table = table.reset_index().rename(columns={"account_code": "계정코드"})
    return table[columns].sort_values("계정코드", key=_code_sort_key).reset_index(drop=True)


TOP_MEASURES = ["라인 금액 합계", "라인 수", "전표 수", "차변 합계", "대변 합계"]


def top_accounts(df: pd.DataFrame, measure="라인 금액 합계", n=10):
    """기준(measure)이 큰 순서로 상위 n개 계정. 값이 같으면 계정코드 순서입니다."""
    if measure not in TOP_MEASURES:
        raise ValueError(f"기준은 {', '.join(TOP_MEASURES)} 중 하나여야 합니다: {measure}")
    accounts = account_summary(df)
    if len(accounts) == 0:
        return accounts
    return accounts.sort_values(measure, ascending=False, kind="stable").head(int(n)).reset_index(drop=True)


def account_group_summary(df: pd.DataFrame, groups):
    """계정 그룹별 합계. groups: {계정코드: 그룹 이름}. 그룹에 넣지 않은 계정은 '(그룹 없음)'으로 모읍니다.

    사용자 정의 계정 그룹을 위한 구조입니다. 그룹 이름과 소속 계정은 사용자가 정하며, 프로그램이 정하지 않습니다.
    """
    columns = ["계정 그룹", "계정 수", "해당 계정", "라인 수", "차변 합계", "대변 합계", "순액(차변-대변)", "라인 금액 합계"]
    accounts = account_summary(df, groups)
    if len(accounts) == 0:
        return pd.DataFrame(columns=columns)
    accounts["계정 그룹"] = accounts["계정 그룹"].replace("", "(그룹 없음)")
    rows = []
    for name, part in accounts.groupby("계정 그룹", sort=False):
        rows.append({"계정 그룹": name, "계정 수": len(part),
                     "해당 계정": ", ".join(f"{c} {n}" for c, n in zip(part["계정코드"], part["계정과목명"])),
                     **{c: part[c].sum() for c in columns[3:]}})
    return pd.DataFrame(rows, columns=columns)


def accounts_in_group(groups, group):
    """계정 그룹에 속하는 계정코드 목록. groups: {계정코드: 그룹 이름}."""
    return [str(code) for code, name in groups.items() if name == group]


def _code_sort_key(codes):
    """숫자 계정코드는 숫자 순서로, 나머지는 그 뒤에 글자 순서로 정렬합니다."""
    return codes.map(lambda c: (0, int(c), "") if str(c).isdigit() else (1, 0, str(c)))


# ---------------------------------------------------------------- 3. 월별 분석
def _filter_accounts(df, account_codes):
    if account_codes is None:
        return df
    return df[df["account_code"].isin([str(c) for c in account_codes])]


def monthly_summary(df: pd.DataFrame, account_codes=None, closing_month=None):
    """연월별 전표 수, 라인 수, 차변·대변 합계와 그 달의 회계연도.

    첫 달부터 마지막 달까지 모든 달을 표시하고, 거래가 없는 달은 0으로 둡니다(비고에 '거래 없음').
    전표 수는 그 달에 라인이 있는 전표번호 수입니다. 여러 달에 걸친 전표는 각 달에 한 번씩 셉니다.
    """
    columns = ["연월", "연도", "월", "회계연도", "전표 수", "라인 수", "차변 합계", "대변 합계", "비고"]
    closing_month = _closing(df, closing_month)
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
    table["회계연도"] = [fiscal_year_of(y, m, closing_month) for y, m in zip(table["연도"], table["월"])]
    return table[columns]


def fiscal_years(df: pd.DataFrame):
    """데이터에 있는 회계연도 목록, 오름차순. 회계연도는 prepare()의 결산월로 계산한 값입니다."""
    if len(df) == 0:
        return []
    return sorted(int(y) for y in df["fiscal_year"].unique())


def default_comparison_years(df: pd.DataFrame):
    """기본 비교 회계연도: 가장 최근 회계연도를 당기, 바로 앞 회계연도를 전기로 봅니다.

    바로 앞 회계연도의 자료가 없으면 (None, 당기)를 돌려줍니다.
    """
    years = fiscal_years(df)
    if not years:
        return None, None
    current = years[-1]
    prior = current - 1 if current - 1 in years else None
    return prior, current


FISCAL_YEAR_COLUMNS = ["회계연도", "회계기간", "자료 시작일", "자료 종료일", "자료가 있는 월 수", "전표 수", "라인 수",
                       "차변 합계", "대변 합계", "비고"]


def fiscal_year_summary(df: pd.DataFrame, closing_month=None):
    """회계연도별 요약. 자료가 12개월에 못 미치는 회계연도는 비고에 적어, 연도 간 합계를 그대로 비교하지 않게 합니다.

    전표 수는 그 회계연도에 라인이 있는 전표번호 수입니다(회계연도에 걸친 전표는 각 연도에 한 번씩).
    """
    closing_month = _closing(df, closing_month)
    if len(df) == 0:
        return pd.DataFrame(columns=FISCAL_YEAR_COLUMNS)
    rows = []
    for fy, part in df.groupby("fiscal_year"):
        start, end = fiscal_year_range(int(fy), closing_month)
        months = int(part["year_month"].nunique())
        rows.append({
            "회계연도": int(fy), "회계기간": f"{start} ~ {end}",
            "자료 시작일": part["posting_date"].min(), "자료 종료일": part["posting_date"].max(),
            "자료가 있는 월 수": months, "전표 수": int(part["je_id"].nunique()), "라인 수": int(len(part)),
            "차변 합계": float(part["debit"].sum()), "대변 합계": float(part["credit"].sum()),
            "비고": "" if months == 12 else f"12개월 중 {months}개월만 자료 있음",
        })
    return pd.DataFrame(rows, columns=FISCAL_YEAR_COLUMNS)


def compare_years(df: pd.DataFrame, prior_year, current_year, measure="차변 합계", account_codes=None,
                  closing_month=None):
    """전기와 당기 회계연도의 같은 달끼리 비교합니다 (회계연도 첫 달부터 12개월 + 합계 행).

    - 회계연도와 월 순서는 결산월로 정합니다. 예: 3월 결산이면 4월, 5월, ..., 3월 순서이고, 2026 회계연도의
      4월은 2025년 4월, 3월은 2026년 3월입니다. 12월 결산이면 1월~12월입니다.
    - 증감 = 당기 - 전기, 증감률(%) = 증감 / 전기 × 100
    - 전기 값이 0이면 증감률을 계산하지 않습니다(빈칸).
    - 어느 한 회계연도에 그 달 자료가 아예 없으면 비고에 표시합니다. 예를 들어 당기 자료가 9월까지만 있으면
      나머지 달은 '당기 자료 없음'으로 표시해, 거래가 줄어든 것과 자료가 없는 것을 구분합니다.
    """
    if measure not in MEASURES:
        raise ValueError(f"비교 항목은 {', '.join(MEASURES)} 중 하나여야 합니다: {measure}")
    closing_month = _closing(df, closing_month)
    columns = ["월", "전기", "당기", "증감", "증감률(%)", "비고"]
    part = _filter_accounts(df, account_codes)
    if len(df):
        fy = df["year"].where(df["month"] <= closing_month, df["year"] + 1)
        all_months = pd.DataFrame({"fy": fy, "month": df["month"]}).drop_duplicates()
        part_fy = part["year"].where(part["month"] <= closing_month, part["year"] + 1)
    else:
        all_months = pd.DataFrame(columns=["fy", "month"])
        part_fy = pd.Series(dtype=int)

    rows = []
    for month in fiscal_month_order(closing_month):
        values, missing = {}, []
        for label, year in (("전기", prior_year), ("당기", current_year)):
            sub = part[(part_fy == year) & (part["month"] == month)]
            values[label] = _measure_value(sub, measure)
            has_data = ((all_months["fy"] == year) & (all_months["month"] == month)).any()
            if not has_data:
                missing.append(f"{label} 자료 없음")
        rows.append({"월": f"{month}월", **values, "비고": ", ".join(missing)})
    total = {"월": "합계",
             "전기": _measure_value(part[part_fy == prior_year], measure),
             "당기": _measure_value(part[part_fy == current_year], measure),
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
