"""Streamlit 화면 자동 테스트 (화면을 띄우지 않고 실행 흐름만 확인)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_샘플_데이터_화면이_오류_없이_실행():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("검증을 통과했습니다" in s.value for s in at.success)
    assert any("가상 데이터" in w.value for w in at.warning)


def test_필수_열_연결을_해제하면_오류_표시():
    at = AppTest.from_file(APP, default_timeout=120).run()
    box = [s for s in at.selectbox if s.label.startswith("대변금액")][0]
    box.select("(선택 안 함)").run()
    assert not at.exception
    assert any("분석을 시작할 수 없습니다" in e.value for e in at.error)


def test_파일_업로드_모드로_전환():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.radio[0].set_value("내 파일 업로드").run()
    assert not at.exception


def test_기초_분석_화면이_표시됨():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any(h.value == "4. 기초 분석" for h in at.header)
    assert [t.label for t in at.tabs] == ["전표 현황", "계정별 분석", "월별 분석·전기 비교"]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["전표 수"] == "4,590" and metrics["분개 라인 수"] == "9,326"
    assert metrics["차이: 차변 - 대변 (원)"] == "0"
    assert any("① 전체 합계" in s.value for s in at.success)
    assert any("② 전표별" in s.value for s in at.success)


def test_전기_비교_항목과_범위_변경():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.selectbox(key="analysis_measure").select("전표 수").run()
    at.selectbox(key="analysis_scope").select("[구분] 재공품").run()
    assert not at.exception
    at.selectbox(key="analysis_account").select("455 제품매출원가").run()
    assert not at.exception


def test_검증_오류가_있으면_분석_대신_안내():
    at = AppTest.from_file(APP, default_timeout=120).run()
    box = [s for s in at.selectbox if s.label.startswith("대변금액")][0]
    box.select("(선택 안 함)").run()
    assert not at.exception
    assert any("기초 분석이 표시됩니다" in i.value for i in at.info)
    assert not at.tabs


def test_분석표_금액_열에_단위_표시():
    at = AppTest.from_file(APP, default_timeout=120).run()
    headers = [list(d.value.columns) for d in at.dataframe]
    assert any("차변 합계 (원)" in h for h in headers)
    assert any("전기 2024년 (원)" in h and "증감 (원)" in h for h in headers)
    at.selectbox(key="analysis_measure").select("전표 수").run()
    headers = [list(d.value.columns) for d in at.dataframe]
    assert any("전기 2024년 (건)" in h for h in headers)


REVIEW_CSV = (
    "전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
    "J1,2025-01-02,131,원재료,1000,\n"
    "J1,2025-01-02,251,외상매입금,,1000\n"
    "J2,2025-01-03,131,원재료,500,\n"
    "J2,2025-01-03,251,외상매입금,,400\n"
    "J3,2025-01-04,131,원재료,700,\n"
    "J3,2025-02-30,251,외상매입금,,700\n"
).encode("utf-8")


def test_불균형과_검증_불가_전표를_나눠_표시():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.radio[0].set_value("내 파일 업로드").run()
    at.file_uploader[0].upload("검토.csv", REVIEW_CSV, "text/csv").run()
    assert not at.exception
    headings = [m.value for m in at.markdown]
    assert any("②-1 차대 불균형 전표 · 분개 오류 의심 - 검토 필요" in h for h in headings)
    assert any("②-2 검증 불가 전표" in h for h in headings)
    assert any("분개 오류로 확정된 것이 아니라" in e.value for e in at.error)
    assert any("제외된 행이 포함된 전표가 1개" in w.value for w in at.warning)

    tables = [d.value for d in at.dataframe]
    unbalanced = next(t for t in tables if "포함 계정" in t.columns)
    assert list(unbalanced["전표번호"]) == ["J2"]
    assert list(unbalanced["판정"]) == ["분개 오류 의심 - 검토 필요"]
    assert list(unbalanced["파일 행 번호"]) == ["4, 5"]
    unverifiable = next(t for t in tables if "제외 사유" in t.columns and "판정" in t.columns)
    assert list(unverifiable["전표번호"]) == ["J3"]
    assert unverifiable["제외 사유"].iloc[0] == "7행 회계처리일 형식 오류('2025-02-30')"
    details = [t for t in tables if "구분" in t.columns]
    assert list(details[0]["파일 행 번호"]) == [4, 5]  # 불균형 전표 J2의 라인
    assert list(details[1]["구분"]) == ["분석 포함", "분석 제외"]  # 검증 불가 전표 J3의 라인
