"""기초 분석 계산 테스트. 작은 표를 직접 만들어 손으로 계산한 값과 비교합니다."""

import pandas as pd
import pytest

from auditlens import analysis as A
from auditlens.validation import validate_journal

MAPPING = {c: c for c in ["je_id", "posting_date", "account_code", "account_name", "debit", "credit"]}


def make_clean(rows):
    """(전표번호, 날짜, 계정코드, 계정과목명, 차변, 대변) 목록 → 검증 후 표와 같은 모양."""
    df = pd.DataFrame(rows, columns=["je_id", "posting_date", "account_code", "account_name", "debit", "credit"])
    df["posting_date"] = pd.to_datetime(df["posting_date"])
    df["debit"] = df["debit"].astype(float)
    df["credit"] = df["credit"].astype(float)
    df.insert(0, "file_row", range(2, len(df) + 2))
    return df


BASIC = [
    ("J1", "2024-01-10", "131", "원재료", 1000, 0),
    ("J1", "2024-01-10", "251", "외상매입금", 0, 1000),
    ("J2", "2024-03-05", "108", "외상매출금", 5000, 0),
    ("J2", "2024-03-05", "404", "제품매출", 0, 5000),
    ("J3", "2025-01-20", "131", "원재료", 1500, 0),
    ("J3", "2025-01-20", "251", "외상매입금", 0, 1500),
    ("J4", "2025-03-31", "455", "제품매출원가", 3000, 0),
    ("J4", "2025-03-31", "135", "제품", 0, 3000),
]


@pytest.fixture
def basic():
    df, notes = A.prepare(make_clean(BASIC))
    assert notes == []
    return df


# ---------------------------------------------------------------- 준비 단계
def test_빈_표는_빈_결과():
    df, notes = A.prepare(make_clean([]))
    assert len(df) == 0 and notes == []
    ov = A.overview(df)
    assert ov["라인 수"] == 0 and ov["전표 수"] == 0 and ov["총 차변"] == 0
    assert ov["기간 시작"] is None and ov["합계 일치"] is True
    assert len(A.account_summary(df)) == 0
    assert len(A.monthly_summary(df)) == 0
    assert A.fiscal_years(df) == []
    assert A.default_comparison_years(df) == (None, None)
    assert (A.key_account_summary(df)["라인 수"] == 0).all()


def test_None_입력도_빈_결과():
    df, notes = A.prepare(None)
    assert len(df) == 0 and notes == []


def test_날짜_금액_결측은_제외하고_처리내역을_남김():
    clean = make_clean(BASIC)
    clean.loc[0, "posting_date"] = pd.NaT
    clean.loc[2, "debit"] = float("nan")
    df, notes = A.prepare(clean)
    assert len(df) == len(BASIC) - 2
    assert {n["항목"]: n["행 수"] for n in notes} == {"회계처리일": 1, "차변·대변 금액": 1}


def test_날짜가_문자열이면_읽을_수_없는_값을_처리내역에_남김():
    clean = make_clean(BASIC)
    clean["posting_date"] = clean["posting_date"].dt.strftime("%Y-%m-%d").astype(object)
    clean.loc[1, "posting_date"] = "날짜아님"
    df, notes = A.prepare(clean)
    assert len(df) == len(BASIC) - 1
    assert notes[0]["항목"] == "회계처리일" and notes[0]["행 수"] == 1


def test_원본_표를_바꾸지_않음():
    clean = make_clean(BASIC)
    before = clean.copy()
    A.prepare(clean)
    pd.testing.assert_frame_equal(clean, before)


# ---------------------------------------------------------------- 전표 현황
def test_전표_현황_요약(basic):
    ov = A.overview(basic)
    assert ov["라인 수"] == 8
    assert ov["전표 수"] == 4
    assert ov["계정 수"] == 6
    assert ov["총 차변"] == 10500 and ov["총 대변"] == 10500
    assert ov["차이"] == 0 and ov["합계 일치"] is True
    assert ov["기간 시작"] == pd.Timestamp("2024-01-10")
    assert ov["기간 종료"] == pd.Timestamp("2025-03-31")
    assert ov["자료가 있는 월 수"] == 4
    assert ov["불균형 전표 수"] == 0


def test_합계는_일치해도_개별_전표는_불균형일_수_있음():
    # J1은 차변이 100 많고 J2는 대변이 100 많아 전체 합계는 같지만, 두 전표 모두 불균형입니다.
    rows = [
        ("J1", "2025-01-01", "131", "원재료", 1100, 0),
        ("J1", "2025-01-01", "251", "외상매입금", 0, 1000),
        ("J2", "2025-01-02", "108", "외상매출금", 2000, 0),
        ("J2", "2025-01-02", "404", "제품매출", 0, 2100),
    ]
    df, _ = A.prepare(make_clean(rows))
    ov = A.overview(df)
    assert ov["합계 일치"] is True and ov["차이"] == 0
    assert ov["불균형 전표 수"] == 2
    assert ov["불균형 전표 차이 절대값 합계"] == 200
    table = A.unbalanced_vouchers(df)
    assert dict(zip(table["전표번호"], table["차이"])) == {"J1": 100, "J2": -100}


def test_합계_불일치와_허용오차():
    rows = [
        ("J1", "2025-01-01", "131", "원재료", 1001, 0),  # 1원 차이: 허용
        ("J1", "2025-01-01", "251", "외상매입금", 0, 1000),
        ("J2", "2025-01-02", "131", "원재료", 500, 0),  # 대변 라인 없음: 500원 차이
    ]
    df, _ = A.prepare(make_clean(rows))
    ov = A.overview(df)
    assert ov["차이"] == 501 and ov["합계 일치"] is False
    assert list(A.unbalanced_vouchers(df)["전표번호"]) == ["J2"]


# ---------------------------------------------------------------- 계정별
def test_계정별_집계(basic):
    table = A.account_summary(basic).set_index("계정코드")
    raw_material = table.loc["131"]
    assert raw_material["라인 수"] == 2 and raw_material["전표 수"] == 2
    assert raw_material["차변 합계"] == 2500 and raw_material["대변 합계"] == 0
    assert raw_material["순액(차변-대변)"] == 2500
    assert raw_material["라인 금액 평균"] == 1250 and raw_material["라인 금액 중앙값"] == 1250
    assert raw_material["라인 금액 최소"] == 1000 and raw_material["라인 금액 최대"] == 1500
    assert table.loc["251", "대변 합계"] == 2500
    assert table["차변 합계"].sum() == table["대변 합계"].sum() == 10500


def test_계정코드는_숫자_순서로_정렬():
    rows = [("J1", "2025-01-01", c, f"계정{c}", 1, 0) for c in ["1000", "200", "30", "ABC"]]
    df, _ = A.prepare(make_clean(rows))
    assert list(A.account_summary(df)["계정코드"]) == ["30", "200", "1000", "ABC"]


def test_계정과목명이_여러_개면_표시():
    rows = [
        ("J1", "2025-01-01", "131", "원재료", 1, 0),
        ("J2", "2025-01-02", "131", "원재료", 1, 0),
        ("J3", "2025-01-03", "131", "원 재료", 1, 0),
    ]
    df, _ = A.prepare(make_clean(rows))
    assert A.account_summary(df).loc[0, "계정과목명"] == "원재료 외 1개"


@pytest.mark.parametrize("code,name,expected", [
    ("131", "원재료", "원재료"),
    ("133", "재공품", "재공품"),
    ("135", "제품", "제품"),
    ("455", "제품매출원가", "매출원가"),
    ("404", "제품매출", "매출"),
    ("501", "원재료비", "제조원가"),
    ("599", "기타제조경비", "제조원가"),
    ("108", "외상매출금", None),
    ("802", "급여", None),
    ("600", "도급원가", None),
    ("A01", "기타", None),
])
def test_제조업_주요_계정_구분(code, name, expected):
    assert A.key_account_group(code, name) == expected


def test_주요_계정_요약은_모든_구분을_표시(basic):
    table = A.key_account_summary(basic).set_index("구분")
    assert list(table.index) == [g for g, _ in A.KEY_ACCOUNT_GROUPS]
    assert table.loc["원재료", "차변 합계"] == 2500
    assert table.loc["매출", "대변 합계"] == 5000
    assert table.loc["매출원가", "차변 합계"] == 3000
    assert table.loc["제품", "대변 합계"] == 3000
    assert table.loc["재공품", "해당 계정"] == "(해당 계정 없음)"
    assert table.loc["재공품", "라인 수"] == 0
    assert A.accounts_in_group(basic, "원재료") == ["131"]


# ---------------------------------------------------------------- 월별
def test_월별_집계와_거래_없는_달(basic):
    table = A.monthly_summary(basic)
    assert list(table["연월"]) == [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01", "2025-02", "2025-03"]
    jan = table[table["연월"] == "2024-01"].iloc[0]
    assert jan["전표 수"] == 1 and jan["라인 수"] == 2 and jan["차변 합계"] == 1000
    feb = table[table["연월"] == "2024-02"].iloc[0]
    assert feb["라인 수"] == 0 and feb["차변 합계"] == 0 and feb["비고"] == "거래 없음"
    assert table["차변 합계"].sum() == 10500


def test_월별_집계_계정_필터(basic):
    table = A.monthly_summary(basic, ["131"])
    assert table["차변 합계"].sum() == 2500
    assert table["연월"].iloc[0] == "2024-01" and table["연월"].iloc[-1] == "2025-01"


def test_여러_달에_걸친_전표는_각_달에_한_번씩():
    rows = [
        ("J1", "2025-01-31", "131", "원재료", 100, 0),
        ("J1", "2025-02-01", "251", "외상매입금", 0, 100),
    ]
    df, _ = A.prepare(make_clean(rows))
    table = A.monthly_summary(df)
    assert list(table["전표 수"]) == [1, 1]
    assert A.overview(df)["전표 수"] == 1


def test_비교_연도_기본값(basic):
    assert A.fiscal_years(basic) == [2024, 2025]
    assert A.default_comparison_years(basic) == (2024, 2025)


def test_한_해만_있으면_전기_없음():
    df, _ = A.prepare(make_clean(BASIC[4:]))
    assert A.default_comparison_years(df) == (None, 2025)


def test_전기_당기_같은_달_비교(basic):
    table = A.compare_years(basic, 2024, 2025, "차변 합계").set_index("월")
    assert len(table) == 13
    assert table.loc["1월", "전기"] == 1000 and table.loc["1월", "당기"] == 1500
    assert table.loc["1월", "증감"] == 500 and table.loc["1월", "증감률(%)"] == 50.0
    assert table.loc["3월", "전기"] == 5000 and table.loc["3월", "당기"] == 3000
    assert table.loc["3월", "증감률(%)"] == -40.0
    assert table.loc["합계", "전기"] == 6000 and table.loc["합계", "당기"] == 4500


def test_전기_값이_0이면_증감률_없음():
    rows = [("J1", "2024-02-01", "131", "원재료", 100, 0), ("J2", "2025-01-01", "131", "원재료", 100, 0)]
    df, _ = A.prepare(make_clean(rows))
    table = A.compare_years(df, 2024, 2025, "차변 합계").set_index("월")
    assert table.loc["1월", "전기"] == 0 and pd.isna(table.loc["1월", "증감률(%)"])


def test_자료가_없는_달은_비고에_표시(basic):
    # 2025년 자료는 3월까지만 있으므로 4~12월은 '당기 자료 없음'
    table = A.compare_years(basic, 2024, 2025, "전표 수").set_index("월")
    assert table.loc["2월", "비고"] == "전기 자료 없음, 당기 자료 없음"
    assert table.loc["3월", "비고"] == ""
    assert table.loc["12월", "비고"] == "전기 자료 없음, 당기 자료 없음"
    assert table.loc["1월", "전기"] == 1 and table.loc["1월", "당기"] == 1


def test_계정별_전기_당기_비교(basic):
    table = A.compare_years(basic, 2024, 2025, "라인 수", ["131"]).set_index("월")
    assert table.loc["1월", "전기"] == 1 and table.loc["1월", "당기"] == 1
    # 3월에 원재료 거래는 없지만 다른 계정 자료가 있으므로 '자료 없음'이 아니라 0입니다.
    assert table.loc["3월", "당기"] == 0 and table.loc["3월", "비고"] == ""


def test_잘못된_비교_항목은_오류():
    df, _ = A.prepare(make_clean(BASIC))
    with pytest.raises(ValueError):
        A.compare_years(df, 2024, 2025, "평균")


# ---------------------------------------------------------------- 검증 결과와 연결
def test_검증에서_제외된_행은_분석에_들어가지_않음():
    raw = pd.DataFrame({
        "je_id": ["J1", "J1", "J2", "J2"],
        "posting_date": ["2025-01-01", "2025-01-01", "2025-02-30", "2025-02-01"],
        "account_code": ["131", "251", "131", "251"],
        "account_name": ["원재료", "외상매입금", "원재료", "외상매입금"],
        "debit": ["1,000", "", "500", ""],
        "credit": ["", "1000", "", "abc"],
    })
    result = validate_journal(raw, MAPPING)
    df, notes = A.prepare(result.clean)
    assert notes == []  # 검증 단계에서 이미 제외했으므로 분석 단계에서 추가로 뺀 행은 없음
    ov = A.overview(df)
    assert ov["라인 수"] == 2 and ov["총 차변"] == 1000 and ov["총 대변"] == 1000
    assert len(result.excluded_rows) == 2


# ---------------------------------------------------------------- 계정코드·계정과목명 불일치 (PR 2 추가 확인)
def test_계정코드가_빈_행은_검증에서_제외되어_분류되지_않음():
    raw = pd.DataFrame({
        "je_id": ["J1", "J1"], "posting_date": ["2025-01-05", "2025-01-05"],
        "account_code": ["", "251"], "account_name": ["원재료", "외상매입금"],
        "debit": ["100", ""], "credit": ["", "100"],
    })
    result = validate_journal(raw, MAPPING)
    assert any(i["코드"] == "V5" and "계정코드" in i["문제"] for i in result.issues)
    df, _ = A.prepare(result.clean)
    assert list(A.account_summary(df)["계정코드"]) == ["251"]
    assert A.key_account_summary(df).set_index("구분").loc["원재료", "라인 수"] == 0


def test_계정과목명이_계정코드보다_먼저_적용됨():
    rows = [
        ("J1", "2025-01-01", "501", "원재료", 100, 0),  # 제조원가 범위 코드지만 이름이 '원재료'
        ("J2", "2025-01-02", "131", "원재료비", 100, 0),  # 원재료 코드지만 이름이 '원재료비'
        ("J3", "2025-01-03", "533", "", 100, 0),  # 이름이 비어 있으면 코드로만 판단
        ("J4", "2025-01-04", "5100", "원재료비", 100, 0),  # 4자리 코드는 500~599가 아님
    ]
    df, _ = A.prepare(make_clean(rows))
    groups = dict(zip(A.account_summary(df)["계정코드"], A.account_summary(df)["주요 계정 구분"]))
    assert groups == {"131": "", "501": "원재료", "533": "제조원가", "5100": ""}


def test_계정과목명이_여러_개면_대표_이름으로_분류():
    # 같은 횟수면 파일에서 먼저 나온 이름이 대표 이름입니다.
    rows = [("J1", "2025-01-01", "135", "제품", 100, 0), ("J1", "2025-01-01", "135", "완제품", 0, 100)]
    df, _ = A.prepare(make_clean(rows))
    row = A.account_summary(df).iloc[0]
    assert row["계정과목명"] == "제품 외 1개" and row["주요 계정 구분"] == "제품"
    rows = [("J1", "2025-01-01", "135", "완제품", 100, 0), ("J1", "2025-01-01", "135", "제품", 0, 100)]
    df, _ = A.prepare(make_clean(rows))
    row = A.account_summary(df).iloc[0]
    assert row["계정과목명"] == "완제품 외 1개" and row["주요 계정 구분"] == ""


def test_전기_자료가_전혀_없는_연도와_비교():
    df, _ = A.prepare(make_clean(BASIC[4:]))  # 2025년 자료만
    table = A.compare_years(df, 2024, 2025, "차변 합계").set_index("월")
    assert table.loc["1월", "전기"] == 0 and table.loc["1월", "당기"] == 1500
    assert pd.isna(table.loc["1월", "증감률(%)"])
    assert table.loc["1월", "비고"] == "전기 자료 없음"
    assert table.loc["합계", "비고"] == ""
