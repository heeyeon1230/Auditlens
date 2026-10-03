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
