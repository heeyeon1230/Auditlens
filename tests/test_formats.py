"""범용화 D: 날짜·금액 표기 읽기와 열 이름 자동 인식 테스트 (가상 자료만 사용)."""

import pandas as pd
import pytest

from auditlens.formats import (
    BAD_CURRENCY, BAD_EUROPEAN, BAD_EXPONENT, BAD_GROUPING, BAD_INVALID, BAD_STANDARD, DAY_ORDER_DMY, DAY_ORDER_MDY,
    NOTATION_EUROPEAN, AmbiguousDate, AmountError, read_amount, read_date,
)
from auditlens.schema import suggest_mapping, suggest_single_amount
from auditlens.validation import ERROR, INFO, WARNING, parse_amount, parse_date, validate_journal

T = pd.Timestamp


# ---------------------------------------------------------------- 날짜
@pytest.mark.parametrize("value, expected", [
    ("2025-03-31", T("2025-03-31")),
    ("2025/03/31", T("2025-03-31")),
    ("2025.03.31", T("2025-03-31")),
    ("20250331", T("2025-03-31")),
    ("2025-3-5", T("2025-03-05")),
    ("2025. 3. 5.", T("2025-03-05")),          # 한국 Excel 표시 형식
    ("2025. 3. 5", T("2025-03-05")),
    ("2025.3.5.", T("2025-03-05")),
    ("2025-03-31 14:05", T("2025-03-31 14:05")),
    ("2025/03/31 14:05:30", T("2025-03-31 14:05:30")),
    ("2025. 3. 5. 14:05", T("2025-03-05 14:05")),
    ("2025-03-31T14:05:30", T("2025-03-31 14:05:30")),  # ISO
    ("2025-03-31T14:05", T("2025-03-31 14:05")),
    ("2025-03-31T14:05:30.123", T("2025-03-31 14:05:30.123")),
    ("2025-03-31 14:05:30.5", T("2025-03-31 14:05:30.5")),
])
def test_지원하는_날짜_표기(value, expected):
    assert read_date(value) == (expected, set())


@pytest.mark.parametrize("value", ["2025-03-31T14:05:30Z", "2025-03-31T23:30:00+09:00", "2025-03-31T23:30:00-0500"])
def test_ISO_시간대는_환산하지_않고_적힌_시각_그대로(value):
    stamp, notes = read_date(value)
    assert stamp.date() == T("2025-03-31").date() and stamp.tzinfo is None and notes == {"timezone"}


def test_날짜_셀_값은_그대로():
    assert read_date(T("2025-03-31 10:00"))[0] == T("2025-03-31 10:00")
    assert read_date(pd.Timestamp("2025-03-31").to_pydatetime().date())[0] == T("2025-03-31")
    assert read_date("")[0] is None and read_date(None)[0] is None


@pytest.mark.parametrize("value", ["03/04/2025", "3/4/2025", "31/03/2025", "04.03.2025", "03-04-2025 10:00"])
def test_월_일_순서가_모호한_날짜는_고르기_전엔_읽지_않음(value):
    # 31/03/2025처럼 한쪽으로만 읽히는 값도 프로그램이 순서를 정하지 않습니다.
    with pytest.raises(AmbiguousDate):
        read_date(value)


def test_날짜_순서를_고르면_그_순서로_읽음():
    assert read_date("03/04/2025", DAY_ORDER_MDY)[0] == T("2025-03-04")
    assert read_date("03/04/2025", DAY_ORDER_DMY)[0] == T("2025-04-03")
    assert read_date("03/04/2025 14:05", DAY_ORDER_DMY)[0] == T("2025-04-03 14:05")
    with pytest.raises(ValueError):  # 고른 순서로 읽으면 없는 날짜(13월)
        read_date("31/03/2025", DAY_ORDER_MDY)
    assert read_date("2025-03-04", DAY_ORDER_DMY)[0] == T("2025-03-04")  # 연도가 앞이면 순서 선택과 무관


@pytest.mark.parametrize("value", ["2025-02-30", "25-03-31", "45747", "2025/03-31", "2025-13-01", "2025-03-31 25:00",
                                   "날짜아님", "2025년 3월 31일", "2025-03-31 오후 2:05"])
def test_읽지_않는_날짜(value):
    with pytest.raises(ValueError) as exc:
        read_date(value)
    assert not isinstance(exc.value, AmbiguousDate)


# ---------------------------------------------------------------- 금액
@pytest.mark.parametrize("value, expected", [
    ("1,500,000", 1_500_000), ("1500000", 1_500_000), ("1500.5", 1500.5), ("1,500.50", 1500.5), ("0.01", 0.01),
    ("-1,000", -1000), ("(1,000)", -1000), ("+300", 300), ("1,000원", 1000), ("₩1,000", 1000), ("KRW 1,000", 1000),
    ("1 000", 1000), (3000, 3000), (1234.5, 1234.5), ("", 0), (None, 0),
])
def test_기본_표기_금액(value, expected):
    assert read_amount(value)[0] == expected


@pytest.mark.parametrize("value, expected", [("1,000-", -1000), ("△1,000", -1000), ("▲500", -500)])
def test_뒤_마이너스와_삼각형은_음수(value, expected):
    number, notes = read_amount(value)
    assert number == expected and "sign_notation" in notes


@pytest.mark.parametrize("value, reason", [
    ("1.000,50", BAD_EUROPEAN), ("1.234.567", BAD_EUROPEAN), ("1,5", BAD_EUROPEAN),
    ("1,50,000", BAD_GROUPING), ("12,34", BAD_EUROPEAN), ("1,0000", BAD_EUROPEAN),
    ("$1,000", BAD_CURRENCY), ("USD 1,000", BAD_CURRENCY), ("€1.000", BAD_CURRENCY), ("1,000 EUR", BAD_CURRENCY),
    ("1e5", BAD_EXPONENT), ("1.5E+06", BAD_EXPONENT),
    ("오백", BAD_INVALID), ("-", BAD_INVALID), ("--5", BAD_INVALID), ("(-5)", BAD_INVALID), ("nan", BAD_INVALID),
    (True, BAD_INVALID), (float("inf"), BAD_INVALID),
])
def test_기본_표기에서_읽지_않는_금액(value, reason):
    with pytest.raises(AmountError) as exc:
        read_amount(value)
    assert exc.value.reason == reason


def test_유럽식_표기를_고르면_읽음():
    assert read_amount("1.000,50", NOTATION_EUROPEAN) == (1000.5, {"tidied"})
    assert read_amount("1.234.567", NOTATION_EUROPEAN)[0] == 1_234_567
    assert read_amount("-1.000,5", NOTATION_EUROPEAN)[0] == -1000.5
    assert read_amount("1500,25", NOTATION_EUROPEAN)[0] == 1500.25
    assert read_amount("(1.000)", NOTATION_EUROPEAN)[0] == -1000
    for value, reason in [("1,000.50", BAD_STANDARD), ("1,234,567", BAD_STANDARD), ("1.00.0", BAD_GROUPING)]:
        with pytest.raises(AmountError) as exc:
            read_amount(value, NOTATION_EUROPEAN)
        assert exc.value.reason == reason


def test_표기에_따라_1000배_달라지는_값은_확인_메모():
    assert read_amount("1.000") == (1.0, {"check_separator"})
    assert read_amount("1,000", NOTATION_EUROPEAN)[0] == 1.0
    assert "check_separator" in read_amount("1,000", NOTATION_EUROPEAN)[1]
    assert "check_separator" not in read_amount("1,000")[1] and "check_separator" not in read_amount("1.5")[1]


def test_기존_함수는_이전과_같은_값():
    # PR #7까지 쓰던 parse_amount·parse_date의 반환 형태와 기존 표기 결과
    assert parse_amount("1,000원") == (1000.0, True)
    assert parse_amount("(2,000)") == (-2000.0, True)
    assert parse_amount("1500") == (1500.0, False) and parse_amount("-1500") == (-1500.0, False)
    assert parse_amount("") == (0.0, False)
    assert parse_date("2025-03-31 14:05") == T("2025-03-31 14:05")


# ---------------------------------------------------------------- 검증 단계에서의 처리
COLUMNS = ["전표번호", "회계처리일", "계정코드", "계정과목명", "차변금액", "대변금액"]


def journal(rows):
    return pd.DataFrame(rows, columns=COLUMNS, dtype=object)


def validate(raw, **formats):
    return validate_journal(raw, suggest_mapping(raw.columns), None, formats or None)


def issues(result, code, level=None):
    return [i for i in result.issues if i["코드"] == code and (level is None or i["등급"] == level)]


def test_모호한_날짜가_있으면_순서를_고를_때까지_오류():
    raw = journal([["J1", "03/04/2025", "101", "현금", "100", ""], ["J1", "2025-03-04", "201", "매출", "", "100"]])
    result = validate(raw)
    assert result.has_errors and result.clean is None
    item = issues(result, "V3", ERROR)[0]
    assert "1개 행" in item["문제"] and "날짜 순서" in item["수정 방법"]
    result = validate(raw, day_order=DAY_ORDER_MDY)
    assert not result.has_errors and list(result.clean["posting_date"]) == [T("2025-03-04")] * 2


def test_입력일시가_모호해도_순서를_골라야_함():
    raw = journal([["J1", "2025-03-04", "101", "현금", "100", ""], ["J1", "2025-03-04", "201", "매출", "", "100"]])
    raw["입력일시"] = ["05/03/2025 10:00", "2025-03-05 10:00"]
    assert issues(validate(raw), "V3", ERROR)
    result = validate(raw, day_order=DAY_ORDER_DMY)
    assert list(result.clean["entry_date"]) == [T("2025-03-05 10:00")] * 2


def test_시간대가_있는_날짜는_정보로_알림():
    raw = journal([["J1", "2025-03-04T09:00:00Z", "101", "현금", "100", ""],
                   ["J1", "2025-03-04", "201", "매출", "", "100"]])
    result = validate(raw)
    assert not result.has_errors
    assert "환산하지 않았습니다" in issues(result, "V3", INFO)[0]["문제"]


def test_유럽식으로_보이는_금액은_조용히_읽지_않고_경고_후_제외():
    raw = journal([["J1", "2025-01-02", "101", "현금", "1.000,50", ""], ["J1", "2025-01-02", "201", "매출", "", "1.000,50"],
                   ["J2", "2025-01-03", "101", "현금", "500", ""], ["J2", "2025-01-03", "201", "매출", "", "500"]])
    result = validate(raw)
    warnings = issues(result, "V4", WARNING)
    assert any("유럽식 표기" in i["문제"] and "'숫자 표기'" in i["수정 방법"] for i in warnings)
    assert result.excluded_rows == [2, 3]
    assert "유럽식 표기로 보임" in result.excluded_detail["사유"].iloc[0]
    assert 1.0005 not in set(result.clean["debit"])  # 예전에는 1.0005로 읽었음
    result = validate(raw, notation=NOTATION_EUROPEAN)
    assert result.excluded_rows == [] and list(result.clean["debit"]) == [1000.5, 0, 500, 0]


def test_읽지_못한_이유별로_경고를_나눔():
    raw = journal([["J1", "2025-01-02", "101", "현금", "$100", ""], ["J1", "2025-01-02", "201", "매출", "", "1,00"],
                   ["J2", "2025-01-03", "101", "현금", "1e3", ""], ["J2", "2025-01-03", "201", "매출", "", "오백"],
                   ["J3", "2025-01-04", "101", "현금", "1,50,000", ""], ["J3", "2025-01-04", "201", "매출", "", "150000"]])
    problems = [i["문제"] for i in issues(validate(raw), "V4", WARNING)]
    assert any("원화가 아닌 통화" in p for p in problems)
    assert any("지수 표기" in p for p in problems)
    assert any("천 단위 구분 기호의 위치가 맞지 않는" in p for p in problems)
    assert any("숫자로 읽을 수 없는" in p for p in problems)
    assert any("유럽식 표기" in p for p in problems)  # '1,00'


def test_1000배_차이날_수_있는_값은_포함하되_경고():
    raw = journal([["J1", "2025-01-02", "101", "현금", "1.000", ""], ["J1", "2025-01-02", "201", "매출", "", "1.000"]])
    result = validate(raw)
    item = [i for i in issues(result, "V4", WARNING) if i["항목"] == "금액 표기 확인"][0]
    assert "1.000 → 1" in item["문제"] and result.excluded_rows == []


def test_음수_표기_정보와_부호_유지():
    raw = journal([["J1", "2025-01-02", "101", "현금", "△1,000", ""], ["J1", "2025-01-02", "201", "매출", "", "1,000-"]])
    result = validate(raw)
    assert issues(result, "V4", INFO) and any(i["항목"] == "음수 표기" for i in issues(result, "V4", INFO))
    assert list(result.clean["debit"]) == [-1000, 0] and list(result.clean["credit"]) == [0, -1000]
    assert issues(result, "V7", WARNING)


def test_금액_한_열_형식에도_같은_규칙():
    raw = pd.DataFrame([["J1", "2025-01-02", "101", "현금", "D", "1.000,50"],
                        ["J1", "2025-01-02", "201", "매출", "C", "1.000,50"]],
                       columns=["전표번호", "회계처리일", "계정코드", "계정과목명", "구분", "금액"], dtype=object)
    single = {"amount": "금액", "dc_flag": "구분", "sides": {"D": "차변", "C": "대변"}}
    result = validate_journal(raw, suggest_mapping(raw.columns), single)
    assert any("유럽식 표기" in i["문제"] for i in issues(result, "V4", WARNING))
    result = validate_journal(raw, suggest_mapping(raw.columns), single, {"notation": NOTATION_EUROPEAN})
    assert list(result.clean["debit"]) == [1000.5, 0] and list(result.clean["credit"]) == [0, 1000.5]


def test_원본_표는_바뀌지_않음():
    raw = journal([["J1", "03/04/2025", "101", "현금", "1.000,50", ""], ["J1", "03/04/2025", "201", "매출", "", "1.000,50"]])
    before = raw.copy(deep=True)
    validate(raw, day_order=DAY_ORDER_MDY, notation=NOTATION_EUROPEAN)
    pd.testing.assert_frame_equal(raw, before)


# ---------------------------------------------------------------- 열 이름
@pytest.mark.parametrize("columns", [
    ["전표번호", "회계처리일", "계정코드", "계정과목명", "차변금액", "대변금액"],          # 기존 한글 이름
    ["전표NO", "전기일자", "계정과목코드", "계정과목", "차변", "대변"],
    ["Document Number", "Posting Date", "GL Account", "Account Description", "Debit", "Credit"],
    ["Voucher No", "Accounting Date", "Account Number", "Account Name", "Debit Amount", "Credit Amount"],
    ["Journal Entry Number", "Post Date", "G/L Account", "G/L Account Name", "Dr Amount", "Cr Amount"],
    ["BELNR", "BUDAT", "HKONT", "Account Title", "Debit", "Credit"],
])
def test_필수_열_자동_인식(columns):
    mapping = suggest_mapping(columns)
    std = ["je_id", "posting_date", "account_code", "account_name", "debit", "credit"]
    assert [mapping[c] for c in std] == columns


def test_선택_열과_한_열_형식_자동_인식():
    mapping = suggest_mapping(["Doc No", "Posting Date", "Account", "Account Name", "Debit", "Credit", "Created On",
                               "Line Description", "Vendor Name", "Journal Source", "Document Type", "Entered By",
                               "Approver Name"])
    assert mapping["entry_date"] == "Created On" and mapping["description"] == "Line Description"
    assert mapping["vendor"] == "Vendor Name" and mapping["source"] == "Journal Source"
    assert mapping["je_type"] == "Document Type" and mapping["created_by"] == "Entered By"
    assert mapping["approved_by"] == "Approver Name"
    assert suggest_single_amount(["DMBTR", "SHKZG"]) == {"amount": "DMBTR", "dc_flag": "SHKZG"}
    assert suggest_single_amount(["Local Amount", "Debit/Credit Indicator"]) == {
        "amount": "Local Amount", "dc_flag": "Debit/Credit Indicator"}


def test_증빙일과_모르는_이름은_추측하지_않음():
    mapping = suggest_mapping(["Document Date", "Ref", "Value"])
    assert set(mapping.values()) == {None}  # 수동 연결로 넘어감


def test_자동_인식이_틀려도_수동_연결로_검증():
    raw = pd.DataFrame([["J1", "2025-01-02", "101", "현금", "100", ""], ["J1", "2025-01-02", "201", "매출", "", "100"]],
                       columns=["A", "B", "C", "D", "E", "F"], dtype=object)
    assert validate_journal(raw, suggest_mapping(raw.columns)).has_errors
    manual = dict(suggest_mapping(raw.columns), je_id="A", posting_date="B", account_code="C", account_name="D",
                  debit="E", credit="F")
    assert not validate_journal(raw, manual).has_errors
