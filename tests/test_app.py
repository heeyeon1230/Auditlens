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
    assert [t.label for t in at.tabs][:3] == ["전표 현황", "계정별 분석", "월별 분석·전기 비교"]  # 뒤는 5단계 탭
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


def test_검토_대상_거래_화면이_표시됨():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    assert any(h.value == "5. 검토 대상 거래 (R1~R6)" for h in at.header)
    assert [t.label for t in at.tabs][3:] == ["종합 (전표별)", "R1 금액 분포", "R2(a) 거래처 이전 거래", "R3 시점",
                                        "R4 수기·조정", "R5 신규 거래처", "R6 중복 가능"]
    notice = " ".join(w.value for w in at.warning)
    assert "오류나 부정으로 확정한 것이 아닙니다" in notice and "탐지되지 않은 거래가 정상 거래라는 뜻도 아닙니다" in notice
    subheaders = [h.value for h in at.subheader]
    assert "5-1. 개별 거래 탐지 결과 (R1 · R2(a) · R3 · R4 · R5 · R6)" in subheaders
    assert "5-2. 이상 기간 탐지 결과 (R2(b) · 계정-월)" in subheaders
    assert any("개별 거래가 아니라 계정과 월의 조합" in i.value for i in at.info)
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["R1 라인"]) > 0 and int(metrics["R3 전표"].replace(",", "")) > 0
    assert int(metrics["검토 대상 기간 (계정-월)"]) > 0
    periods = [d.value for d in at.dataframe if "연월" in d.value.columns and "판정" in d.value.columns][0]
    assert (periods["판정"] == "검토 대상 기간 (계정-월)").all()
    summary = at.dataframe[[i for i, d in enumerate(at.dataframe) if "해당 규칙 수" in d.value.columns][0]].value
    assert (summary["판정"] == "검토 대상 거래").all()
    assert summary["해당 규칙 수"].is_monotonic_decreasing
    assert (summary["검토 구분"] == "우선 검토").all()  # 첫 종합표는 우선 검토 목록
    assert any(e.label.startswith("보조 목록: 수기·조정 여부(R4(a))만 해당하는 전표") for e in at.expander)
    assert int(metrics["우선 검토 전표"].replace(",", "")) == len(summary)
    support = [d.value for d in at.dataframe if "검토 구분" in d.value.columns
               and (d.value["검토 구분"] == "보조 (수기·조정 여부만 해당)").all() and len(d.value)][0]
    assert (support["판정"] == "수기·조정 전표(보조 정보)").all()
    at.selectbox(key="det_r2b_pick").set_value("824 운반비 · 2025-06").run()
    assert not at.exception
    period = [d.value for d in at.dataframe if "종합 검토 구분" in d.value.columns][0]
    assert len(period) >= 36 and set(period["종합 검토 구분"]) <= {"우선 검토", "보조 (수기·조정 여부만 해당)", "해당 없음"}


def test_탐지_기준을_바꾸면_다시_계산됨():
    at = AppTest.from_file(APP, default_timeout=180).run()
    before = int({m.label: m.value for m in at.metric}["R3 전표"].replace(",", ""))
    counts = [d.value for d in at.dataframe if "탐지 결과 포함" in d.value.columns][0].set_index("조건")
    extra = counts.loc["(a-2) 매월 말일 전후", "해당 전표 수"]
    assert extra > 0 and "꺼짐" in counts.loc["(a-2) 매월 말일 전후", "탐지 결과 포함"]
    at.checkbox(key="det_r3_month_end").check().run()
    assert not at.exception
    after = int({m.label: m.value for m in at.metric}["R3 전표"].replace(",", ""))
    assert before < after <= before + extra  # (b)·(c)로 이미 표시된 전표는 다시 세지 않음


def test_선택_열이_없는_다른_업종_파일도_탐지_화면이_표시됨():
    """필수 6개 열만 있고 계정 체계가 다른(서비스업 가정) 파일: R2(a)·R3(b)(c)는 꺼지고 이유가 표시돼야 합니다."""
    lines = ["전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액"]
    for i in range(40):
        day = f"2025-{1 + i % 12:02d}-{10 + i % 15:02d}"
        lines += [f"S{i},{day},6100,교육훈련비,{200000 + i * 1000},0", f"S{i},{day},1010,현금,0,{200000 + i * 1000}"]
    lines += ["BIG,2025-06-11,6100,교육훈련비,90000000,0", "BIG,2025-06-11,1010,현금,0,90000000"]
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.radio[0].set_value("내 파일 업로드").run()
    at.file_uploader[0].upload("서비스업.csv", "\n".join(lines).encode("utf-8"), "text/csv").run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["R1 라인"] == "2" and metrics["R2(a) 라인"] == "0"
    captions = " ".join(c.value for c in at.caption)
    assert "입력일시 열이 없거나" in captions
    status = [d.value for d in at.dataframe if "구분" in d.value.columns and "설명" in d.value.columns][0]
    assert "거래처 열 자체가 없음" in status.set_index("구분").loc["거래처 없음", "설명"]


def test_R4_R6_탭과_R5_기준금액_입력():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["R4 전표"].replace(",", "")) > 0 and metrics["R6 전표 쌍"] == "1"
    assert metrics["R5 거래처·조합"] == "0"  # 기준금액을 넣기 전에는 R5를 검사하지 않음
    assert any("기준금액을 입력해야 검사합니다" in i.value for i in at.info)
    at.number_input(key="det_r5_threshold").set_value(50_000_000).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["R5 거래처·조합"] == "1"
