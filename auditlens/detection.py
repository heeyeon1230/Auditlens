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
                "결산일과 같은 말일은 (a)로 봄). (b) 회계처리일은 결산일 이전인데 입력일시(시스템에 실제 입력한 시각)가 결산일 다음 날 "
                "이후(결산 후 입력). (c) 입력일시가 토·일요일.",
        "한계": "기말에는 감가상각·충당금 같은 정상 결산 분개가 몰립니다. 영업일은 월~금이며 공휴일은 반영하지 않습니다. "
                "자동 배치 전표는 주말에도 정상적으로 생길 수 있습니다. 입력일시 열이 없으면 (b)·(c)는 검사하지 않습니다.",
    },
    "R4": {
        "이름": "수기·조정 분개와 특정 조건",
        "단위": "전표",
        "목적": "자동 전표는 시스템 통제를 거치지만 수기·조정 분개는 사람의 판단이 개입하고, 직무 분리가 안 된 전표는 검토 "
                "필요성이 높습니다.",
        "조건": "(a) 전표 원천 또는 전표 유형이 사용자가 고른 값(기본: 원천 '수기', 유형 '조정'·'결산'). (b) 입력자와 승인자가 "
                "같음. (c) 입력자의 그 회계연도 전표 수가 전체의 기준 비율(기본 1%) 미만. (d) 적요가 비었거나 사용자 지정 "
                "키워드(기본: 수정, 조정, 오류, 임시)를 포함.",
        "한계": "'수기'를 나타내는 값은 ERP마다 달라 사용자가 골라야 합니다. 작은 회사는 입력자와 승인자가 같은 것이 일상일 수 "
                "있습니다. 평소 전표가 적은 정상 사용자(배치 계정, 결산 담당)도 (c)에 걸립니다. 키워드는 표현이 다양해 놓치는 "
                "것이 많고 정상 적요에도 쓰입니다. 열이 없는 세부 조건은 검사하지 않습니다. 적요 열은 있지만 값이 모두 비어 "
                "있으면 (d)는 평가 불가로 두고 탐지하지 않습니다. 적요를 일부만 쓰는 회사는 (d) '적요 없음'이 많아질 수 있으니 "
                "적요 입력률을 함께 보세요.",
    },
    "R5": {
        "이름": "신규 거래처·신규 계정 조합",
        "단위": "거래처 (또는 기존 거래처×계정), 해당 전표",
        "목적": "기말에 갑자기 나타나 큰 금액을 거래한 거래처는 실재성·특수관계자 여부를 확인할 필요가 있을 수 있습니다.",
        "조건": "파일 안에서 거래처의 최초 등장일이 분석 기간 마지막 n개월(기본 2개월) 안이고, 그 거래처의 합계 금액이 사용자가 "
                "입력한 기준금액 이상. 기존 거래처가 처음 쓰는 계정 조합도 같은 방식으로 봅니다. 합계 금액은 전표마다 한 번만 "
                "셉니다(그 거래처 라인의 차변 합계와 대변 합계 중 큰 값).",
        "한계": "'신규'는 올린 파일 기준일 뿐이며, 파일 시작 전에 거래한 거래처도 신규로 보일 수 있습니다. 거래처 마스터가 없어 "
                "표기 차이가 오탐을 만듭니다. 기준금액은 감사인의 중요성 판단에 따라 정해야 하며 기본값이 없습니다. 거래처가 "
                "빈 라인은 판단하지 않습니다.",
    },
    "R6": {
        "이름": "중복 가능 분개",
        "단위": "전표 쌍",
        "목적": "같은 증빙의 이중 입력은 비용·매출 과대계상이나 이중 지급으로 이어질 수 있습니다.",
        "조건": "서로 다른 전표에 계정·거래처·방향·금액이 같은 라인이 있고 회계처리일 차이가 n일(기본 7일, 달력 기준) 이내. "
                "적요가 있으면 적요 일치 여부를 함께 표시합니다.",
        "한계": "같은 금액의 정상 반복 거래(주간 용역비 등)가 걸립니다. 금액을 조금 바꾼 중복이나 다른 거래처명으로 입력한 중복은 "
                "놓칩니다. 거래처가 빈 라인과 0원 라인은 비교하지 않습니다. 유사 적요 비교는 하지 않습니다.",
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
    r4_sources: tuple = ("수기",)                       # (a) 수기로 볼 전표 원천 값
    r4_types: tuple = ("조정", "결산")                  # (a) 조정으로 볼 전표 유형 값
    r4_rare_share: float = 0.01                         # (c) 입력자 전표 비율 기준(미만)
    r4_keywords: tuple = ("수정", "조정", "오류", "임시")  # (d) 적요 키워드
    r5_threshold: float = None                          # 기준금액(원). 입력하지 않으면 R5를 검사하지 않음
    r5_months: int = 2                                  # 분석 기간 마지막 n개월
    r6_days: int = 7                                    # 회계처리일 차이(일) 이내
    # 규칙별 켜기/끄기 (기본은 모두 켜짐). 꺼진 규칙은 계산하지 않고 종합에도 넣지 않습니다.
    r1_enabled: bool = True
    r2a_enabled: bool = True
    r2b_enabled: bool = True
    r3_enabled: bool = True
    r4_enabled: bool = True
    r5_enabled: bool = True
    r6_enabled: bool = True

    def as_table(self):
        labels = {
            "r1_multiplier": "R1 IQR 배수", "r1_min_lines": "R1 계정·방향별 최소 라인 수",
            "r2_ratio": "R2(a) 이전 거래 중앙값 대비 배수", "r2_min_prior": "R2(a) 최소 이전 거래 수",
            "r2_month_high": "R2(b) 다른 달 중앙값 대비 상한 배수", "r2_month_low": "R2(b) 다른 달 중앙값 대비 하한 배수",
            "r3_window": "R3(a) 전후 영업일 수", "r3_month_end": "R3(a-2) 매월 말일 보조 옵션",
            "closing_month": "결산월", "closing_day": "결산일",
            "r4_sources": "R4(a) 수기로 볼 전표 원천", "r4_types": "R4(a) 조정으로 볼 전표 유형",
            "r4_rare_share": "R4(c) 입력자 전표 비율 기준(미만)", "r4_keywords": "R4(d) 적요 키워드",
            "r5_threshold": "R5 기준금액(원)", "r5_months": "R5 마지막 n개월", "r6_days": "R6 회계처리일 차이(일) 이내",
            **{field: f"{rule} 사용" for rule, field in RULE_SWITCHES.items()},
        }

        def show(v):
            if isinstance(v, bool):
                return "켜짐" if v else "꺼짐"
            if isinstance(v, tuple):
                return ", ".join(v) if v else "(선택 안 함)"
            return "입력 안 함 (검사하지 않음)" if v is None else v
        base = asdict(DetectionSettings())
        return pd.DataFrame([{"기준": labels[k], "기본값": show(base[k]), "적용값": show(v),
                              "기본값과 다름": "예" if v != base[k] else ""} for k, v in asdict(self).items()])

    def changed(self):
        """기본값과 다른 설정 항목 이름(필드 이름) 목록."""
        base = asdict(DetectionSettings())
        return [k for k, v in asdict(self).items() if v != base[k]]

    def disabled_rules(self):
        """꺼진 규칙 코드 목록 (예: ["R1", "R6"])."""
        return [rule for rule, field in RULE_SWITCHES.items() if not getattr(self, field)]


# 규칙 코드 → 켜기/끄기 설정 이름
RULE_SWITCHES = {"R1": "r1_enabled", "R2(a)": "r2a_enabled", "R2(b)": "r2b_enabled", "R3": "r3_enabled",
                 "R4": "r4_enabled", "R5": "r5_enabled", "R6": "r6_enabled"}
OFF_NOTE = "사용자가 이 규칙을 껐습니다. 계산하지 않았고 종합(전표별)에도 넣지 않았습니다."


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
EXCLUDED_COLUMNS = LINE_COLUMNS + ["입력일시", "적요", "제외 사유"]


def _excluded_lines(lines, reasons):
    """비교에서 뺀 라인과 사유. reasons는 lines와 같은 순서의 사유 목록입니다.

    금액은 파일 값 그대로이고, 입력일시·적요(열이 있을 때)와 파일 행 번호를 함께 남겨 원래 거래를 찾을 수 있게 합니다.
    """
    rows = [{**_line_info(r), "입력일시": getattr(r, "entry_date", pd.NaT),
             "적요": getattr(r, "description", "") if pd.notna(getattr(r, "description", "")) else "",
             "제외 사유": why} for r, why in zip(lines.itertuples(index=False), reasons)]
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
                                       f"(회계처리일 {period}, 이 라인과 같은 날이거나 뒤의 거래는 제외, 기준 {s.r2_min_prior}건 이상)의 중앙값은 {median:,.0f}원이고 "
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
            # (b) 회계처리일이 속한 회계연도의 결산일보다 '입력일시'의 날짜가 뒤인 경우. 회계처리일은 결산일 찾는 데만 씁니다.
            if entry_day > closing:
                counts["(b) 결산 후 입력"] += 1
                conditions.append("(b) 결산 후 입력")
                reasons.append(f"회계처리일은 {posting:%Y-%m-%d}로 결산일 {closing:%Y-%m-%d} 이전인데, 입력일시는 "
                               f"{entry:%Y-%m-%d %H:%M}로 결산일 {(entry_day - closing).days}일 뒤입니다(입력일시 기준).")
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



# ---------------------------------------------------------------- 공통 (R4~R6)
def _text(series):
    return series.fillna("").astype(str).str.strip() if series is not None else None


def _values_by_voucher(df, col):
    """전표번호별로 열 값(앞뒤 공백 제거, 빈 값 제외)을 파일 순서대로 중복 없이 모읍니다. {전표번호: [값, ...]}"""
    if col not in df:
        return {}
    work = pd.DataFrame({"je_id": df["je_id"], "v": _text(df[col])})
    work = work[work["v"] != ""].drop_duplicates()
    return work.groupby("je_id", sort=False)["v"].agg(list).to_dict()


def _voucher_frame(df):
    """전표 단위 기본 정보: 회계처리일(가장 이른 날), 전표 금액(차변 합계), 포함 계정, 파일 행 번호."""
    lines = df.sort_values("file_row") if "file_row" in df else df
    base = lines.groupby("je_id", sort=False).agg(date=("posting_date", "min"), amount=("debit", "sum"))
    labels = (lines["account_code"].astype(str) + " " + lines["account_name"].astype(str)).str.strip()
    accounts = pd.DataFrame({"je_id": lines["je_id"], "a": labels}).drop_duplicates() \
        .groupby("je_id", sort=False)["a"].agg(", ".join)
    rows = lines["file_row"].astype(str).groupby(lines["je_id"], sort=False).agg(", ".join) if "file_row" in lines \
        else pd.Series("", index=base.index)
    return {je: {"회계처리일": r.date, "전표 금액": float(r.amount), "포함 계정": accounts.get(je, ""),
                 "파일 행 번호": rows.get(je, "")} for je, r in base.iterrows()}


# ---------------------------------------------------------------- R4
R4_COLUMNS = ["판정", "전표번호", "회계처리일", "해당 조건", "전표 원천", "전표 유형", "입력자", "승인자", "적요",
              "포함 계정", "전표 금액", "파일 행 번호", "탐지 이유"]


def _fiscal_year_end(posting, s):
    return _closing_for(posting.date(), s)


def description_fill_rate(df):
    """적요 입력률. 라인 기준(적요가 있는 라인 / 전체 라인)과 전표 기준(모든 라인의 적요가 빈 전표 수)을 돌려줍니다.

    적요 열이 없으면 None입니다. 공백만 있는 값은 빈 값으로 봅니다.
    """
    if "description" not in df:
        return None
    text = _text(df["description"])
    lines, filled = int(len(df)), int(text.ne("").sum())
    vouchers = int(df["je_id"].nunique())
    blank_vouchers = int((~text.ne("").groupby(df["je_id"]).any()).sum()) if lines else 0
    rate = filled / lines if lines else 0.0
    return {"라인 수": lines, "적요 있는 라인 수": filled, "적요 입력률": rate, "전표 수": vouchers,
            "적요 없는 전표 수": blank_vouchers,
            "문장": f"적요 입력률: 라인 {filled:,} / {lines:,} ({rate:.2%}). 모든 라인의 적요가 빈 전표 "
                    f"{blank_vouchers:,} / {vouchers:,}개."}


def r4_manual_entries(df, settings=None):
    """R4. 전표 단위로 (a) 수기·조정 값, (b) 입력자=승인자, (c) 드문 입력자, (d) 적요 없음·키워드를 봅니다.

    반환값: (탐지 표, 처리 내역 목록, 조건별 전표 수 표). 열이 없는 세부 조건은 검사하지 않고 그 사실을 적습니다.
    값은 앞뒤 공백만 빼고 그대로 비교합니다(대소문자·표기를 바꾸지 않음).
    """
    s = settings or DetectionSettings()
    notes = []
    count_cols = ["조건", "해당 전표 수", "적용 여부"]
    if len(df) == 0:
        return pd.DataFrame(columns=R4_COLUMNS), notes, pd.DataFrame(columns=count_cols)
    has = {c: c in df and _text(df[c]).ne("").any() for c in ["source", "je_type", "created_by", "approved_by", "description"]}
    applied = {
        "(a) 수기·조정 전표": "적용" if (has["source"] or has["je_type"]) and (s.r4_sources or s.r4_types)
        else ("검사 안 함 (전표 원천·유형 열 없음)" if not (has["source"] or has["je_type"]) else "검사 안 함 (선택한 값 없음)"),
        "(b) 입력자 = 승인자": "적용" if has["created_by"] and has["approved_by"] else "검사 안 함 (입력자 또는 승인자 열 없음)",
        "(c) 드문 입력자": "적용" if has["created_by"] else "검사 안 함 (입력자 열 없음)",
        # (d) 적요 열이 있어도 값이 모두 비어 있으면 '적요 없음'이 모든 전표에 해당하게 되므로 평가 불가로 둡니다.
        "(d) 적요 없음·키워드": ("적용" if has["description"] else "평가 불가 (적요 열의 값이 모두 비어 있음)")
        if "description" in df else "검사 안 함 (적요 열 없음)",
    }
    for cond, state in applied.items():
        if state != "적용":
            notes.append(f"{cond}: {state}")
    if "description" in df:
        notes.append(description_fill_rate(df)["문장"])

    info = _voucher_frame(df)
    values = {c: _values_by_voucher(df, c) for c in ["source", "je_type", "created_by", "approved_by", "description"]}
    # (c) 회계연도별 입력자 전표 수 (입력자가 빈 전표는 세지 않음). 한 전표에 입력자가 여럿이면 첫 값을 씁니다.
    creators = {}
    if has["created_by"]:
        for je, v in info.items():
            users = values["created_by"].get(je, [])
            creators[je] = (users[0] if users else "", _fiscal_year_end(v["회계처리일"], s))
        by_fy = pd.DataFrame([(u, fy) for u, fy in creators.values() if u], columns=["user", "fy"])
        fy_total = by_fy.groupby("fy").size().to_dict()
        user_count = by_fy.groupby(["fy", "user"]).size().to_dict()

    counts = {k: 0 for k in applied}
    skipped_b = 0
    rows = []
    for je in info:
        conditions, reasons = [], []
        sources, types = values["source"].get(je, []), values["je_type"].get(je, [])
        makers, approvers = values["created_by"].get(je, []), values["approved_by"].get(je, [])
        descs = values["description"].get(je, [])

        if applied["(a) 수기·조정 전표"] == "적용":
            hit_src = [v for v in sources if v in s.r4_sources]
            hit_type = [v for v in types if v in s.r4_types]
            if hit_src or hit_type:
                counts["(a) 수기·조정 전표"] += 1
                conditions.append("(a) 수기·조정 전표")
                what = ([f"전표 원천 '{v}'" for v in hit_src] + [f"전표 유형 '{v}'" for v in hit_type])
                reasons.append(f"{', '.join(what)}에 해당합니다(선택한 값: 원천 {list(s.r4_sources)}, 유형 {list(s.r4_types)}).")
        if applied["(b) 입력자 = 승인자"] == "적용":
            if not makers or not approvers:
                skipped_b += 1
            elif set(makers) & set(approvers):
                same = sorted(set(makers) & set(approvers))[0]
                counts["(b) 입력자 = 승인자"] += 1
                conditions.append("(b) 입력자 = 승인자")
                reasons.append(f"입력자와 승인자가 모두 '{same}'입니다.")
        if applied["(c) 드문 입력자"] == "적용" and creators.get(je, ("", None))[0]:
            user, fy = creators[je]
            n, total = user_count[(fy, user)], fy_total[fy]
            if n / total < s.r4_rare_share:
                counts["(c) 드문 입력자"] += 1
                conditions.append("(c) 드문 입력자")
                reasons.append(f"입력자 '{user}'의 {fy:%Y-%m-%d} 결산 회계연도 전표는 {n:,}개로 그해 전체 {total:,}개의 "
                               f"{n / total:.2%}입니다(기준 {s.r4_rare_share:.0%} 미만).")
        if applied["(d) 적요 없음·키워드"] == "적용":
            if not descs:
                counts["(d) 적요 없음·키워드"] += 1
                conditions.append("(d) 적요 없음")
                reasons.append("모든 라인의 적요가 비어 있습니다.")
            else:
                found = [k for k in s.r4_keywords if k and any(k in d for d in descs)]
                if found:
                    counts["(d) 적요 없음·키워드"] += 1
                    conditions.append("(d) 적요 키워드")
                    reasons.append(f"적요에 키워드 {', '.join(repr(k) for k in found)}가 들어 있습니다.")
        if conditions:
            v = info[je]
            rows.append({"판정": REVIEW_LABEL, "전표번호": je, "회계처리일": v["회계처리일"],
                         "해당 조건": ", ".join(conditions), "전표 원천": ", ".join(sources), "전표 유형": ", ".join(types),
                         "입력자": ", ".join(makers), "승인자": ", ".join(approvers), "적요": " / ".join(descs),
                         "포함 계정": v["포함 계정"], "전표 금액": v["전표 금액"], "파일 행 번호": v["파일 행 번호"],
                         "탐지 이유": " ".join(reasons)})
    if skipped_b:
        notes.append(f"입력자나 승인자가 비어 있는 전표 {skipped_b:,}개는 (b)를 검사하지 않았습니다.")
    count_table = pd.DataFrame([{"조건": k, "해당 전표 수": counts[k], "적용 여부": applied[k]} for k in applied],
                               columns=count_cols)
    return pd.DataFrame(rows, columns=R4_COLUMNS), notes, count_table


# ---------------------------------------------------------------- R5
R5_COLUMNS = ["판정", "구분", "거래처", "계정", "최초 등장일", "판단 기간", "전표 수", "라인 수", "합계 금액", "기준금액",
              "전표번호", "탐지 이유"]
R5_CANDIDATE_COLUMNS = ["구분", "거래처", "계정", "최초 등장일", "전표 수", "합계 금액", "기준금액 이상"]


def _voucher_amount_sum(part):
    """전표마다 한 번만 세는 합계: 전표별로 (차변 합계, 대변 합계) 중 큰 값을 더합니다."""
    per = part.groupby("je_id").agg(dr=("debit", "sum"), cr=("credit", "sum"))
    return float(np.maximum(per["dr"], per["cr"]).sum())


def r5_new_vendors(df, settings=None):
    """R5. 분석 기간 마지막 n개월에 처음 나타난 거래처, 기존 거래처의 처음 쓰는 계정 조합.

    반환값: (탐지 표, 처리 내역 목록, 신규 후보 전체 표). 기준금액이 없으면 탐지 표는 비고 후보 표만 돌려줍니다.
    """
    s = settings or DetectionSettings()
    notes = []
    empty = (pd.DataFrame(columns=R5_COLUMNS), notes, pd.DataFrame(columns=R5_CANDIDATE_COLUMNS))
    if len(df) == 0:
        return empty
    if "vendor" not in df or _text(df["vendor"]).eq("").all():
        notes.append("거래처 열이 없거나 모두 비어 있어 R5를 검사하지 않았습니다.")
        return empty
    lines = df.copy()
    lines["vendor"] = _text(lines["vendor"])
    blank = lines["vendor"] == ""
    if blank.any():
        notes.append(f"거래처가 비어 있는 라인 {int(blank.sum()):,}건은 R5에서 판단하지 않았습니다.")
    lines = lines[~blank]
    last = lines["posting_date"].max().to_period("M")
    start = (last - (s.r5_months - 1)).start_time
    end = lines["posting_date"].max()
    period = f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} (파일의 마지막 {s.r5_months}개월)"
    if s.r5_threshold is None:
        notes.append("R5 기준금액을 입력하지 않아 탐지하지 않았습니다. 아래 신규 후보 목록은 참고용입니다.")

    first_vendor = lines.groupby("vendor")["posting_date"].min()
    candidates, rows = [], []

    def add(kind, vendor, account, part, first):
        total = _voucher_amount_sum(part) if account == "" else float(part["line_amount"].sum())
        ok = s.r5_threshold is not None and total >= s.r5_threshold
        candidates.append({"구분": kind, "거래처": vendor, "계정": account, "최초 등장일": first,
                           "전표 수": part["je_id"].nunique(), "합계 금액": total,
                           "기준금액 이상": "예" if ok else ("기준금액 미입력" if s.r5_threshold is None else "아니오")})
        if ok:
            target = f"{vendor}" + (f"·{account}" if account else "")
            rows.append({"판정": REVIEW_LABEL, "구분": kind, "거래처": vendor, "계정": account, "최초 등장일": first,
                         "판단 기간": period, "전표 수": part["je_id"].nunique(), "라인 수": len(part),
                         "합계 금액": total, "기준금액": s.r5_threshold,
                         "전표번호": ", ".join(dict.fromkeys(part.sort_values("posting_date")["je_id"])),
                         "탐지 이유": f"{target}의 파일 내 최초 등장일 {first:%Y-%m-%d}가 판단 기간({period}) 안이고, "
                                   f"합계 금액 {total:,.0f}원이 기준금액 {s.r5_threshold:,.0f}원 이상입니다"
                                   f"(전표 {part['je_id'].nunique()}개{', 전표마다 한 번만 셈' if not account else ''})."})

    for vendor, first in first_vendor.items():
        if first >= start:
            add("신규 거래처", vendor, "", lines[lines["vendor"] == vendor], first)
    old = lines[lines["vendor"].map(first_vendor) < start]
    first_combo = old.groupby(["vendor", "account_code"])["posting_date"].min()
    for (vendor, code), first in first_combo.items():
        if first >= start:
            part = old[(old["vendor"] == vendor) & (old["account_code"] == code)]
            name = part["account_name"].mode().iloc[0]
            add("기존 거래처의 신규 계정", vendor, f"{code} {name}", part, first)
    return pd.DataFrame(rows, columns=R5_COLUMNS), notes, pd.DataFrame(candidates, columns=R5_CANDIDATE_COLUMNS)


# ---------------------------------------------------------------- R6
R6_COLUMNS = ["판정", "전표번호 A", "전표번호 B", "회계처리일 A", "회계처리일 B", "일수 차이", "거래처", "일치 라인",
              "일치 금액", "적요 일치", "파일 행 번호 A", "파일 행 번호 B", "탐지 이유"]


def r6_duplicates(df, settings=None):
    """R6. 서로 다른 전표의 같은 계정·거래처·방향·금액 라인이 n일 이내에 있으면 전표 쌍으로 묶어 보여 줍니다.

    반환값: (탐지 표, 처리 내역 목록). 같은 전표 안의 라인끼리는 비교하지 않습니다.
    """
    s = settings or DetectionSettings()
    notes = []
    if len(df) == 0:
        return pd.DataFrame(columns=R6_COLUMNS), notes
    if "vendor" not in df or _text(df["vendor"]).eq("").all():
        notes.append("거래처 열이 없거나 모두 비어 있어 R6을 검사하지 않았습니다(설계서 조건: 같은 거래처).")
        return pd.DataFrame(columns=R6_COLUMNS), notes
    lines = _lines(df)
    lines["description"] = _text(lines["description"]) if "description" in lines else ""
    blank = lines["vendor"] == ""
    zero = (lines["amount"] == 0) & ~blank
    both = (lines["direction"] == "차변·대변") & ~blank & ~zero
    for mask, text in [(blank, "거래처가 비어 있어"), (zero, "0원이라"), (both, "한 행에 차변·대변이 모두 있어")]:
        if mask.any():
            notes.append(f"{text} 비교하지 않은 라인 {int(mask.sum()):,}건이 있습니다.")
    lines = lines[~blank & ~zero & ~both]
    keys = ["account_code", "vendor", "direction", "amount"]
    lines = lines[lines.duplicated(keys, keep=False)]  # 같은 계정·거래처·방향·금액이 2번 이상 나오는 라인만 비교

    pairs = {}
    for (code, vendor, direction, amount), part in lines.groupby(["account_code", "vendor", "direction", "amount"]):
        if part["je_id"].nunique() < 2:
            continue
        recs = part.sort_values(["posting_date", "file_row"]).to_dict("records")
        for i, a in enumerate(recs):
            for b in recs[i + 1:]:
                days = (b["posting_date"] - a["posting_date"]).days
                if days > s.r6_days:
                    break
                if a["je_id"] == b["je_id"]:
                    continue
                x, y = (a, b) if a["je_id"] < b["je_id"] else (b, a)
                key = (x["je_id"], y["je_id"])
                item = pairs.setdefault(key, {"lines": [], "a": x, "b": y, "days": abs(days)})
                item["lines"].append((x, y))
    rows = []
    for (ja, jb), item in sorted(pairs.items()):
        matched = item["lines"]
        desc_a = [x["description"] for x, _ in matched]
        desc_b = [y["description"] for _, y in matched]
        if not any(desc_a) and not any(desc_b):
            same_desc = "적요 없음"
        else:
            same_desc = "일치" if all(p == q and p for p, q in zip(desc_a, desc_b)) else "다름"
        text = "; ".join(f"{x['account_code']} {x['account_name']} {x['direction']} {x['amount']:,.0f}원" for x, _ in matched)
        days = max(abs((y["posting_date"] - x["posting_date"]).days) for x, y in matched)
        rows.append({"판정": REVIEW_LABEL, "전표번호 A": ja, "전표번호 B": jb,
                     "회계처리일 A": item["a"]["posting_date"], "회계처리일 B": item["b"]["posting_date"],
                     "일수 차이": days, "거래처": item["a"]["vendor"], "일치 라인": text,
                     "일치 금액": max(x["amount"] for x, _ in matched), "적요 일치": same_desc,
                     "파일 행 번호 A": ", ".join(str(x["file_row"]) for x, _ in matched),
                     "파일 행 번호 B": ", ".join(str(y["file_row"]) for _, y in matched),
                     "탐지 이유": f"전표 {ja}와 {jb}에 거래처 {item['a']['vendor']}의 같은 계정·방향·금액 라인이 있습니다"
                               f"({text}). 회계처리일 차이 {days}일(기준 {s.r6_days}일 이내), 적요 {same_desc}."})
    return pd.DataFrame(rows, columns=R6_COLUMNS), notes


# ---------------------------------------------------------------- 종합
SUMMARY_COLUMNS = ["전표번호", "판정", "검토 구분", "해당 규칙 수", "해당 규칙", "해당 세부 조건 수", "해당 세부 조건",
                   "회계처리일", "포함 계정", "전표 금액", "탐지 이유"]
REASON_COLUMNS = ["전표번호", "규칙", "대상", "탐지 이유"]
# 종합표의 검토 구분. 수기·조정 여부(R4(a))만 해당하는 전표는 보조 목록으로 나눕니다(오류·부정의 증거가 아님).
PRIORITY_GROUP = "우선 검토"
SUPPORT_GROUP = "보조 (수기·조정 여부만 해당)"
R4_MANUAL_ID = "R4(a)"  # R4 (a) 수기·조정 전표
SUPPORT_LABEL = "수기·조정 전표(보조 정보)"  # 보조 목록 전표의 판정 문구 (수기 전표 자체를 위험으로 보지 않음)
NO_GROUP = "해당 없음"
PERIOD_VOUCHER_COLUMNS = ["전표번호", "회계처리일", "이 계정 라인 수", "이 계정 금액", "포함 계정", "전표 금액", "종합 검토 구분",
                          "판정", "해당 규칙"]


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
    r4: pd.DataFrame = None
    r4_notes: list = None
    r4_counts: pd.DataFrame = None
    r5: pd.DataFrame = None
    r5_notes: list = None
    r5_candidates: pd.DataFrame = None
    r6: pd.DataFrame = None
    r6_notes: list = None


def summarize(df, r1, r2a, r3, extra=()):
    """전표 단위 종합표와 사유 표.

    수기·조정 여부(R4(a))만 해당하는 전표는 '검토 구분'을 보조 목록으로 두고 맨 뒤에 놓습니다. 나머지(우선 검토 목록)는
    해당 규칙 수 → 해당 세부 조건 수가 많은 순서로 정렬합니다. 점수나 가중치는 만들지 않습니다.

    R2(b)는 계정-월(기간) 단위 판단이라 전표 종합표에 넣지 않습니다. 전표 금액은 그 전표의 차변 합계(원)입니다.
    반환값: (종합표, 사유 표). 사유 표에는 한 전표가 여러 규칙·여러 라인에서 탐지된 사유가 모두 한 행씩 들어갑니다.
    extra: R4~R6처럼 표 모양이 다른 규칙의 사유 목록. {"전표번호", "회계처리일", "규칙", "대상", "탐지 이유"} 사전들.
           세부 조건이 있는 규칙(R4)은 "조건"에 '해당 조건' 항목 목록을 함께 넣습니다.
    """
    found, reason_rows = {}, []

    def note_conditions(item, rule, conditions):
        # 세부 조건: R3·R4는 '해당 조건'의 각 항목, 그 밖의 규칙은 규칙 하나를 조건 하나로 셉니다(라인이 여럿이어도 1개).
        for c in conditions or [rule]:
            name = rule + c.split(" ")[0] if conditions else rule  # 예: "(a-2) 매월 말일 전후" → "R3(a-2)"
            if name not in item["conditions"]:
                item["conditions"].append(name)

    for rule, table in [("R1", r1), ("R2(a)", r2a), ("R3", r3)]:
        for row in table.to_dict("records"):
            item = found.setdefault(row["전표번호"], {"rules": [], "reasons": [], "conditions": [],
                                                    "date": row["회계처리일"]})
            if rule not in item["rules"]:
                item["rules"].append(rule)
            note_conditions(item, rule, row["해당 조건"].split(", ") if rule == "R3" else None)
            item["reasons"].append(f"[{rule}] {row['탐지 이유']}")
            target = (f"라인 {row['파일 행 번호']}행 · {row['계정코드']} {row['계정과목명']} {row['방향']}"
                      if rule != "R3" else f"전표 · {row['해당 조건']}")
            reason_rows.append({"전표번호": row["전표번호"], "규칙": rule, "대상": target, "탐지 이유": row["탐지 이유"]})
    for e in extra:
        item = found.setdefault(e["전표번호"], {"rules": [], "reasons": [], "conditions": [],
                                              "date": e["회계처리일"]})
        if e["규칙"] not in item["rules"]:
            item["rules"].append(e["규칙"])
        note_conditions(item, e["규칙"], e.get("조건"))
        item["reasons"].append(f"[{e['규칙']}] {e['탐지 이유']}")
        reason_rows.append({k: e[k] for k in REASON_COLUMNS})
    part = df[df["je_id"].isin(found)] if found else df.iloc[0:0]
    totals = part.groupby("je_id")["debit"].sum().to_dict()
    accounts = {je: ", ".join(dict.fromkeys(f"{c} {n}".strip() for c, n in zip(g["account_code"], g["account_name"])))
                for je, g in part.groupby("je_id")}

    def group(v):
        # 해당 세부 조건이 R4(a) 하나뿐이면 보조 목록, 그 밖에는 우선 검토 목록입니다.
        return SUPPORT_GROUP if v["conditions"] == [R4_MANUAL_ID] else PRIORITY_GROUP

    rows = [{"전표번호": je, "판정": SUPPORT_LABEL if group(v) == SUPPORT_GROUP else REVIEW_LABEL, "검토 구분": group(v), "해당 규칙 수": len(v["rules"]),
             "해당 규칙": ", ".join(v["rules"]), "해당 세부 조건 수": len(v["conditions"]),
             "해당 세부 조건": ", ".join(v["conditions"]),
             "회계처리일": v["date"], "포함 계정": accounts.get(je, ""), "전표 금액": totals.get(je, 0.0),
             "탐지 이유": " ".join(v["reasons"])}
            for je, v in found.items()]
    table = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    # 정렬: 우선 검토 목록 먼저 → 해당 규칙 수 → 해당 세부 조건 수(많은 순) → 회계처리일. 점수·가중치는 만들지 않습니다.
    table["_support"] = table["검토 구분"].eq(SUPPORT_GROUP)
    table = table.sort_values(["_support", "해당 규칙 수", "해당 세부 조건 수", "회계처리일", "전표번호"],
                              ascending=[True, False, False, True, True]).drop(columns="_support").reset_index(drop=True)
    return table, pd.DataFrame(reason_rows, columns=REASON_COLUMNS)


def run_detection(df, settings=None):
    s = settings or DetectionSettings()
    off = set(s.disabled_rules())

    def data(rule):
        # 꺼진 규칙은 빈 표로 계산해 결과 구조(열)는 그대로 두고 탐지 결과만 비웁니다.
        return df.iloc[0:0] if rule in off else df

    r1, r1_skipped, r1_excluded = r1_amount_outliers(data("R1"), s)
    r2a, r2a_status, r2a_skipped = r2_vendor_history(data("R2(a)"), s)
    r2b = r2_monthly_pattern(data("R2(b)"), s)
    r3, r3_notes, r3_counts = r3_timing(data("R3"), s)
    r4, r4_notes, r4_counts = r4_manual_entries(data("R4"), s)
    r5, r5_notes, r5_candidates = r5_new_vendors(data("R5"), s)
    r6, r6_notes = r6_duplicates(data("R6"), s)
    for rule, notes in [("R3", r3_notes), ("R4", r4_notes), ("R5", r5_notes), ("R6", r6_notes)]:
        if rule in off:
            notes[:] = [OFF_NOTE]
    summary, reasons = summarize(df, r1, r2a, r3, _extra_reasons(df, r4, r5, r6))
    # R4 표에서도 종합의 보조 목록 전표는 같은 판정 문구를 씁니다(탐지 대상과 건수는 그대로).
    support = set(summary.loc[summary["검토 구분"] == SUPPORT_GROUP, "전표번호"])
    if support:
        r4 = r4.copy()
        r4.loc[r4["전표번호"].isin(support), "판정"] = SUPPORT_LABEL
    return DetectionResult(s, r1, r1_skipped, r1_excluded, r2a, r2a_status, r2a_skipped, r2b, r3, r3_notes,
                           r3_counts, summary, reasons, r4, r4_notes, r4_counts, r5, r5_notes, r5_candidates, r6, r6_notes)


def _extra_reasons(df, r4, r5, r6):
    """R4~R6 결과를 전표별 사유 목록으로 바꿉니다(종합표용)."""
    dates = df.groupby("je_id")["posting_date"].min().to_dict() if len(df) else {}
    out = []
    for row in r4.to_dict("records"):
        out.append({"전표번호": row["전표번호"], "회계처리일": row["회계처리일"], "규칙": "R4",
                    "대상": f"전표 · {row['해당 조건']}", "탐지 이유": row["탐지 이유"],
                    "조건": row["해당 조건"].split(", ")})
    for row in r5.to_dict("records"):
        target = f"{row['구분']} · {row['거래처']}" + (f" · {row['계정']}" if row["계정"] else "")
        for je in row["전표번호"].split(", "):
            out.append({"전표번호": je, "회계처리일": dates.get(je), "규칙": "R5", "대상": target,
                        "탐지 이유": row["탐지 이유"]})
    for row in r6.to_dict("records"):
        for me, other in [("전표번호 A", "전표번호 B"), ("전표번호 B", "전표번호 A")]:
            out.append({"전표번호": row[me], "회계처리일": dates.get(row[me]), "규칙": "R6",
                        "대상": f"짝 전표 {row[other]} · {row['일치 라인']}", "탐지 이유": row["탐지 이유"]})
    return out


def period_vouchers(df, summary, account_code, ym):
    """R2(b) 이상 기간(계정-월)에 속한 전표 목록. 각 전표가 종합의 어느 목록에 있는지(없으면 '해당 없음') 함께 적습니다.

    R2(b) 판정을 전표로 옮기지 않습니다. 이 기간의 전표를 찾아가기 위한 목록이며, 순서는 우선 검토 → 보조 → 해당 없음,
    그 안에서 회계처리일 순입니다.
    """
    if len(df) == 0:
        return pd.DataFrame(columns=PERIOD_VOUCHER_COLUMNS)
    lines = _lines(df)
    mask = (lines["account_code"] == account_code) & (lines["posting_date"].dt.strftime("%Y-%m") == ym)
    part = lines[mask]
    if part.empty:
        return pd.DataFrame(columns=PERIOD_VOUCHER_COLUMNS)
    ids = part["je_id"].unique()
    info = _voucher_frame(df[df["je_id"].isin(ids)])
    found = summary.set_index("전표번호")
    rows = []
    for je, g in part.groupby("je_id", sort=False):
        hit = je in found.index
        rows.append({"전표번호": je, "회계처리일": g["posting_date"].min(), "이 계정 라인 수": len(g),
                     "이 계정 금액": float(g["amount"].sum()), "포함 계정": info[je]["포함 계정"],
                     "전표 금액": info[je]["전표 금액"],
                     "종합 검토 구분": found.loc[je, "검토 구분"] if hit else NO_GROUP,
                     "판정": found.loc[je, "판정"] if hit else "", "해당 규칙": found.loc[je, "해당 규칙"] if hit else ""})
    order = {PRIORITY_GROUP: 0, SUPPORT_GROUP: 1, NO_GROUP: 2}
    table = pd.DataFrame(rows, columns=PERIOD_VOUCHER_COLUMNS)
    table["_o"] = table["종합 검토 구분"].map(order)
    return table.sort_values(["_o", "회계처리일", "전표번호"]).drop(columns="_o").reset_index(drop=True)
