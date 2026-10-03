"""AuditLens 화면 (Streamlit).

실행: streamlit run app.py
현재 버전: 1) 데이터 불러오기  2) 열 연결  3) 데이터 검증  4) 기초 분석
이상 징후 탐지·결과 다운로드는 다음 개발 단위에서 추가합니다.
"""

import streamlit as st

from auditlens.analysis import (
    CHART_UNIT, CHART_UNIT_LABEL, KEY_ACCOUNT_GROUPS, MEASURES, account_summary, accounts_in_group,
    compare_years, fiscal_years, key_account_summary, monthly_summary, overview, prepare,
    unbalanced_vouchers,
)
from auditlens.loader import LoadError, list_excel_sheets, read_uploaded_file
from auditlens.sample_data import (
    COMPANY_NAME, CURRENT_YEAR, FICTIONAL_NOTICE, PRIOR_YEAR, generate_error_example,
    generate_sample_journal, journal_to_csv_bytes, journal_to_excel_bytes,
)
from auditlens.schema import COLUMN_LABELS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, suggest_mapping
from auditlens.validation import ERROR, INFO, WARNING, validate_journal

NOT_SELECTED = "(선택 안 함)"
SAMPLE_FILE_NAME = f"가상데이터_분개장_{PRIOR_YEAR}_{CURRENT_YEAR}.csv"

st.set_page_config(page_title="AuditLens", page_icon="🔍", layout="wide")


@st.cache_data(show_spinner="가상 분개장을 만드는 중입니다...")
def load_sample_files():
    journal, answer_key = generate_sample_journal()
    return (journal_to_csv_bytes(journal), journal_to_excel_bytes(journal), answer_key,
            len(journal), journal["je_id"].nunique())


@st.cache_data(show_spinner="파일을 읽는 중입니다...")
def cached_read(file_name, data, sheet):
    return read_uploaded_file(file_name, data, sheet)


@st.cache_data(show_spinner="데이터를 검증하는 중입니다...")
def cached_validate(raw, mapping_items):
    return validate_journal(raw, dict(mapping_items))


# ---------------------------------------------------------------- 머리말
st.title("🔍 AuditLens")
st.caption("분개장 비경상 거래 탐지 프로그램 · 감사인의 추가 검토 대상 선정을 돕는 도구")
st.info(
    "AuditLens가 표시하는 결과는 **추가 검토가 필요할 수 있는 후보**입니다. "
    "부정이나 회계 오류를 확정하지 않으며, 감사인의 전문가적 판단을 대체하지 않습니다."
)

with st.sidebar:
    st.header("진행 단계")
    st.markdown("1. 데이터 불러오기\n2. 열 연결\n3. 데이터 검증\n4. 기초 분석")
    st.caption("이상 징후 탐지, 결과 다운로드는 다음 개발 단위에서 추가될 예정입니다.")

# ---------------------------------------------------------------- 1. 데이터 불러오기
st.header("1. 데이터 불러오기")
mode = st.radio("데이터 선택", ["가상 샘플 데이터로 체험", "내 파일 업로드"], horizontal=True)

file_name, data, sheet = None, None, None
if mode == "가상 샘플 데이터로 체험":
    csv_bytes, excel_bytes, answer_key, n_lines, n_entries = load_sample_files()
    st.warning(f"**[가상 데이터] {COMPANY_NAME}**  \n{FICTIONAL_NOTICE}")
    st.write(f"전기 {PRIOR_YEAR}년·당기 {CURRENT_YEAR}년, 전표 {n_entries:,}개 · 분개 라인 {n_lines:,}행. "
             "전표번호는 모두 SIM-으로 시작합니다.")
    c1, c2, c3 = st.columns(3)
    c1.download_button("가상 분개장 CSV 내려받기", csv_bytes, SAMPLE_FILE_NAME, "text/csv")
    c2.download_button("가상 분개장 Excel 내려받기", excel_bytes,
                       f"가상데이터_분개장_{PRIOR_YEAR}_{CURRENT_YEAR}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c3.download_button("테스트 정답표 CSV 내려받기",
                       answer_key.to_csv(index=False).encode("utf-8-sig"),
                       "가상데이터_테스트정답표.csv", "text/csv")
    with st.expander("테스트 정답표 보기 (의도적으로 넣은 패턴 목록)"):
        st.caption("탐지 규칙이 제대로 작동하는지 확인하려고 넣은 테스트용 장치입니다. "
                   "실제 부정이나 회계 오류를 뜻하지 않습니다.")
        st.dataframe(answer_key.drop(columns=["전표번호 전체"]), hide_index=True, use_container_width=True)
    file_name, data = SAMPLE_FILE_NAME, csv_bytes
else:
    uploaded = st.file_uploader("분개장 파일 (CSV 또는 Excel .xlsx)", type=["csv", "xlsx", "xlsm", "xls"])
    st.caption("업로드한 파일은 이 화면에서만 사용하며 저장하지 않습니다. "
               "공개된 환경에서는 실제 회사 자료를 올리지 마세요.")
    error_csv, error_notes = generate_error_example()
    with st.expander("검증 기능을 체험해 보고 싶다면: 오류 예시 파일"):
        st.download_button("오류 예시 CSV 내려받기", error_csv, "가상데이터_오류예시.csv", "text/csv")
        st.markdown("이 파일을 내려받아 위에 다시 올리면 아래 오류들이 어떻게 안내되는지 볼 수 있습니다.")
        st.markdown("\n".join(f"- {n}" for n in error_notes))
    if uploaded is not None:
        file_name, data = uploaded.name, uploaded.getvalue()
        if file_name.lower().endswith((".xlsx", ".xlsm")):
            try:
                sheets = list_excel_sheets(data)
                sheet = st.selectbox("분개장이 들어 있는 시트", sheets)
            except LoadError as exc:
                st.error(str(exc))
                st.stop()

if data is None:
    st.stop()

try:
    raw, info = cached_read(file_name, data, sheet)
except LoadError as exc:
    st.error(f"파일을 읽을 수 없습니다. {exc}")
    st.stop()

detail = f"{info['format']}"
if info.get("encoding"):
    detail += f", 인코딩 {'UTF-8' if info['encoding'].startswith('utf') else 'CP949'}"
if info.get("sheet"):
    detail += f", 시트 '{info['sheet']}'"
st.success(f"파일을 읽었습니다: {file_name} ({detail}) · {len(raw):,}행 × {len(raw.columns)}열")
with st.expander("원본 미리보기 (처음 20행)"):
    st.dataframe(raw.head(20), use_container_width=True)

# ---------------------------------------------------------------- 2. 열 연결
st.header("2. 열 연결")
st.write("파일의 열이 어떤 항목인지 연결합니다. 열 이름을 보고 자동으로 추측했으니 맞는지 확인해 주세요. "
         "**\\*** 표시는 필수 항목입니다.")
suggested = suggest_mapping(raw.columns)
options = [NOT_SELECTED] + list(raw.columns)
mapping = {}
cols = st.columns(3)
for n, std_col in enumerate(REQUIRED_COLUMNS + OPTIONAL_COLUMNS):
    required = std_col in REQUIRED_COLUMNS
    label = f"{COLUMN_LABELS[std_col]}{' *' if required else ''}"
    default = suggested.get(std_col)
    choice = cols[n % 3].selectbox(
        label, options, index=options.index(default) if default in options else 0,
        key=f"map_{std_col}_{file_name}_{sheet}",
    )
    mapping[std_col] = None if choice == NOT_SELECTED else choice
missing_optional = [COLUMN_LABELS[c] for c in OPTIONAL_COLUMNS if not mapping[c]]
if missing_optional:
    st.caption("연결하지 않은 선택 항목: " + ", ".join(missing_optional)
               + " → 이 항목이 필요한 일부 탐지 규칙은 나중에 자동으로 꺼집니다.")

# ---------------------------------------------------------------- 3. 데이터 검증
st.header("3. 데이터 검증")
result = cached_validate(raw, tuple(sorted(mapping.items())))
table = result.issues_table()
n_err = int((table["등급"] == ERROR).sum())
n_warn = int((table["등급"] == WARNING).sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("전체 행", f"{len(raw):,}")
m2.metric("분석 대상 행", f"{result.summary.get('분석 대상 행', 0):,}")
m3.metric("제외 행", f"{len(result.excluded_rows):,}")
m4.metric("오류 / 경고", f"{n_err} / {n_warn}")

if result.has_errors:
    st.error("오류가 있어 분석을 시작할 수 없습니다. 아래 '수정 방법'을 참고해 파일이나 열 연결을 고쳐 주세요.")
elif n_warn:
    st.warning("분석은 가능하지만 확인할 경고가 있습니다. 제외된 행이 있다면 분석 결과에 반영되지 않습니다.")
else:
    st.success("검증을 통과했습니다. 아래 4번에서 기초 분석 결과를 확인할 수 있습니다.")


st.subheader("검증 결과")
show = {ERROR: st.error, WARNING: st.warning, INFO: st.info}
for item in table.to_dict("records"):
    lines = [f"**[{item['등급']} · {item['코드']}] {item['항목']}**", item["문제"]]
    if item["행 번호(예시)"]:
        lines.append(f"해당 행: {item['행 번호(예시)']}")
    lines.append(f"수정 방법: {item['수정 방법']}")
    show[item["등급"]]("  \n".join(lines))
st.download_button("검증 결과 CSV 내려받기", table.to_csv(index=False).encode("utf-8-sig"),
                   "검증결과.csv", "text/csv")

with st.expander("검증 항목 설명"):
    st.markdown(
        "- **V1 필수 열**: 전표번호, 회계처리일, 계정코드, 계정과목명, 차변금액, 대변금액\n"
        "- **V2 빈 파일**: 데이터 행이 1개 이상인지\n"
        "- **V3 날짜 형식**: 회계처리일(필수)·입력일시(선택)를 날짜로 읽을 수 있는지\n"
        "- **V4 금액 형식**: 차변·대변 금액을 숫자로 읽을 수 있는지\n"
        "- **V5 필수 값 결측**: 전표번호·회계처리일·계정코드·계정과목명이 비어 있지 않은지\n"
        "- **V6 차대 기재**: 한 행에 차변 또는 대변 중 하나만 있는지\n"
        "- **V7 음수 금액**: 음수 금액 여부(역분개 표기 방식일 수 있음)\n"
        "- **V8 전표별 차대 균형**: 같은 전표번호의 차변 합계와 대변 합계가 같은지(1원 이하 차이 허용)\n"
        "- **V9 중복 행**: 모든 열의 값이 같은 행이 있는지\n"
        "- **V10 분석 기간**: 회계처리일 범위와 차변·대변 합계\n"
        "- **V11 계정명 일치**: 계정코드 하나에 계정과목명이 하나인지"
    )

if result.excluded_rows:
    with st.expander(f"분석에서 제외한 행 번호 ({len(result.excluded_rows)}개)"):
        st.write(", ".join(map(str, result.excluded_rows)))

if result.clean is not None:
    st.session_state["validated_journal"] = result.clean
    with st.expander("검증 후 분석용 데이터 미리보기 (처음 100행)"):
        preview = result.clean.head(100).rename(columns={**COLUMN_LABELS, "file_row": "파일 행 번호"})
        st.dataframe(preview, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- 4. 기초 분석
st.header("4. 기초 분석")
if result.clean is None:
    st.info("검증 '오류'를 해결하면 기초 분석이 표시됩니다.")
    st.stop()

df, prep_notes = prepare(result.clean)
st.caption(
    f"분석 기준: 3단계 검증을 통과한 {len(result.clean):,}행"
    f"{f' (검증에서 제외한 {len(result.excluded_rows):,}행은 포함하지 않음)' if result.excluded_rows else ''} · "
    f"금액 단위: 표는 원, 차트는 {CHART_UNIT_LABEL} · 연도·월은 회계처리일 기준, 회계연도는 1~12월로 가정 · "
    "이 화면은 데이터를 이해하기 위한 요약이며 이상 거래 탐지 결과가 아닙니다."
)
for note in prep_notes:
    st.warning(f"[분석 제외] {note['항목']}: {note['행 수']:,}행. {note['처리']}")
if len(df) == 0:
    st.warning("분석할 수 있는 행이 없습니다. 파일 내용과 검증 결과를 확인해 주세요.")
    st.stop()


def money(table, columns):
    """금액 열에 천 단위 쉼표를 붙여 보여 줍니다(값 자체는 바꾸지 않음)."""
    return table.style.format("{:,.0f}", subset=columns, na_rep="")


def to_chart_unit(table, columns):
    return table[columns] / CHART_UNIT


tab_overview, tab_account, tab_month = st.tabs(["전표 현황", "계정별 분석", "월별 분석·전기 비교"])

with tab_overview:
    ov = overview(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전표 수", f"{ov['전표 수']:,}")
    c2.metric("분개 라인 수", f"{ov['라인 수']:,}")
    c3.metric("계정 수", f"{ov['계정 수']:,}")
    c4.metric("자료가 있는 월 수", f"{ov['자료가 있는 월 수']:,}")
    st.write(f"**분석 대상 기간**: {ov['기간 시작']:%Y-%m-%d} ~ {ov['기간 종료']:%Y-%m-%d} (회계처리일 기준)")
    d1, d2, d3 = st.columns(3)
    d1.metric("총 차변 (원)", f"{ov['총 차변']:,.0f}")
    d2.metric("총 대변 (원)", f"{ov['총 대변']:,.0f}")
    d3.metric("차이: 차변 - 대변 (원)", f"{ov['차이']:,.0f}")
    st.caption("전표 수는 전표번호 기준, 분개 라인 수는 파일의 행 기준입니다.")

    st.subheader("차변·대변 일치 확인")
    if ov["합계 일치"]:
        st.success("① 전체 합계: 차변 합계와 대변 합계가 일치합니다 (1원 이하 차이 허용).")
    else:
        st.warning(f"① 전체 합계: 차변 합계와 대변 합계가 {abs(ov['차이']):,.0f}원 다릅니다.")
    if ov["불균형 전표 수"] == 0:
        st.success("② 전표별: 모든 전표에서 차변 합계와 대변 합계가 일치합니다 (1원 이하 차이 허용).")
    else:
        st.warning(f"② 전표별: 차변·대변이 맞지 않는 전표가 {ov['불균형 전표 수']:,}개 있습니다 "
                   f"(차이 절대값 합계 {ov['불균형 전표 차이 절대값 합계']:,.0f}원).")
        unbalanced = unbalanced_vouchers(df)
        with st.expander("불균형 전표 목록"):
            st.dataframe(money(unbalanced, ["차변 합계", "대변 합계", "차이"]), hide_index=True,
                         use_container_width=True)
    st.info(
        "**읽는 법**: ①이 일치해도 ②에서 불균형 전표가 있을 수 있습니다. 한 전표의 차이가 다른 전표의 반대 차이와 "
        "상쇄되면 전체 합계는 맞아 보이기 때문입니다. 또한 ①과 ②가 모두 일치해도 계정 분류, 금액, 회계기간이 "
        "회계적으로 올바르다는 뜻은 아닙니다. 그 판단에는 증빙 확인 등 별도의 감사절차가 필요합니다."
    )

with tab_account:
    accounts = account_summary(df)
    key = key_account_summary(df)
    st.subheader("제조업 주요 계정")
    st.caption("원가 흐름: 원재료 → (제조원가 투입) → 재공품 → 제품 → 매출원가. 자산 계정은 차변이 증가, "
               "대변이 감소이고, 비용 계정은 차변이 발생입니다. 분류는 아래 '분류 기준'에 따라 계정과목명과 "
               "계정코드로 자동 판단하므로, 회사 계정체계와 다르면 실제와 맞지 않을 수 있습니다.")
    st.dataframe(money(key, ["차변 합계", "대변 합계", "순액(차변-대변)"]), hide_index=True,
                 use_container_width=True)
    chart = key.set_index("구분")
    st.bar_chart(to_chart_unit(chart, ["차변 합계", "대변 합계"]), stack=False, horizontal=True,
                 x_label="구분", y_label=f"금액 ({CHART_UNIT_LABEL})")

    st.subheader("계정별 월별 추이")
    labels = [f"{c} {n}" for c, n in zip(accounts["계정코드"], accounts["계정과목명"])]
    default = next((i for i, g in enumerate(accounts["주요 계정 구분"]) if g == "원재료"), 0)
    chosen = st.selectbox("계정 선택", labels, index=default, key="analysis_account")
    code = accounts["계정코드"].iloc[labels.index(chosen)]
    row = accounts[accounts["계정코드"] == code].iloc[0]
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("라인 수", f"{row['라인 수']:,}")
    e2.metric("라인 금액 중앙값 (원)", f"{row['라인 금액 중앙값']:,.0f}")
    e3.metric("라인 금액 최대 (원)", f"{row['라인 금액 최대']:,.0f}")
    e4.metric("순액: 차변 - 대변 (원)", f"{row['순액(차변-대변)']:,.0f}")
    monthly_account = monthly_summary(df, [code]).set_index("연월")
    st.bar_chart(to_chart_unit(monthly_account, ["차변 합계", "대변 합계"]), stack=False,
                 x_label="연월", y_label=f"금액 ({CHART_UNIT_LABEL})")

    st.subheader("전체 계정")
    st.caption("라인 금액 = 차변 + 대변 (한 라인에는 보통 한쪽만 있으므로 그 라인의 거래 금액). "
               "계정과목명이 여러 개로 적힌 계정은 가장 많이 쓰인 이름 뒤에 '외 n개'를 붙였습니다.")
    money_cols = ["차변 합계", "대변 합계", "순액(차변-대변)", "라인 금액 평균", "라인 금액 중앙값",
                  "라인 금액 최소", "라인 금액 최대"]
    st.dataframe(money(accounts, money_cols), hide_index=True, use_container_width=True)
    st.download_button("계정별 분석 CSV 내려받기", accounts.to_csv(index=False).encode("utf-8-sig"),
                       "계정별분석.csv", "text/csv")

with tab_month:
    st.subheader("월별 거래 현황")
    monthly = monthly_summary(df)
    st.line_chart(to_chart_unit(monthly.set_index("연월"), ["차변 합계", "대변 합계"]),
                  x_label="연월", y_label=f"금액 ({CHART_UNIT_LABEL})")
    st.bar_chart(monthly.set_index("연월")[["전표 수"]], x_label="연월", y_label="전표 수 (건)")
    st.dataframe(money(monthly, ["차변 합계", "대변 합계"]), hide_index=True, use_container_width=True)
    st.caption("차변 합계와 대변 합계가 같은 달은 두 선이 겹쳐 하나로 보입니다. 거래가 없는 달도 0으로 표시합니다(비고: 거래 없음). 전표 수는 그 달에 라인이 있는 전표번호 수이며, "
               "여러 달에 걸친 전표는 각 달에 한 번씩 셉니다.")

    st.subheader("전기·당기 같은 달 비교")
    years = fiscal_years(df)
    current_options = [y for y in years if y - 1 in years]
    if not current_options:
        st.info(f"데이터에 있는 연도: {', '.join(map(str, years))}년. 전기·당기 비교에는 연속된 2개 연도의 자료가 "
                "필요합니다.")
    else:
        f1, f2, f3 = st.columns(3)
        current = f1.selectbox("당기", current_options, index=len(current_options) - 1,
                               format_func=lambda y: f"{y}년 (전기 {y - 1}년)", key="analysis_current")
        prior = current - 1
        measure = f2.selectbox("비교 항목", MEASURES, index=MEASURES.index("차변 합계"), key="analysis_measure")
        scopes = ["전체 계정"] + [f"[구분] {g}" for g, _ in KEY_ACCOUNT_GROUPS] + labels
        scope = f3.selectbox("범위", scopes, key="analysis_scope")
        if scope == "전체 계정":
            codes = None
        elif scope.startswith("[구분] "):
            codes = accounts_in_group(df, scope.removeprefix("[구분] "))
        else:
            codes = [accounts["계정코드"].iloc[labels.index(scope)]]

        table = compare_years(df, prior, current, measure, codes)
        is_amount = measure in ("차변 합계", "대변 합계")
        months = table[table["월"] != "합계"].copy()
        months.index = range(1, 13)
        chart = months[["전기", "당기"]].rename(columns={"전기": f"{prior}년 (전기)", "당기": f"{current}년 (당기)"})
        if is_amount:
            chart = chart / CHART_UNIT
        st.bar_chart(chart, stack=False, x_label="월",
                     y_label=f"{measure} ({CHART_UNIT_LABEL if is_amount else '건'})")
        fmt = "{:,.0f}"
        st.dataframe(table.style.format(fmt, subset=["전기", "당기", "증감"])
                     .format("{:,.1f}", subset=["증감률(%)"], na_rep=""),
                     hide_index=True, use_container_width=True)
        st.caption("같은 달끼리 비교합니다. 증감 = 당기 - 전기, 증감률 = 증감 ÷ 전기 × 100. 전기 값이 0이면 증감률은 "
                   "빈칸입니다. '자료 없음'은 그 해 그 달에 어떤 계정의 자료도 없다는 뜻으로, 거래가 0인 것과 "
                   "구분해서 봐야 합니다.")
        if codes == []:
            st.warning("선택한 구분에 해당하는 계정이 없어 모든 값이 0입니다.")
