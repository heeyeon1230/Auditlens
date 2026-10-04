"""데이터 검증(V1~V11) 테스트."""

import pandas as pd
import pytest

from auditlens.schema import suggest_mapping
from auditlens.validation import ERROR, INFO, WARNING, parse_amount, parse_date, validate_journal

COLUMNS = ["전표번호", "회계처리일", "계정코드", "계정과목명", "차변금액", "대변금액"]


def make(rows, columns=COLUMNS):
    return pd.DataFrame(rows, columns=columns, dtype=object)


def ok_rows():
    return [
        ["J1", "2025-01-02", "830", "소모품비", "1000", ""],
        ["J1", "2025-01-02", "103", "보통예금", "", "1000"],
        ["J2", "2025-02-03", "824", "운반비", "500", ""],
        ["J2", "2025-02-03", "103", "보통예금", "", "500"],
    ]


def run(df, mapping=None):
    return validate_journal(df, mapping or suggest_mapping(df.columns))


def codes(result, level):
    return [i["코드"] for i in result.issues if i["등급"] == level]


def issue(result, code, level=WARNING):
    found = [i for i in result.issues if i["코드"] == code and i["등급"] == level]
    assert found, f"{code} {level} 항목이 없습니다: {result.issues}"
    return found[0]


def test_정상_데이터는_오류_경고_없음():
    result = run(make(ok_rows()))
    assert codes(result, ERROR) == [] and codes(result, WARNING) == []
    assert result.summary["분석 대상 행"] == 4
    assert result.summary["전표 수"] == 2
    assert result.clean["debit"].dtype == float
    assert list(result.clean["file_row"]) == [2, 3, 4, 5]


def test_V1_필수_열_누락은_오류():
    df = make([r[:5] for r in ok_rows()], COLUMNS[:5])  # 대변금액 열 없음
    result = run(df)
    assert result.has_errors
    item = issue(result, "V1", ERROR)
    assert "대변금액" in item["문제"] and "열 연결" in item["수정 방법"]
    assert result.clean is None


def test_V1_한_열을_두_항목에_연결하면_오류():
    df = make(ok_rows())
    mapping = suggest_mapping(df.columns)
    mapping["credit"] = "차변금액"
    result = run(df, mapping)
    assert "V1" in codes(result, ERROR)


def test_V2_빈_데이터는_오류():
    result = run(make([]))
    assert "V2" in codes(result, ERROR)


def test_V3_날짜_오류_행번호와_값을_알려주고_제외():
    rows = ok_rows()
    rows[2][1] = "2025-13-01"
    rows[3][1] = "2025-13-01"
    result = run(make(rows))
    item = issue(result, "V3")
    assert item["해당 행 수"] == 2
    assert "4행 '2025-13-01'" in item["행 번호(예시)"]
    assert "YYYY-MM-DD" in item["수정 방법"]
    assert result.excluded_rows == [4, 5]
    assert result.summary["분석 대상 행"] == 2


def test_V3_모든_행이_날짜_오류면_분석_불가():
    rows = [r[:1] + ["날짜아님"] + r[2:] for r in ok_rows()]
    result = run(make(rows))
    assert result.has_errors


@pytest.mark.parametrize("value", ["2025-03-31", "2025/03/31", "2025.03.31", "20250331", "2025-03-31 14:05"])
def test_여러_날짜_표기를_읽음(value):
    assert parse_date(value).date() == pd.Timestamp("2025-03-31").date()


def test_존재하지_않는_날짜는_오류():
    with pytest.raises(ValueError):
        parse_date("2025-02-30")


def test_V4_금액_오류와_표기_정리():
    rows = ok_rows()
    rows[0][4] = "1,000원"  # 숫자로 읽을 수 있음 (정보)
    rows[2][4] = "오백"  # 읽을 수 없음 (경고, 제외)
    result = run(make(rows))
    item = issue(result, "V4")
    assert "4행 '오백'" in item["행 번호(예시)"]
    assert 4 in result.excluded_rows
    assert "V4" in codes(result, INFO)
    assert result.clean.loc[result.clean["file_row"] == 2, "debit"].iloc[0] == 1000


@pytest.mark.parametrize("value, expected", [("1,500,000", 1_500_000), ("(2,000)", -2000), ("", 0), (3000, 3000)])
def test_금액_읽기(value, expected):
    assert parse_amount(value)[0] == expected


def test_V5_필수_값_결측():
    rows = ok_rows()
    rows[1][0] = ""
    rows[2][3] = None
    result = run(make(rows))
    je_blank = [i for i in result.issues if i["코드"] == "V5" and "전표번호" in i["항목"]][0]
    assert je_blank["행 번호(예시)"] == "3행"
    assert 3 in result.excluded_rows
    name_blank = [i for i in result.issues if i["코드"] == "V5" and "계정과목명" in i["항목"]][0]
    assert name_blank["행 번호(예시)"] == "4행"
    assert 4 not in result.excluded_rows  # 계정과목명만 빈 행은 제외하지 않음


def test_V6_차대_동시_기재와_둘_다_0():
    rows = ok_rows()
    rows[0][5] = "1000"  # 차변·대변 동시
    rows.append(["J3", "2025-03-01", "830", "소모품비", "0", ""])
    result = run(make(rows))
    problems = [i["문제"] for i in result.issues if i["코드"] == "V6"]
    assert any("모두 적힌" in p for p in problems)
    assert any("모두 0" in p for p in problems)


def test_V7_음수_금액():
    rows = ok_rows()
    rows[0][4] = "-1000"
    rows[1][5] = "-1000"
    result = run(make(rows))
    assert "V7" in codes(result, WARNING)


def test_V8_전표별_차대_불균형():
    rows = ok_rows()
    rows[3][5] = "400"
    result = run(make(rows))
    item = issue(result, "V8")
    assert "J2(차이 100원)" in item["문제"]


def test_V8_1원_차이는_허용():
    rows = ok_rows()
    rows[3][5] = "501"
    assert "V8" not in codes(run(make(rows)), WARNING)


def test_V9_완전_중복_행():
    rows = ok_rows() + [ok_rows()[2]]
    result = run(make(rows))
    item = issue(result, "V9")
    assert item["행 번호(예시)"] == "6행"
    assert result.summary["분석 대상 행"] == 5  # 중복 행을 지우지 않음


def test_V10_분석_기간_정보():
    item = issue(run(make(ok_rows())), "V10", INFO)
    assert "2025-01-02 ~ 2025-02-03" in item["문제"]


def test_V11_계정코드_하나에_계정명_여러개():
    rows = ok_rows()
    rows[2][3] = "운송비"
    rows.append(["J3", "2025-03-01", "824", "운반비", "10", ""])
    rows.append(["J3", "2025-03-01", "103", "보통예금", "", "10"])
    item = issue(run(make(rows)), "V11")
    assert "824" in item["항목"]


def test_Excel처럼_숫자로_읽힌_계정코드():
    rows = ok_rows()
    rows[0][2] = 830.0
    result = run(make(rows))
    assert result.clean.loc[0, "account_code"] == "830"


def test_원본_데이터는_바뀌지_않음():
    df = make(ok_rows())
    before = df.copy()
    run(df)
    pd.testing.assert_frame_equal(df, before)
