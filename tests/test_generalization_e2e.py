"""범용화 통합·회귀 테스트: 업로드 → 열 연결 → 검증 → 기초 분석 → R1~R6 → 다운로드.

1) 제조업 가상 샘플: 범용화 전(main 030d1c7)의 결과 수와 같은지
2) 업종·결산월·열 이름·금액 형식·날짜 표기가 다른 두 번째 가상 회사(서비스업, 3월 결산)
실제 회사 자료는 쓰지 않습니다.
"""

import io
import zipfile

import openpyxl
import pandas as pd
import pytest

from auditlens.analysis import account_summary, compare_years, fiscal_year_summary, fiscal_years, prepare, top_accounts
from auditlens.detection import DetectionSettings, run_detection
from auditlens.export import KST, SHEET_NAMES, build_tables, to_csv_zip_bytes, to_excel_bytes
from auditlens.loader import read_uploaded_file
from auditlens.sample_data import generate_sample_journal, journal_to_csv_bytes, journal_to_excel_bytes
from auditlens.schema import suggest_mapping, suggest_single_amount
from auditlens.validation import validate_journal

CREATED = pd.Timestamp("2026-11-05 12:30", tz=KST).to_pydatetime()


def pipeline(raw, single=None, formats=None, closing_month=12, **settings):
    result = validate_journal(raw, suggest_mapping(raw.columns), single, formats)
    assert not result.has_errors, result.issues
    df, _ = prepare(result.clean, closing_month)
    det = run_detection(df, DetectionSettings(closing_month=closing_month, **settings))
    tables = build_tables(det, result, df, "file.csv", CREATED)
    return result, df, det, tables


# ---------------------------------------------------------------- 1. 제조업 샘플 회귀
@pytest.fixture(scope="module")
def sample():
    journal, _ = generate_sample_journal()
    return journal, pipeline(journal, r5_threshold=50_000_000)


def test_샘플_검증_탐지_다운로드_결과_수가_범용화_전과_같음(sample):
    _, (result, df, det, tables) = sample
    # 기준값: main 030d1c7에서 같은 설정으로 실행한 결과
    assert [(i["코드"], i["등급"]) for i in result.issues] == [("V10", "정보")]
    assert result.summary["분석 대상 행"] == 9326 and result.excluded_rows == []
    sizes = {name: len(getattr(det, name)) for name in ["r1", "r2a", "r2b", "r3", "r4", "r5", "r6", "summary", "reasons"]}
    assert sizes == {"r1": 3, "r2a": 6, "r2b": 10, "r3": 102, "r4": 622, "r5": 1, "r6": 1, "summary": 707,
                     "reasons": 737}
    groups = det.summary["검토 구분"].value_counts()
    assert groups["우선 검토"] == 137 and groups["보조 (수기·조정 여부만 해당)"] == 570
    assert list(tables) == SHEET_NAMES
    assert {n: len(t) for n, t in tables.items() if n not in ("안내", "적용 기준")} == {
        "우선 검토": 137, "보조 목록": 570, "탐지 사유": 737, "R1": 3, "R2(a)": 6, "R2(b) 이상 기간": 10, "R3": 102,
        "R4": 622, "R5": 1, "R6": 1, "비교 제외 라인": 725, "검토 대상 전표 라인": 1472, "검증 결과": 1}


def test_샘플_12월_결산_기초_분석은_달력_연도(sample):
    _, (_, df, _, _) = sample
    assert fiscal_years(df) == [2024, 2025] and (df["fiscal_year"] == df["year"]).all()
    assert list(compare_years(df, 2024, 2025)["월"])[:12] == [f"{m}월" for m in range(1, 13)]
    assert list(fiscal_year_summary(df)["회계기간"]) == ["2024-01 ~ 2024-12", "2025-01 ~ 2025-12"]


def test_샘플_적요_입력률이_R4_처리_내역에_표시(sample):
    _, (_, _, det, tables) = sample
    assert ("적요 입력률: 라인 기준 99.98% (9,324 / 9,326라인), 전표 기준 99.98% (4,589 / 4,590전표). "
            "적요가 빈 전표 1 / 4,590개.") in det.r4_notes
    assert det.r4_counts.set_index("조건").loc["(d) 적요 없음·키워드", "적용 여부"] == "적용"


def test_샘플_CSV_Excel_업로드도_같은_결과(sample):
    journal, (result, _, det, _) = sample
    for name, data in [("sample.csv", journal_to_csv_bytes(journal)), ("sample.xlsx", journal_to_excel_bytes(journal))]:
        raw, _ = read_uploaded_file(name, data)
        r2 = validate_journal(raw, suggest_mapping(raw.columns))
        assert not r2.has_errors and r2.summary == result.summary
        pd.testing.assert_frame_equal(r2.clean[["je_id", "account_code", "debit", "credit"]],
                                      result.clean[["je_id", "account_code", "debit", "credit"]])


def test_샘플_다운로드_파일이_만들어짐(sample):
    _, (_, _, _, tables) = sample
    book = openpyxl.load_workbook(io.BytesIO(to_excel_bytes(tables)), read_only=True)
    assert book.sheetnames == SHEET_NAMES
    names = zipfile.ZipFile(io.BytesIO(to_csv_zip_bytes(tables))).namelist()
    assert names[0] == "00_안내.txt" and len(names) == len(SHEET_NAMES)  # 안내는 txt, 나머지 시트는 csv


# ---------------------------------------------------------------- 2. 두 번째 가상 회사 (서비스업, 3월 결산)
DATE_STYLES = [lambda d: d.strftime("%Y-%m-%d"), lambda d: f"{d.year}. {d.month}. {d.day}.",
               lambda d: d.strftime("%Y/%m/%d"), lambda d: d.strftime("%Y-%m-%dT09:30:00")]
AMOUNT_STYLES = [lambda a: f"{a:,}", lambda a: f"₩{a:,}", lambda a: f"{a}원", lambda a: str(a)]


def service_rows():
    """2024-04 ~ 2026-03, 매달 20개 전표(용역매출, 급여, 임차료). 6자리 계정코드. 전표마다 날짜·금액 표기를 바꿈."""
    rows = []
    months = pd.period_range("2024-04", "2026-03", freq="M")
    n = 0
    for ym in months:
        for k in range(20):
            day = pd.Timestamp(year=ym.year, month=ym.month, day=1 + k)
            amount = 1_000_000 + (n % 37) * 10_000
            debit, credit = [("103000", "보통예금"), ("511000", "급여"), ("519000", "지급임차료")][k % 3], ("411000", "용역매출")
            if k % 3:
                debit, credit = debit, ("103000", "보통예금")
            rows.append((f"SV{n:05d}", day, debit, credit, amount, "" if k == 19 else f"정기 거래 {k}"))
            n += 1
    rows.append(("SVBIG", pd.Timestamp("2025-11-12"), ("519000", "지급임차료"), ("103000", "보통예금"), 95_000_000, ""))
    return rows


def service_single_csv(rows):
    """영문 열 이름 + 금액 한 열(Amount) + 차대 구분(D/C). 날짜·금액 표기를 섞음."""
    out = ["Document Number,Posting Date,GL Account,Account Description,D/C,Amount,Text,Vendor Name"]
    for i, (je, day, dr, cr, amount, text) in enumerate(rows):
        d, a = DATE_STYLES[i % 4](day), AMOUNT_STYLES[i % 4](amount)
        for (code, name), flag in ((dr, "D"), (cr, "C")):
            out.append(f'{je},{d},{code},{name},{flag},"{a}",{text},가상거래처{i % 7}')
    return "\n".join(out).encode("utf-8")


def service_two_column(rows):
    """같은 거래를 두 열 형식(한글 열 이름, 쉼표 없는 숫자)으로. 날짜 표기는 한 열 파일과 같게 섞음."""
    data = []
    for i, (je, day, dr, cr, amount, text) in enumerate(rows):
        for (code, name), side in ((dr, "D"), (cr, "C")):
            data.append({"전표번호": je, "회계처리일": DATE_STYLES[i % 4](day), "계정코드": code, "계정과목명": name,
                         "차변금액": str(amount) if side == "D" else "", "대변금액": str(amount) if side == "C" else "",
                         "적요": text, "거래처": f"가상거래처{i % 7}"})
    return pd.DataFrame(data, dtype=object)


@pytest.fixture(scope="module")
def service():
    rows = service_rows()
    raw, _ = read_uploaded_file("service.csv", service_single_csv(rows))
    guess = suggest_single_amount(raw.columns, [v for v in suggest_mapping(raw.columns).values() if v])
    assert guess == {"amount": "Amount", "dc_flag": "D/C"}  # 영문 이름 자동 인식
    single = {**guess, "sides": {"D": "차변", "C": "대변"}}
    return raw, pipeline(raw, single, closing_month=3, closing_day=31, r5_threshold=50_000_000), rows


def test_서비스업_파일_열_인식과_표기_변환(service):
    raw, (result, df, _, _), rows = service
    mapping = suggest_mapping(raw.columns)
    assert mapping["je_id"] == "Document Number" and mapping["posting_date"] == "Posting Date"
    assert mapping["account_code"] == "GL Account" and mapping["account_name"] == "Account Description"
    assert mapping["description"] == "Text" and mapping["vendor"] == "Vendor Name"
    assert result.excluded_rows == [] and len(result.clean) == 2 * len(rows)
    assert df["posting_date"].min() == pd.Timestamp("2024-04-01")
    assert df["posting_date"].dt.hour.max() == 9  # ISO 표기 행의 시각
    assert set(df["account_code"]) == {"103000", "411000", "511000", "519000"}  # 6자리 코드 그대로


def test_서비스업_3월_결산_기초_분석(service):
    _, (_, df, _, _), _ = service
    assert fiscal_years(df) == [2025, 2026]
    summary = fiscal_year_summary(df).set_index("회계연도")
    assert summary.loc[2026, "회계기간"] == "2025-04 ~ 2026-03" and summary.loc[2026, "자료가 있는 월 수"] == 12
    table = compare_years(df, 2025, 2026, "전표 수").set_index("월")
    assert table.index[0] == "4월" and table.index[11] == "3월"
    assert table.loc["4월", "전기"] == 20 and table.loc["11월", "당기"] == 21  # SVBIG
    assert "주요 계정 구분" not in account_summary(df)
    assert top_accounts(df, "라인 금액 합계", 1)["계정코드"].iloc[0] == "103000"


def test_서비스업_R1_R6_실행과_다운로드(service):
    _, (_, df, det, tables), _ = service
    assert "SVBIG" in set(det.r1["전표번호"])
    assert set(det.summary["전표번호"]) >= {"SVBIG"}
    # 적요가 일부(전표 19번째 라인마다)만 비어 있으므로 R4(d)는 기존 규칙대로 적용
    counts = det.r4_counts.set_index("조건")
    assert counts.loc["(d) 적요 없음·키워드", "적용 여부"] == "적용"
    assert counts.loc["(d) 적요 없음·키워드", "해당 전표 수"] == 25  # 월 1개 × 24개월 + SVBIG
    assert list(tables) == SHEET_NAMES
    book = openpyxl.load_workbook(io.BytesIO(to_excel_bytes(tables)), read_only=True)
    header = [c.value for c in next(book["검토 대상 전표 라인"].iter_rows(max_row=1))]
    assert "차변금액" in header and "대변금액" in header and "Amount" not in header
    assert to_csv_zip_bytes(tables)


def test_두_금액_형식_결과가_같음(service):
    _, (one, df_one, det_one, _), rows = service
    two_raw = service_two_column(rows)
    two, df_two, det_two, _ = pipeline(two_raw, closing_month=3, closing_day=31, r5_threshold=50_000_000)
    cols = ["je_id", "account_code", "debit", "credit"]
    pd.testing.assert_frame_equal(one.clean[cols], two.clean[cols])
    for name in ["r1", "r2a", "r2b", "r4", "r5", "r6"]:
        pd.testing.assert_frame_equal(getattr(det_one, name), getattr(det_two, name))
    # R3는 입력일시가 없고 회계처리일만 보므로 ISO 시각 행이 있어도 같아야 함
    assert list(det_one.r3["전표번호"]) == list(det_two.r3["전표번호"])


def test_적요가_모두_빈_회사는_R4d_평가_불가(service):
    _, _, rows = service
    raw = service_two_column(rows).assign(적요="")
    _, _, det, tables = pipeline(raw, closing_month=3)
    counts = det.r4_counts.set_index("조건")
    assert counts.loc["(d) 적요 없음·키워드", "적용 여부"] == "평가 불가 (적요 열의 값이 모두 비어 있음)"
    assert det.r4.empty  # 원천·유형·입력자 열도 없으므로 R4 전체 0개 (예전에는 전표 전부)
    notice = tables["안내"].astype(str)
    assert notice.apply(lambda col: col.str.contains("평가 불가 (적요 열의 값이 모두 비어 있음)", regex=False)).any().any()
    assert notice.apply(lambda col: col.str.contains("라인 기준 0.00% (0 /", regex=False)).any().any()
