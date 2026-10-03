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
