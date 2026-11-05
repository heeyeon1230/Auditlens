"""범용화 A: 결산월 기준 회계연도 테스트 (가상 자료만 사용).

회계연도는 끝나는 해의 이름입니다. 3월 결산이면 2025년 4월 ~ 2026년 3월이 2026 회계연도입니다.
"""

import pandas as pd
import pytest

from auditlens import analysis as A


def make_clean(months, amount=lambda n: 100 + n):
    """months의 매월 15일에 2라인짜리 전표 하나씩(차변 101 현금 / 대변 401 매출)."""
    rows = []
    for n, ym in enumerate(months):
        day = pd.Timestamp(f"{ym}-15")
        rows += [(f"J{n}", day, "101", "현금", amount(n), 0.0), (f"J{n}", day, "401", "매출", 0.0, amount(n))]
    return pd.DataFrame([{"file_row": i + 2, "je_id": r[0], "posting_date": r[1], "account_code": r[2],
                          "account_name": r[3], "debit": float(r[4]), "credit": float(r[5])}
                         for i, r in enumerate(rows)])


TWO_YEARS_APR = [str(p) for p in pd.period_range("2024-04", "2026-03", freq="M")]  # 3월 결산 2개 회계연도
TWO_YEARS_JAN = [str(p) for p in pd.period_range("2024-01", "2025-12", freq="M")]  # 12월 결산 2개 회계연도


def test_회계연도_계산_규칙():
    assert A.fiscal_month_order(12) == list(range(1, 13))
    assert A.fiscal_month_order(3) == [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    assert A.fiscal_month_order(6)[0] == 7 and A.fiscal_month_order(9)[0] == 10
    assert A.fiscal_year_of(2025, 4, 3) == 2026 and A.fiscal_year_of(2026, 3, 3) == 2026
    assert A.fiscal_year_of(2025, 12, 12) == 2025
    assert A.fiscal_year_range(2026, 3) == ("2025-04", "2026-03")
    assert A.fiscal_year_range(2025, 12) == ("2025-01", "2025-12")
    with pytest.raises(ValueError):
        A.prepare(make_clean(["2025-01"]), 13)


def test_3월_결산은_4월부터_다음해_3월까지():
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=3)
    assert A.fiscal_years(df) == [2025, 2026]
    assert A.default_comparison_years(df) == (2025, 2026)
    by_month = df.drop_duplicates("je_id").set_index("year_month")
    assert by_month.loc["2025-03", "fiscal_year"] == 2025 and by_month.loc["2025-04", "fiscal_year"] == 2026
    assert by_month.loc["2025-04", "fiscal_month_no"] == 1 and by_month.loc["2026-03", "fiscal_month_no"] == 12
    summary = A.fiscal_year_summary(df).set_index("회계연도")
    assert summary.loc[2026, "회계기간"] == "2025-04 ~ 2026-03"
    assert summary.loc[2026, "자료 시작일"] == pd.Timestamp("2025-04-15")
    assert summary.loc[2026, "전표 수"] == 12 and (summary["비고"] == "").all()


def test_같은_자료를_12월_결산으로_보면_달력_연도():
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=12)
    assert A.fiscal_years(df) == [2024, 2025, 2026]
    summary = A.fiscal_year_summary(df).set_index("회계연도")
    assert summary.loc[2024, "비고"] == "12개월 중 9개월만 자료 있음"
    assert summary.loc[2026, "비고"] == "12개월 중 3개월만 자료 있음"


def test_3월_결산_전기_당기_비교는_같은_회계연도_구간끼리():
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=3)
    table = A.compare_years(df, 2025, 2026, "차변 합계").set_index("월")
    assert list(table.index) == [f"{m}월" for m in [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]] + ["합계"]
    # 2025 회계연도 4월 = 2024-04 (n=0, 100원), 2026 회계연도 4월 = 2025-04 (n=12, 112원)
    assert table.loc["4월", "전기"] == 100 and table.loc["4월", "당기"] == 112
    assert table.loc["3월", "전기"] == 111 and table.loc["3월", "당기"] == 123
    assert table.loc["합계", "전기"] == sum(100 + n for n in range(12))
    assert table.loc["합계", "당기"] == sum(100 + n for n in range(12, 24))
    assert (table["증감"].drop("합계") == 12).all() and not table["비고"].astype(bool).any()


def test_12월_결산_결과는_기존_달력_연도와_같음():
    df, _ = A.prepare(make_clean(TWO_YEARS_JAN))  # 기본값 12월
    assert A.fiscal_years(df) == [2024, 2025]
    assert (df["fiscal_year"] == df["year"]).all()
    table = A.compare_years(df, 2024, 2025, "라인 수").set_index("월")
    assert list(table.index)[:12] == [f"{m}월" for m in range(1, 13)]
    assert (table["전기"].drop("합계") == 2).all()
    explicit = A.compare_years(df, 2024, 2025, "라인 수", closing_month=12)
    pd.testing.assert_frame_equal(table.reset_index(), explicit)


@pytest.mark.parametrize("closing, first, years", [(6, 7, [2024, 2025, 2026]), (9, 10, [2024, 2025, 2026])])
def test_6월_9월_결산(closing, first, years):
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=closing)
    assert A.fiscal_years(df) == years
    table = A.compare_years(df, years[1] - 1, years[1], "전표 수")
    assert table["월"].iloc[0] == f"{first}월" and table["월"].iloc[11] == f"{closing}월"
    monthly = A.monthly_summary(df).set_index("연월")
    assert monthly.loc[f"2025-{closing:02d}", "회계연도"] == 2025
    assert monthly.loc[f"2025-{first:02d}", "회계연도"] == 2026


def test_자료가_없는_달은_비고에_표시():
    months = TWO_YEARS_APR[:-2]  # 2026년 2·3월 자료 없음
    df, _ = A.prepare(make_clean(months), closing_month=3)
    table = A.compare_years(df, 2025, 2026, "차변 합계").set_index("월")
    assert table.loc["2월", "비고"] == "당기 자료 없음" and table.loc["3월", "비고"] == "당기 자료 없음"
    assert table.loc["1월", "비고"] == ""
    assert A.fiscal_year_summary(df).set_index("회계연도").loc[2026, "비고"] == "12개월 중 10개월만 자료 있음"


def test_월별_현황에_회계연도_열():
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=3)
    table = A.monthly_summary(df)
    assert list(table.columns) == ["연월", "연도", "월", "회계연도", "전표 수", "라인 수", "차변 합계", "대변 합계", "비고"]
    assert list(table["연월"])[:2] == ["2024-04", "2024-05"]  # 시간 순서 = 회계연도 첫 달부터
    assert table.set_index("연월").loc["2025-01", "회계연도"] == 2025


def test_결산월은_분석표에_남아_다른_함수의_기본값이_됨():
    df, _ = A.prepare(make_clean(TWO_YEARS_APR), closing_month=3)
    assert df.attrs["closing_month"] == 3
    assert A.compare_years(df, 2025, 2026)["월"].iloc[0] == "4월"
    assert A.compare_years(df, 2025, 2026, closing_month=12)["월"].iloc[0] == "1월"  # 직접 주면 그 값
