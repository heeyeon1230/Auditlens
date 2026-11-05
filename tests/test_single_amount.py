"""금액 한 열 + 차대 구분 열 형식 테스트.

이 형식은 검증 단계에서 차변금액·대변금액 두 열로 바뀌므로, 같은 거래를 두 형식으로 올리면 검증·탐지·다운로드 결과가
같아야 합니다. 제조업 가상 샘플을 금액 한 열 형식으로 바꾼 표와, 서비스업을 가정한 작은 가상 표를 씁니다.
"""

import io

import numpy as np
import pandas as pd
import pytest

from auditlens.analysis import prepare
from auditlens.detection import DetectionSettings, run_detection
from auditlens.export import KST, SHEET_NAMES, build_tables, to_csv_zip_bytes, to_excel_bytes
from auditlens.schema import suggest_mapping, suggest_single_amount
from auditlens.validation import ERROR, WARNING, dc_flag_values, validate_journal

COLUMNS = ["전표번호", "회계처리일", "계정코드", "계정과목명", "구분", "금액"]
SIDES = {"차변": "차변", "대변": "대변"}


def make(rows):
    return pd.DataFrame(rows, columns=COLUMNS, dtype=object)


def ok_rows(debit="차변", credit="대변"):
    return [
        ["J1", "2025-01-02", "6100", "교육훈련비", debit, "1,000"],
        ["J1", "2025-01-02", "1010", "현금", credit, "1,000"],
        ["J2", "2025-02-03", "6200", "지급수수료", debit, "500.5"],
        ["J2", "2025-02-03", "1010", "현금", credit, "500.5"],
    ]


def run(raw, sides=SIDES, amount="금액", flag="구분"):
    mapping = suggest_mapping(raw.columns)
    return validate_journal(raw, mapping, {"amount": amount, "dc_flag": flag, "sides": sides})


def issues(result, code, level=WARNING):
    return [i for i in result.issues if i["코드"] == code and i["등급"] == level]


def to_single(journal, debit_value="차변", credit_value="대변"):
    """두 열 형식 분개장을 금액 한 열 + 구분 열 형식으로 바꿉니다(테스트용 가상 파일)."""
    out = journal.drop(columns=["debit", "credit"]).copy()
    out["dc"] = np.where(journal["debit"] != 0, debit_value, credit_value)
    out["amount"] = np.where(journal["debit"] != 0, journal["debit"], journal["credit"])
    return out


# ---------------------------------------------------------------- 변환과 검증
def test_구분값에_따라_차변_대변_두_열로_바뀜():
    result = run(make(ok_rows()))
    assert not result.has_errors and not issues(result, "V12")
    clean = result.clean
    assert list(clean["debit"]) == [1000.0, 0.0, 500.5, 0.0]  # 소수점 그대로
    assert list(clean["credit"]) == [0.0, 1000.0, 0.0, 500.5]
    assert not issues(result, "V8")  # 전표별 차대 균형
    assert "금액" not in clean and "구분" not in clean  # 내부 표는 기존 표준 열 구성 그대로


def test_다른_표기의_구분값도_사용자가_연결하면_됨():
    raw = make(ok_rows("D", "C"))
    assert not run(raw, {"D": "차변", "C": "대변"}).has_errors
    # Excel이 숫자 1·2를 1.0·2.0으로 읽은 경우도 '1'·'2'로 연결
    raw = make(ok_rows(1.0, 2.0))
    assert dc_flag_values(raw, "구분") == [("1", 2), ("2", 2)]
    result = run(raw, {"1": "차변", "2": "대변"})
    assert list(result.clean["debit"]) == [1000.0, 0.0, 500.5, 0.0]


def test_반대로_연결하면_반대로_바뀜_프로그램이_추측하지_않음():
    result = run(make(ok_rows()), {"차변": "대변", "대변": "차변"})
    assert list(result.clean["credit"]) == [1000.0, 0.0, 500.5, 0.0]


@pytest.mark.parametrize("sides", [{}, {"차변": "차변"}, {"차변": "대변", "대변": "대변"}])
def test_차변이나_대변으로_지정한_값이_없으면_오류(sides):
    result = run(make(ok_rows()), sides)
    assert result.has_errors and issues(result, "V12", ERROR) and result.clean is None


def test_금액이나_구분_열을_연결하지_않으면_오류():
    result = run(make(ok_rows()), amount=None)
    assert any(i["항목"] == "금액" for i in issues(result, "V1", ERROR))
    result = run(make(ok_rows()), flag=None)
    assert any(i["항목"] == "차대 구분" for i in issues(result, "V1", ERROR))
    result = run(make(ok_rows()), amount="구분")  # 같은 열을 두 항목에 연결
    assert any("동시에" in i["문제"] for i in issues(result, "V1", ERROR))


def test_구분값이_빈_행은_경고하고_제외_전표는_검증_불가():
    rows = ok_rows()
    rows[1][4] = "  "
    result = run(make(rows))
    item = issues(result, "V12")[0]
    assert "비어 있는" in item["문제"] and item["행 번호(예시)"] == "3행"
    assert result.excluded_rows == [3]
    assert "차대 구분 없음" in result.excluded_detail.set_index("file_row").loc[3, "사유"]
    assert any("검증 불가" in i["항목"] and "J1" in i["문제"] for i in issues(result, "V8"))
    assert not any(i["항목"] == "전표별 차대 균형" for i in issues(result, "V8"))  # 불균형으로 세지 않음


def test_지정하지_않은_구분값의_행은_경고하고_제외():
    rows = ok_rows() + [["J3", "2025-03-01", "6100", "교육훈련비", "X", "700"]]
    result = run(make(rows))
    item = issues(result, "V12")[0]
    assert "'X'(1행)" in item["문제"] and item["행 번호(예시)"] == "6행 'X'"
    assert result.excluded_rows == [6]
    assert "J3" not in set(result.clean["je_id"])


def test_금액을_읽을_수_없으면_경고하고_제외():
    rows = ok_rows()
    rows[2][5] = "오백"
    result = run(make(rows))
    item = issues(result, "V4")[0]
    assert "차변으로 지정한 행" in item["항목"] and "4행 '오백'" in item["행 번호(예시)"]
    assert result.excluded_rows == [4]


def test_금액이_빈칸이나_0이면_두_열_형식과_같이_경고만():
    rows = ok_rows()
    rows[0][5] = ""
    rows[1][5] = "0"
    result = run(make(rows))
    item = [i for i in issues(result, "V6") if "0이거나 빈칸" in i["문제"]][0]
    assert item["해당 행 수"] == 2 and result.excluded_rows == []


def test_음수_금액은_부호를_바꾸지_않고_지정한_쪽에_둠():
    rows = ok_rows()
    rows[0][5] = "-1,000"
    rows[1][5] = "(1,000)"
    result = run(make(rows))
    assert issues(result, "V7")
    assert result.clean.loc[0, "debit"] == -1000 and result.clean.loc[0, "credit"] == 0
    assert result.clean.loc[1, "credit"] == -1000 and result.clean.loc[1, "debit"] == 0


def test_변환_뒤_전표별_차대_불균형을_찾음():
    rows = ok_rows()
    rows[1][5] = "900"
    result = run(make(rows))
    item = [i for i in issues(result, "V8") if i["항목"] == "전표별 차대 균형"][0]
    assert "J1(차이 100원)" in item["문제"]


def test_원본_표는_바뀌지_않음():
    raw = make(ok_rows())
    before = raw.copy(deep=True)
    run(raw)
    pd.testing.assert_frame_equal(raw, before)


def test_열_이름_추측():
    assert suggest_single_amount(["전표번호", "구분", "금액"]) == {"amount": "금액", "dc_flag": None}  # '구분'은 추측 안 함
    assert suggest_single_amount(["차대구분", "금액"]) == {"amount": "금액", "dc_flag": "차대구분"}
    assert suggest_single_amount(["Amount", "D/C"]) == {"amount": "Amount", "dc_flag": "D/C"}


# ---------------------------------------------------------------- 제조업 샘플: 두 형식의 결과가 같음
@pytest.fixture(scope="module")
def both_formats():
    from auditlens.sample_data import generate_sample_journal

    journal, _ = generate_sample_journal()
    two = validate_journal(journal, suggest_mapping(journal.columns))
    single_raw = to_single(journal, "D", "C")
    one = validate_journal(single_raw, suggest_mapping(single_raw.columns),
                           {"amount": "amount", "dc_flag": "dc", "sides": {"D": "차변", "C": "대변"}})
    return two, one


def test_샘플_검증_결과와_내부_표가_같음(both_formats):
    two, one = both_formats
    assert not one.has_errors
    pd.testing.assert_frame_equal(one.clean, two.clean)
    assert one.issues == two.issues and one.summary == two.summary


def test_샘플_탐지와_다운로드_결과가_같음(both_formats):
    two, one = both_formats
    settings = DetectionSettings(r5_threshold=50_000_000)
    df_two, _ = prepare(two.clean)
    df_one, _ = prepare(one.clean)
    det_two, det_one = run_detection(df_two, settings), run_detection(df_one, settings)
    groups = det_one.summary["검토 구분"].value_counts()
    assert groups["우선 검토"] == 137 and groups["보조 (수기·조정 여부만 해당)"] == 570
    for name in ["r1", "r2a", "r2b", "r3", "r4", "r5", "r6", "summary", "reasons"]:
        pd.testing.assert_frame_equal(getattr(det_one, name), getattr(det_two, name))
    created = pd.Timestamp("2026-11-05 12:30", tz=KST).to_pydatetime()
    t_two = build_tables(det_two, two, df_two, "a.csv", created)
    t_one = build_tables(det_one, one, df_one, "a.csv", created)
    assert list(t_one) == SHEET_NAMES
    for name in SHEET_NAMES:
        pd.testing.assert_frame_equal(t_one[name], t_two[name])
    assert to_excel_bytes(t_one) and to_csv_zip_bytes(t_one)


def test_서비스업_가정_한_열_파일로_탐지와_다운로드():
    rows = []
    for i in range(40):
        day = f"2025-{1 + i % 12:02d}-{10 + i % 15:02d}"
        rows += [[f"S{i}", day, "6100", "교육훈련비", "D", str(200000 + i * 1000)],
                 [f"S{i}", day, "1010", "현금", "C", str(200000 + i * 1000)]]
    rows += [["BIG", "2025-06-11", "6100", "교육훈련비", "D", "90,000,000"],
             ["BIG", "2025-06-11", "1010", "현금", "C", "90,000,000"]]
    result = run(make(rows), {"D": "차변", "C": "대변"})
    df, _ = prepare(result.clean)
    det = run_detection(df)
    assert set(det.r1["전표번호"]) == {"BIG"}
    tables = build_tables(det, result, df, "서비스업.csv", pd.Timestamp("2026-11-05", tz=KST).to_pydatetime())
    back = pd.read_excel(io.BytesIO(to_excel_bytes(tables)), sheet_name="검토 대상 전표 라인")
    assert {"차변금액", "대변금액"} <= set(back.columns) and "금액" not in back.columns
