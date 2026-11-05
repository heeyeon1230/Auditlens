"""Streamlit 화면 자동 테스트 (화면을 띄우지 않고 실행 흐름만 확인)."""

from pathlib import Path

import pandas as pd
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
    at.selectbox(key="analysis_scope").select("455 제품매출원가").run()  # 제조업 구분 대신 계정 단위
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
    assert any("전기 2024 회계연도 (원)" in h and "증감 (원)" in h for h in headers)
    at.selectbox(key="analysis_measure").select("전표 수").run()
    headers = [list(d.value.columns) for d in at.dataframe]
    assert any("전기 2024 회계연도 (건)" in h for h in headers)


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


def test_탐지_기준_설정이_사이드바에_있고_기본값이면_변경_0개():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    assert any(h.value == "탐지 기준 설정" for h in at.sidebar.header)
    labels = [e.label for e in at.sidebar.expander]
    # 결산일은 기초 분석(회계연도)과 탐지가 함께 쓰므로 범용화 작업에서 사이드바 맨 위 '공통: 결산일'로 옮겼습니다.
    headers = [h.value for h in at.sidebar.header]
    assert headers.index("공통: 결산일") < headers.index("탐지 기준 설정")
    assert at.sidebar.number_input(key="det_closing_month").value == 12
    assert labels == ["R1 금액 분포", "R2(a) 거래처 이전 거래", "R2(b) 계정-월 이상 기간", "R3 시점",
                      "R4 수기·조정", "R5 신규 거래처", "R6 중복 가능"]
    assert [t.label for t in at.sidebar.toggle] == ["R1 사용", "R2(a) 사용", "R2(b) 사용", "R3 사용", "R4 사용", "R5 사용",
                                                    "R6 사용"]
    assert all(t.value for t in at.sidebar.toggle)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["기본값과 다른 항목"] == "0개"
    assert metrics["우선 검토 전표"] == "137"  # 기본값 결과는 이전과 같음
    table = [d.value for d in at.dataframe if "기본값과 다름" in d.value.columns][0]
    assert (table["기본값과 다름"] == "").all()


def test_규칙을_끄면_꺼짐_표시되고_되돌리기로_복원():
    at = AppTest.from_file(APP, default_timeout=180).run()
    before = {m.label: m.value for m in at.metric}
    at.toggle(key="det_on_r3_enabled").set_value(False).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["R3 전표"] == "꺼짐" and metrics["기본값과 다른 항목"] == "1개"
    assert metrics["R1 라인"] == before["R1 라인"] and metrics["R4 전표"] == before["R4 전표"]  # 다른 규칙은 그대로
    assert any("R3 규칙이 꺼져 있습니다" in w.value for w in at.warning)
    assert any("꺼진 규칙: R3" in w.value for w in at.sidebar.warning)
    assert "R3 시점" in [t.label for t in at.tabs]  # 탭은 그대로 둠
    table = [d.value for d in at.dataframe if "기본값과 다름" in d.value.columns][0].set_index("기준")
    assert table.loc["R3 사용", "적용값"] == "꺼짐" and table.loc["R3 사용", "기본값과 다름"] == "예"

    at.number_input(key="det_r6_days").set_value(10).run()
    assert {m.label: m.value for m in at.metric}["기본값과 다른 항목"] == "2개"
    at.button(key="det_reset").click().run()
    assert not at.exception
    assert at.toggle(key="det_on_r3_enabled").value is True and at.number_input(key="det_r6_days").value == 7
    assert {m.label: m.value for m in at.metric} == before  # 기본값 결과로 복원


def test_R2b를_끄면_5_2에_꺼짐_표시():
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.toggle(key="det_on_r2b_enabled").set_value(False).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["검토 대상 기간 (계정-월)"] == "꺼짐"
    assert any("R2(b) 규칙이 꺼져 있습니다" in w.value for w in at.warning)


def test_결과_다운로드_구역과_버튼():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    assert any(h.value == "6. 결과 다운로드" for h in at.header)
    buttons = at.get("download_button")
    labels = [b.proto.label for b in buttons]
    assert "Excel 파일 내려받기 (.xlsx)" in labels and "CSV 묶음 내려받기 (.zip)" in labels
    sidebar = " ".join(c.value for c in at.sidebar.caption)
    assert "다음 개발 단위" not in sidebar and "6. 결과 다운로드" in sidebar
    sheets = [d.value for d in at.dataframe if "Excel 시트" in d.value.columns][0]
    assert sheets["Excel 시트"].iloc[0] == "안내"


def _single_amount_csv():
    """금액 한 열 + 차대 구분(D/C) 형식의 작은 가상 분개장(서비스업 가정, 실제 회사 자료 아님)."""
    lines = ["전표번호,회계처리일,계정코드,계정과목명,구분,금액"]
    for i in range(40):
        day = f"2025-{1 + i % 12:02d}-{10 + i % 15:02d}"
        lines += [f"S{i},{day},6100,교육훈련비,D,{200000 + i * 1000}", f"S{i},{day},1010,현금,C,{200000 + i * 1000}"]
    lines += ["BIG,2025-06-11,6100,교육훈련비,D,90000000", "BIG,2025-06-11,1010,현금,C,90000000", "X1,2025-07-01,6100,교육훈련비,,500"]
    return "\n".join(lines).encode("utf-8")


def test_금액_한_열_형식_업로드와_구분값_연결():
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.radio[0].set_value("내 파일 업로드").run()
    at.file_uploader[0].upload("한열형식.csv", _single_amount_csv(), "text/csv").run()
    assert not at.exception
    fmt = [r for r in at.radio if r.label == "금액 형식"][0]
    assert fmt.value == "차변·대변 두 열"  # 기본은 기존 형식 그대로
    fmt.set_value("금액 한 열 + 차대 구분 열").run()
    labels = [s.label for s in at.selectbox]
    assert "금액 *" in labels and "차대 구분 *" in labels and "차변금액 *" not in labels
    [s for s in at.selectbox if s.label == "차대 구분 *"][0].select("구분").run()
    # 구분값을 고르기 전에는 분석을 시작하지 않음
    assert any("분석을 시작할 수 없습니다" in e.value for e in at.error)
    sides = {s.label: s for s in at.selectbox if s.label.startswith("'")}
    assert set(sides) == {"'D' (41행)", "'C' (41행)"}
    sides["'D' (41행)"].select("차변").run()
    [s for s in at.selectbox if s.label == "'C' (41행)"][0].select("대변").run()
    assert not at.exception and not at.error
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["제외 행"] == "1"  # 구분이 빈 X1 행
    assert any("차대 구분 값이 비어 있는 행이 1개" in w.value for w in at.warning)
    assert metrics["R1 라인"] == "2"  # BIG 전표의 차변·대변 라인 (두 열 형식 같은 자료의 기존 테스트와 같음)
    assert any(h.value == "6. 결과 다운로드" for h in at.header)


# ---------------------------------------------------------------- 범용화: 회계연도·계정별 분석·날짜와 숫자 표기·R4(d)
def _march_csv(description=True, blank_description=False):
    """3월 결산 서비스업을 가정한 가상 분개장(2024-04 ~ 2026-03, 영문 열 이름). 실제 회사 자료 아님."""
    head = "Document Number,Posting Date,GL Account,Account Description,Debit,Credit" + (",Text" if description else "")
    lines = [head]
    months = pd.period_range("2024-04", "2026-03", freq="M")
    for n, ym in enumerate(months):
        day = f"{ym.year}-{ym.month:02d}-15"
        text = "" if blank_description else f",용역 수익 {n}"
        lines += [f"D{n},{day},103000,보통예금,{100000 + n},0{text if description else ''}",
                  f"D{n},{day},411000,용역매출,0,{100000 + n}{text if description else ''}"]
    return "\n".join(lines).encode("utf-8")


def _upload(at, name, data):
    at.radio[0].set_value("내 파일 업로드").run()
    at.file_uploader[0].upload(name, data, "text/csv").run()
    return at


def test_결산월을_바꾸면_기초_분석의_회계연도가_바뀜():
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "march.csv", _march_csv())
    assert not at.exception and not at.error  # 영문 열 이름이 자동 연결됨
    fy = [d.value for d in at.dataframe if "회계기간" in d.value.columns][0]
    assert list(fy["회계연도"]) == [2024, 2025, 2026]  # 12월 결산(기본): 달력 연도
    at.sidebar.number_input(key="det_closing_month").set_value(3).run()
    assert not at.exception
    fy = [d.value for d in at.dataframe if "회계기간" in d.value.columns][0]
    assert list(fy["회계연도"]) == [2025, 2026]
    assert list(fy["회계기간"]) == ["2024-04 ~ 2025-03", "2025-04 ~ 2026-03"]
    compare = [d.value for d in at.dataframe if "증감률(%)" in d.value.columns][0]
    assert list(compare["월"])[:2] == ["4월", "5월"] and list(compare["월"])[11] == "3월"
    assert not compare["비고"].astype(bool).any()  # 두 회계연도 모두 12개월 자료
    assert any("회계연도: 4월 ~ 3월(3월 결산" in c.value for c in at.caption)


def test_상위_계정_표와_제조업_표_제거():
    at = AppTest.from_file(APP, default_timeout=120).run()
    subheaders = [h.value for h in at.subheader]
    assert "상위 계정" in subheaders and "제조업 주요 계정" not in subheaders
    top = [d.value for d in at.dataframe if "금액 비중(%)" in d.value.columns][0]
    assert len(top) == 10
    at.number_input(key="analysis_top_n").set_value(5).run()
    at.selectbox(key="analysis_top_measure").select("라인 수").run()
    assert not at.exception
    top = [d.value for d in at.dataframe if "금액 비중(%)" in d.value.columns][0]
    assert len(top) == 5 and top["라인 수"].is_monotonic_decreasing
    assert not any(o.startswith("[구분]") for o in at.selectbox(key="analysis_scope").options)


def test_모호한_날짜는_순서를_골라야_분석():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
            "J1,03/04/2025,131,원재료,1000,\nJ1,03/04/2025,251,외상매입금,,1000\n").encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=120).run(), "mdy.csv", data)
    assert any("월·일 순서를 알 수 없는 날짜" in e.value for e in at.error)
    order = [r for r in at.radio if r.label.startswith("날짜 순서")][0]
    order.set_value("일/월/연도").run()
    assert not at.exception and not at.error
    preview = [d.value for d in at.dataframe if "파일 행 번호" in d.value.columns and "회계처리일" in d.value.columns][0]
    assert str(preview["회계처리일"].iloc[0])[:10] == "2025-04-03"


def test_유럽식_숫자_표기를_고르면_읽음():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
            'J1,2025-01-02,131,원재료,"1.000,50",\nJ1,2025-01-02,251,외상매입금,,"1.000,50"\n').encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=120).run(), "eu.csv", data)
    assert any("유럽식 표기(예: 1.000,50)로 보이는" in w.value for w in at.warning)
    assert any("분석을 시작할 수 없습니다" in e.value for e in at.error)  # 두 행 모두 제외되어 분석할 행 없음
    [r for r in at.radio if r.label == "숫자 표기"][0].set_value("유럽식 (1.234,56)").run()
    assert not at.exception and not at.error
    preview = [d.value for d in at.dataframe if "파일 행 번호" in d.value.columns and "차변금액" in d.value.columns][0]
    assert preview["차변금액"].iloc[0] == 1000.5


def test_적요가_모두_비면_R4d_평가_불가_안내():
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "blank.csv", _march_csv(blank_description=True))
    assert not at.exception
    assert any("R4(d)는 평가하지 않았습니다(평가 불가)" in w.value for w in at.warning)
    counts = [d.value for d in at.dataframe if "적용 여부" in d.value.columns][0].set_index("조건")
    assert counts.loc["(d) 적요 없음·키워드", "적용 여부"] == "평가 불가 (적요 열의 값이 모두 비어 있음)"
    assert counts.loc["(d) 적요 없음·키워드", "해당 전표 수"] == 0


# ---------------------------------------------------------------- 5단계: 오류 수정·사용성
def test_화면_코드에_곧_없어질_use_container_width를_쓰지_않음():
    # Streamlit이 2025-12-31 이후 없앤다고 안내한 인수입니다. 새 버전을 설치해도 화면이 멈추지 않게 width를 씁니다.
    assert "use_container_width" not in Path(APP).read_text(encoding="utf-8")


def test_앞뒤_공백만_다른_같은_이름의_열이_있어도_화면이_멈추지_않음():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액,적요,적요 \n"
            "J1,2025-01-02,830,소모품비,1000,,가,나\nJ1,2025-01-02,103,보통예금,,1000,가,나\n").encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "dup.csv", data)
    assert not at.exception
    assert any("'적요' → '적요.1'" in i.value for i in at.info)
    assert any(h.value == "6. 결과 다운로드" for h in at.header)


def test_첫_행이_열_이름이_아니면_안내():
    data = "가상회사 분개장,,\n전표번호,회계처리일,계정코드\nJ1,2025-01-02,830\n".encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=120).run(), "title.csv", data)
    assert not at.exception
    assert any("첫 행이 열 이름이" in w.value for w in at.warning)


def test_제외한_행의_사유를_표로_보여_줌():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
            "J1,2025-01-02,830,소모품비,1000,\nJ1,2025-01-02,103,보통예금,,1000\n"
            "J2,2025-13-01,830,소모품비,500,\nJ2,2025-01-03,103,보통예금,,500\n,,,,,\n").encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "excluded.csv", data)
    assert not at.exception
    table = [d.value for d in at.dataframe if "제외 사유" in d.value.columns and "계정코드" in d.value.columns][0]
    assert list(table["파일 행 번호"]) == [4, 6]
    assert "회계처리일 형식 오류" in table["제외 사유"].iloc[0] and table["제외 사유"].iloc[1] == "모든 칸이 빈 행"


def _space_code_csv():
    """계정코드에 공백이 든 가상 분개장. '110 01' 계정의 2025-06 라인 수가 다른 달보다 크게 많습니다(R2(b))."""
    lines = ["전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액"]
    n = 0
    for ym in pd.period_range("2024-01", "2025-12", freq="M"):
        for k in range(12 if str(ym) == "2025-06" else 2):
            n += 1
            day = f"{ym.year}-{ym.month:02d}-{10 + k % 10:02d}"
            lines += [f"S{n},{day},110 01,가상미수금,{1000 + k},", f"S{n},{day},401,가상매출,,{1000 + k}"]
    return "\n".join(lines).encode("utf-8")


def test_R2b_기간_선택은_공백이_든_계정코드도_그_계정의_라인을_보여_줌():
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "space.csv", _space_code_csv())
    assert not at.exception
    at.selectbox(key="det_r2b_pick").set_value("110 01 가상미수금 · 2025-06").run()
    assert not at.exception
    assert any("2025-06 110 01 라인 12건" in c.value for c in at.caption)


def test_행_끝의_빈_추가_열을_제거하면_열_수와_함께_안내():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
            "J1,2025-01-02,830,소모품비,1000,,\nJ1,2025-01-02,103,보통예금,,1000,\n").encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=180).run(), "trailing.csv", data)
    assert not at.exception
    assert any("열 7개 → 6개" in i.value and "행은 2개" in i.value for i in at.info)
    assert any("2행 × 6열" in s.value for s in at.success)
    assert any("검증을 통과했습니다" in s.value for s in at.success)


def test_추가_열에_값이_있으면_읽기를_멈추고_안내():
    data = ("전표번호,회계처리일,계정코드,계정과목명,차변금액,대변금액\n"
            "J1,2025-01-02,830,소모품비,1000,,메모\nJ1,2025-01-02,103,보통예금,,1000\n").encode("utf-8")
    at = _upload(AppTest.from_file(APP, default_timeout=120).run(), "filled.csv", data)
    assert not at.exception
    assert any("열 구조가 맞지 않습니다" in e.value for e in at.error)
    assert not any(h.value == "2. 열 연결" for h in at.header)
