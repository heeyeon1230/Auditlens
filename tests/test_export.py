"""결과 다운로드(F7) 테스트.

화면이 쓰는 탐지 결과(DetectionResult)에서 만든 Excel·CSV 묶음을 다시 읽어, 시트마다 원래 표와 행·열·값이
같은지 확인합니다. 샘플(제조업 가상 데이터) 외에 계정 체계가 다른 작은 가상 분개장으로도 확인합니다.
"""

import io
import math
import zipfile
from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest
from openpyxl import load_workbook

from auditlens.analysis import prepare
from auditlens.detection import PRIORITY_GROUP, SUPPORT_GROUP, SUPPORT_LABEL, DetectionSettings, run_detection
from auditlens.export import (
    KST, NOTE_COLUMN, SHEET_NAMES, SHEETS, build_tables, csv_safe, export_file_name, to_csv_zip_bytes, to_excel_bytes,
)
from auditlens.schema import suggest_mapping
from auditlens.validation import validate_journal

R5_ON = DetectionSettings(r5_threshold=50_000_000)
CREATED = datetime(2026, 11, 5, 12, 30, tzinfo=KST)
SCREEN_TABLES = {"탐지 사유": "reasons", "R1": "r1", "R2(a)": "r2a", "R2(b) 이상 기간": "r2b", "R3": "r3", "R4": "r4",
                 "R5": "r5", "R6": "r6"}


def norm(v):
    """비교용 값: 빈 값은 '', 숫자는 실수, 날짜는 Timestamp, 나머지는 글자."""
    if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 6)
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).round("s")
    if hasattr(v, "item"):  # numpy 값
        return norm(v.item())
    return str(v)


def rows(table):
    return [tuple(norm(v) for v in r) for r in table.itertuples(index=False, name=None)]


def assert_same(original, back):
    assert [str(c) for c in back.columns] == [str(c) for c in original.columns]
    assert rows(back) == rows(original)


def make_export(raw, settings, name="가상데이터.csv"):
    validation = validate_journal(raw, suggest_mapping(raw.columns))
    df, notes = prepare(validation.clean)
    det = run_detection(df, settings)
    tables = build_tables(det, validation, df, name, CREATED, notes)
    return det, df, tables, to_excel_bytes(tables), to_csv_zip_bytes(tables)


def read_excel(data):
    """시트별 표. pandas.read_excel은 '830' 같은 글자를 숫자로 바꾸므로, 저장된 값 그대로 보려고 openpyxl로 읽습니다."""
    book = load_workbook(io.BytesIO(data))
    out = {}
    for ws in book.worksheets:
        values = list(ws.values)
        out[ws.title] = pd.DataFrame(values[1:], columns=values[0], dtype=object)
    return out


@pytest.fixture(scope="module")
def sample():
    from auditlens.sample_data import generate_sample_journal

    raw, key = generate_sample_journal()
    det, df, tables, xlsx, zipped = make_export(raw, R5_ON)
    return {"raw": raw, "key": key, "det": det, "df": df, "tables": tables, "xlsx": xlsx, "zip": zipped,
            "back": read_excel(xlsx)}


def test_첫_시트는_안내이고_시트_순서가_정해져_있음(sample):
    assert list(sample["back"]) == SHEET_NAMES and SHEET_NAMES[0] == "안내"
    names = zipfile.ZipFile(io.BytesIO(sample["zip"])).namelist()
    assert names == [f for _, f, _ in SHEETS] and names[0] == "00_안내.txt"
    assert all(len(n) <= 31 for n in SHEET_NAMES)


def test_안내에_판정의_뜻_한계_생성_정보가_있고_점수_표현은_없음(sample):
    notice = sample["back"]["안내"].set_index("항목")["내용"]
    assert "오류나 부정으로 확정한 것이 아닙니다" in notice["판정의 뜻"]
    assert "정상 거래라는 뜻도 아닙니다" in notice["미탐"]
    assert "미탐" in notice["공통 한계"] and "오탐" in notice["공통 한계"]
    assert all(f"한계: {code}" in " ".join(notice.index) for code in ["R1", "R2(a)", "R2(b)", "R3", "R4", "R5", "R6"])
    assert notice["원본 파일"] == "가상데이터.csv"
    assert notice["파일 내용을 만든 시각"] == "2026-11-05 12:30 (한국 시간)"
    assert notice["우선 검토 전표"] == "137개" and notice["보조 목록 전표"] == "570개"
    assert notice["분석 기간"].startswith("2024-01-01 ~ 2025-12-31")
    assert notice["꺼진 규칙"] == "없음" and notice["기본값과 다른 기준"].startswith("1개")  # R5 기준금액만 입력
    text = zipfile.ZipFile(io.BytesIO(sample["zip"])).read("00_안내.txt").decode("utf-8-sig")
    for book in [text, " ".join(sample["back"]["안내"]["내용"])]:
        assert "위험 점수" not in book and "위험도" not in book
    assert "[판정의 뜻]" in text and "작은따옴표" in text


def test_우선_검토와_보조_목록이_화면과_같음(sample):
    summary, back = sample["det"].summary, sample["back"]
    priority = summary[summary["검토 구분"] == PRIORITY_GROUP]
    support = summary[summary["검토 구분"] == SUPPORT_GROUP]
    assert len(back["우선 검토"]) == 137 and len(back["보조 목록"]) == 570
    assert_same(priority, back["우선 검토"])
    assert_same(support, back["보조 목록"])
    assert (back["보조 목록"]["판정"] == SUPPORT_LABEL).all()
    assert set(back["우선 검토"]["검토 구분"]) == {PRIORITY_GROUP}  # 합쳐도 구분이 남도록 열을 유지
    assert all(isinstance(v, (int, float)) for v in back["우선 검토"]["전표 금액"])  # 금액은 숫자
    assert all(isinstance(v, datetime) for v in back["우선 검토"]["회계처리일"])  # 날짜는 날짜
    assert all(isinstance(v, str) for v in back["R1"]["계정코드"])  # 계정코드는 글자 그대로(앞자리 0 보존)


@pytest.mark.parametrize("sheet", list(SCREEN_TABLES))
def test_규칙별_시트가_결과_표와_같음(sample, sheet):
    assert_same(getattr(sample["det"], SCREEN_TABLES[sheet]), sample["back"][sheet])


def test_적용_기준_시트가_화면_표와_같음(sample):
    assert_same(sample["det"].settings.as_table(), sample["back"]["적용 기준"])
    table = sample["back"]["적용 기준"].set_index("기준")
    assert table.loc["R5 기준금액(원)", "적용값"] == 50_000_000 and table.loc["R5 기준금액(원)", "기본값과 다름"] == "예"


def test_비교_제외_라인과_검토_대상_전표_라인(sample):
    det, df, back = sample["det"], sample["df"], sample["back"]
    excluded = back["비교 제외 라인"]
    counts = excluded["목록"].value_counts()
    assert counts.get("R2(a) 비교하지 않은 라인", 0) == len(det.r2a_skipped)
    assert sum(counts) == len(det.r1_excluded) + len(det.r2a_skipped) + len(det.r5_candidates)
    lines = back["검토 대상 전표 라인"]
    flagged = set(det.summary["전표번호"])
    assert len(lines) == int(df["je_id"].isin(flagged).sum())
    assert list(lines.columns[:3]) == ["검토 구분", "전표번호", "파일 행 번호"]
    assert lines["전표번호"].drop_duplicates().tolist() == det.summary["전표번호"].tolist()  # 종합과 같은 순서
    # 파일 행 번호로 원본 행을 찾으면 같은 전표·금액입니다.
    raw = sample["raw"]
    for r in lines.head(30).itertuples(index=False):
        original = raw.iloc[r[2] - 2]  # 머리글이 1행
        assert original["je_id"] == r[1] and float(original["debit"]) == r.차변금액 and float(original["credit"]) == r.대변금액


def test_검증_결과_시트(sample):
    sheet = sample["back"]["검증 결과"]
    assert set(sheet["목록"]) == {"검증 결과"} and "V10" in set(sheet["코드"])


def test_CSV_묶음의_각_파일이_Excel_시트와_같음(sample):
    zf = zipfile.ZipFile(io.BytesIO(sample["zip"]))
    for name, file_name, _ in SHEETS[1:]:
        csv = pd.read_csv(io.BytesIO(zf.read(file_name)), encoding="utf-8-sig", dtype=str, keep_default_na=False)
        expected = csv_safe(sample["tables"][name])
        assert list(csv.columns) == [str(c) for c in expected.columns], name
        assert len(csv) == len(expected), name
    first = pd.read_csv(io.BytesIO(zf.read("02_우선검토.csv")), encoding="utf-8-sig")
    assert first["전표번호"].tolist() == sample["back"]["우선 검토"]["전표번호"].tolist()
    assert first["전표 금액"].tolist() == sample["back"]["우선 검토"]["전표 금액"].tolist()


def test_정답표의_비정상_패턴이_파일에서_찾아짐(sample):
    """생성 → 검증 → 탐지 → 다운로드 통합 확인 (설계서 7절)."""
    back = sample["back"]
    ids = {r["시나리오"]: r["전표번호 전체"].split(", ") for r in sample["key"].to_dict("records")}
    priority, support = set(back["우선 검토"]["전표번호"]), set(back["보조 목록"]["전표번호"])
    for case in ["A01", "A02", "A04", "A05", "A06", "A07", "A08", "A09"]:
        assert set(ids[case]) <= priority, case
    assert set(ids["A03"]) <= support
    periods = back["R2(b) 이상 기간"]
    assert ((periods["계정코드"].astype(str) == "824") & (periods["연월"] == "2025-06")).any()  # A03은 기간 결과로도 나옴


def test_규칙을_끄면_파일에도_반영됨(sample):
    _, _, _, xlsx, _ = make_export(sample["raw"], replace(R5_ON, r3_enabled=False))
    back = read_excel(xlsx)
    assert len(back["우선 검토"]) == 38 and len(back["보조 목록"]) == 584
    assert list(back["R3"].columns) == [NOTE_COLUMN] and "꺼져 있어" in back["R3"].iloc[0, 0]
    assert back["적용 기준"].set_index("기준").loc["R3 사용", "적용값"] == "꺼짐"
    assert back["안내"].set_index("항목").loc["꺼진 규칙", "내용"] == "R3"
    assert "R3" not in set(back["탐지 사유"]["규칙"])


def test_결과_없음과_검사_안_함을_구분함(sample):
    _, _, _, xlsx, _ = make_export(sample["raw"], DetectionSettings())  # R5 기준금액 미입력
    back = read_excel(xlsx)
    assert "입력하지 않아 검사하지 않았습니다" in back["R5"].iloc[0, 0]
    _, _, _, xlsx, _ = make_export(sample["raw"], replace(R5_ON, r6_days=0))
    r6 = read_excel(xlsx)["R6"]
    assert list(r6.columns) == [NOTE_COLUMN] and "해당하는 결과가 없습니다" in r6.iloc[0, 0]


def test_내보내기가_결과_표와_원본을_바꾸지_않음(sample):
    det = run_detection(sample["df"], R5_ON)
    before = {n: getattr(det, n).copy(deep=True) for n in ["summary", "reasons", "r1", "r3", "r4"]}
    df_before = sample["df"].copy(deep=True)
    from auditlens.validation import validate_journal as v

    build_tables(det, v(sample["raw"], suggest_mapping(sample["raw"].columns)), sample["df"], "a.csv", CREATED)
    for n, t in before.items():
        pd.testing.assert_frame_equal(getattr(det, n), t)
    pd.testing.assert_frame_equal(sample["df"], df_before)


def small_journal(extra_columns=True, description="정상 거래"):
    """계정 체계가 다른 작은 가상 분개장(서비스업 가정, 실제 회사 자료 아님)."""
    rows = []
    for i in range(40):
        day = f"2025-{1 + i % 12:02d}-{10 + i % 15:02d}"
        rows += [{"전표번호": f"S{i}", "회계처리일": day, "계정코드": "6100", "계정과목명": "교육훈련비",
                  "차변금액": 200000 + i * 1000, "대변금액": 0},
                 {"전표번호": f"S{i}", "회계처리일": day, "계정코드": "1010", "계정과목명": "현금",
                  "차변금액": 0, "대변금액": 200000 + i * 1000}]
    rows += [{"전표번호": "BIG", "회계처리일": "2025-06-11", "계정코드": "6100", "계정과목명": "교육훈련비",
              "차변금액": 90000000, "대변금액": 0},
             {"전표번호": "BIG", "회계처리일": "2025-06-11", "계정코드": "1010", "계정과목명": "현금",
              "차변금액": 0, "대변금액": 90000000}]
    raw = pd.DataFrame(rows)
    if extra_columns:
        raw["적요"] = description
        raw["거래처"] = "가상거래처"
    return raw


def test_선택_열이_없는_다른_업종_파일도_만들어짐():
    det, _, _, xlsx, zipped = make_export(small_journal(extra_columns=False), DetectionSettings(), "서비스업.csv")
    back = read_excel(xlsx)
    assert list(back) == SHEET_NAMES
    assert set(back["R1"]["전표번호"]) == {"BIG"}
    lines = back["검토 대상 전표 라인"]
    assert list(lines.columns) == ["검토 구분", "전표번호", "파일 행 번호", "회계처리일", "계정코드", "계정과목명",
                                   "차변금액", "대변금액"]  # 연결하지 않은 선택 열은 나오지 않음
    assert "BIG" in set(lines["전표번호"])
    assert len(zipfile.ZipFile(io.BytesIO(zipped)).namelist()) == len(SHEETS)


def test_수식처럼_보이는_값은_Excel에서는_글자로_CSV에서는_작은따옴표(tmp_path):
    raw = small_journal(description="=1+1")
    raw.loc[raw["전표번호"] == "BIG", "적요"] = "-대체 @메모"
    det, df, tables, xlsx, zipped = make_export(raw, DetectionSettings())
    assert (df["description"] == "=1+1").sum() > 0  # 분석용 값은 그대로
    ws = load_workbook(io.BytesIO(xlsx))["검토 대상 전표 라인"]
    header = [c.value for c in ws[1]]
    col = header.index("적요") + 1
    cells = [ws.cell(r, col) for r in range(2, ws.max_row + 1)]
    assert {c.value for c in cells} == {"-대체 @메모"} and all(c.data_type == "s" for c in cells)
    csv = pd.read_csv(io.BytesIO(zipfile.ZipFile(io.BytesIO(zipped)).read("13_검토대상전표라인.csv")),
                      encoding="utf-8-sig")
    assert set(csv["적요"]) == {"'-대체 @메모"}
    assert (csv["차변금액"] >= 0).all() and csv["차변금액"].dtype.kind in "if"  # 숫자 칸은 그대로
    # 다른 시트(비교 제외 라인)의 '=1+1'도 같은 처리
    ws = load_workbook(io.BytesIO(xlsx))["비교 제외 라인"]
    values = [c.value for row in ws.iter_rows(min_row=2) for c in row if c.value == "=1+1"]
    assert values and all(c.data_type == "s" for row in ws.iter_rows(min_row=2) for c in row if c.value == "=1+1")
    assert csv_safe(pd.DataFrame({"a": ["=1+1", "+1", "보통", None], "b": [-5, 1, 2, 3]}))["a"].fillna("").tolist() == \
        ["'=1+1", "'+1", "보통", ""]


def test_파일_이름():
    assert export_file_name("분개장 2025.csv", CREATED, "xlsx") == "AuditLens_검토결과_분개장_2025_20261105_1230.xlsx"
    assert export_file_name('a/b:c*?"<>|.xlsx', CREATED, "zip").startswith("AuditLens_검토결과_a_b_c_")
    assert export_file_name("", CREATED, "zip") == "AuditLens_검토결과_파일_20261105_1230.zip"
