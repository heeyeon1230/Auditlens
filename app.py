"""AuditLens 화면 (Streamlit).

실행: streamlit run app.py
현재 버전: 1) 데이터 불러오기  2) 열 연결  3) 데이터 검증  4) 기초 분석  5) 검토 대상 거래(R1~R6)  6) 결과 다운로드
"""

import hashlib

import pandas as pd
import streamlit as st

from auditlens.analysis import (
    CHART_UNIT, CHART_UNIT_LABEL, KEY_ACCOUNT_GROUPS, MEASURES, REVIEW_LABEL, account_summary, accounts_in_group,
    compare_years, fiscal_years, key_account_summary, monthly_summary, overview, prepare,
    unbalanced_vouchers, unverifiable_vouchers, voucher_lines,
)
from auditlens.detection import RULE_SWITCHES, RULES, DetectionSettings, run_detection
from auditlens.detection import PERIOD_LABEL, PRIORITY_GROUP, SUPPORT_GROUP, SUPPORT_LABEL, period_vouchers
from auditlens.detection import REVIEW_LABEL as DETECTION_LABEL
from auditlens.export import (
    COMMON_LIMITS, MISS_NOTICE, REVIEW_NOTICE, SHEETS, build_tables, export_file_name, now_kst, to_csv_zip_bytes,
    to_excel_bytes,
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
    st.markdown("1. 데이터 불러오기\n2. 열 연결\n3. 데이터 검증\n4. 기초 분석\n5. 검토 대상 거래 (R1~R6)\n6. 결과 다운로드")
    st.caption("탐지 결과는 본문 맨 아래 '6. 결과 다운로드'에서 Excel 또는 CSV 묶음(ZIP)으로 내려받을 수 있습니다.")

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


def with_won(table, columns):
    """금액 열 이름 뒤에 단위 '(원)'을 붙입니다(값은 바꾸지 않음)."""
    return table.rename(columns={c: f"{c} (원)" for c in columns})


def money(table, columns):
    """금액 열 이름에 '(원)'을 붙이고 천 단위 쉼표로 보여 줍니다(값 자체는 바꾸지 않음)."""
    return with_won(table, columns).style.format("{:,.0f}", subset=[f"{c} (원)" for c in columns], na_rep="")


def date_only(table, column="회계처리일"):
    """날짜 열을 YYYY-MM-DD 글자로 바꿔 보여 줍니다. 날짜가 없으면 빈칸입니다."""
    return table.assign(**{column: table[column].dt.strftime("%Y-%m-%d").fillna("")})


def show_voucher_lines(je_id, excluded):
    """전표 하나의 라인 상세(파일 행 번호, 계정, 차변·대변, 제외 사유)를 보여 줍니다."""
    lines = voucher_lines(df, je_id, excluded)
    shown = with_won(date_only(lines), ["차변", "대변"])
    for col in ["차변 (원)", "대변 (원)"]:  # 제외된 행은 금액을 읽지 못했을 수 있으므로 빈칸
        shown[col] = [f"{v:,.0f}" if pd.notna(v) else "" for v in shown[col]]
    st.dataframe(shown, hide_index=True, use_container_width=True,
                 column_config={"제외 사유": st.column_config.TextColumn(width="large")})
    kept = lines[lines["구분"] == "분석 포함"]
    st.caption(f"분석 포함 행 합계: 차변 {kept['차변'].sum():,.0f}원 · 대변 {kept['대변'].sum():,.0f}원 · "
               f"차이 {kept['차변'].sum() - kept['대변'].sum():,.0f}원. 파일 행 번호는 올린 파일에서의 행 번호"
               "(머리글이 1행)입니다.")


def to_chart_unit(table, columns):
    return table[columns] / CHART_UNIT


tab_overview, tab_account, tab_month = st.tabs(["전표 현황", "계정별 분석", "월별 분석·전기 비교"])

with tab_overview:
    excluded = result.excluded_detail
    ov = overview(df, excluded)
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
    if result.excluded_rows:
        st.caption(f"검증에서 제외한 {len(result.excluded_rows):,}행은 ① 합계에 들어 있지 않습니다. 위 차이 중 "
                   f"{ov['검증 불가 전표 차이 합계']:,.0f}원은 아래 '검증 불가' 전표에서 생긴 것입니다.")

    s1, s2, s3 = st.columns(3)
    s1.metric("균형 전표", f"{ov['균형 전표 수']:,}")
    s2.metric("불균형 전표 (검토 필요)", f"{ov['불균형 전표 수']:,}")
    s3.metric("검증 불가 전표 (제외 행 포함)", f"{ov['검증 불가 전표 수']:,}")
    if ov["불균형 전표 수"] == 0:
        scope = "검증할 수 있는 전표는 모두" if ov["검증 불가 전표 수"] else "모든 전표에서"
        st.success(f"② 전표별: {scope} 차변 합계와 대변 합계가 일치합니다 (1원 이하 차이 허용).")

    if ov["불균형 전표 수"]:
        with st.container(border=True):
            st.markdown(f"#### ②-1 차대 불균형 전표 · {REVIEW_LABEL}")
            st.error(f"제외된 행이 없는데도 차변 합계와 대변 합계가 다른 전표가 {ov['불균형 전표 수']:,}개 있습니다 "
                     f"(차이 절대값 합계 {ov['불균형 전표 차이 절대값 합계']:,.0f}원). 분개 오류로 확정된 것이 "
                     "아니라 증빙과 대조해 검토가 필요한 전표입니다.")
            table = unbalanced_vouchers(df, excluded)
            st.dataframe(money(date_only(table), ["차변 합계", "대변 합계", "차이"]), hide_index=True,
                         use_container_width=True,
                         column_config={"포함 계정": st.column_config.TextColumn(width="large")})
            pick = st.selectbox("라인 상세를 볼 불균형 전표", table["전표번호"], key="unbalanced_pick")
            show_voucher_lines(pick, excluded)

    if ov["검증 불가 전표 수"]:
        with st.container(border=True):
            st.markdown("#### ②-2 검증 불가 전표 · 제외 행 수정 후 재확인")
            st.warning(f"검증에서 제외된 행이 포함된 전표가 {ov['검증 불가 전표 수']:,}개 있습니다. 남은 행만으로는 "
                       "균형 여부를 판단할 수 없어 위 불균형 전표에 넣지 않았습니다. 제외 사유를 고쳐 다시 올리면 "
                       "확인할 수 있습니다.", icon="🔍")
            table = unverifiable_vouchers(df, excluded)
            st.dataframe(money(date_only(table), ["차변 합계", "대변 합계", "차이"]), hide_index=True,
                         use_container_width=True,
                         column_config={"제외 사유": st.column_config.TextColumn(width="large")})
            st.caption("라인 수·금액·차이는 분석에 남은 행만으로 계산한 값입니다.")
            pick = st.selectbox("라인 상세를 볼 검증 불가 전표", table["전표번호"], key="unverifiable_pick")
            show_voucher_lines(pick, excluded)

    if ov["전표번호 없는 제외 행 수"]:
        st.caption(f"전표번호가 비어 있어 어느 전표인지 알 수 없는 제외 행이 {ov['전표번호 없는 제외 행 수']:,}개 "
                   "있습니다. 이 행들은 어느 전표의 판정에도 반영되지 않았습니다.")
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
    st.download_button("계정별 분석 CSV 내려받기",
                       with_won(accounts, money_cols).to_csv(index=False).encode("utf-8-sig"),
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
        unit = "원" if is_amount else "건"
        shown = table.rename(columns={"전기": f"전기 {prior}년 ({unit})", "당기": f"당기 {current}년 ({unit})",
                                      "증감": f"증감 ({unit})"})
        st.dataframe(shown.style.format("{:,.0f}", subset=list(shown.columns[1:4]))
                     .format("{:,.1f}", subset=["증감률(%)"], na_rep=""),
                     hide_index=True, use_container_width=True)
        st.caption("같은 달끼리 비교합니다. 증감 = 당기 - 전기, 증감률 = 증감 ÷ 전기 × 100. 전기 값이 0이면 증감률은 "
                   "빈칸입니다. '자료 없음'은 그 해 그 달에 어떤 계정의 자료도 없다는 뜻으로, 거래가 0인 것과 "
                   "구분해서 봐야 합니다.")
        if codes == []:
            st.warning("선택한 구분에 해당하는 계정이 없어 모든 값이 0입니다.")


# ---------------------------------------------------------------- 5. 검토 대상 거래 (R1~R6)
st.header("5. 검토 대상 거래 (R1~R6)")
st.warning(f"{REVIEW_NOTICE}  \n{MISS_NOTICE}")  # 같은 문구를 내려받는 파일의 '안내'에도 씁니다(auditlens/export.py).
st.caption(f"분석 기준: 4단계 기초 분석과 같은 {len(df):,}행(검증에서 제외된 행은 탐지 대상이 아님) · 금액 단위: 원 · "
           "계정은 계정코드로 구분하며 특정 업종의 계정 체계를 가정하지 않습니다.")

defaults = DetectionSettings()
# 탐지 기준 입력칸의 이름표(key). '기본값으로 되돌리기'는 이 값들을 지워 입력칸이 기본값으로 다시 그려지게 합니다.
SETTING_KEYS = ["det_closing_month", "det_closing_day", "det_r3_window", "det_r3_month_end", "det_r1_mult", "det_r1_min",
                "det_r2_ratio", "det_r2_min", "det_r2_high", "det_r2_low", "det_r4_src", "det_r4_type", "det_r4_rare",
                "det_r4_kw", "det_r5_threshold", "det_r5_months", "det_r6_days"] + [f"det_on_{f}" for f in RULE_SWITCHES.values()]


def reset_detection_settings():
    for k in SETTING_KEYS:
        st.session_state.pop(k, None)


def file_values(col):
    """파일에 실제로 있는 값(앞뒤 공백 제거). 선택지는 이 값과 기본값을 합친 목록입니다."""
    if col not in df:
        return []
    return sorted(v for v in df[col].fillna("").astype(str).str.strip().unique() if v)


def rule_switch(box, rule):
    field = RULE_SWITCHES[rule]
    return box.toggle(f"{rule} 사용", getattr(defaults, field), key=f"det_on_{field}",
                      help="끄면 이 규칙을 계산하지 않고 종합(전표별)에도 넣지 않습니다. 결과 탭에는 '꺼짐'으로 표시합니다.")


with st.sidebar:
    st.divider()
    st.header("탐지 기준 설정")
    st.caption("기본값은 설계서 6절과 이전 단계에서 확정한 값입니다. 값을 바꾸면 결과를 바로 다시 계산합니다.")
    status_box = st.container()
    st.button("기본값으로 되돌리기", on_click=reset_detection_settings, key="det_reset", use_container_width=True)

    with st.expander("공통: 결산일"):
        closing_month = st.number_input("결산월", 1, 12, defaults.closing_month, key="det_closing_month")
        closing_day = st.number_input("결산일", 1, 31, defaults.closing_day, key="det_closing_day",
                                      help="그 달에 없는 날이면 그 달 말일로 맞춥니다(예: 2월 30일 → 2월 28일). "
                                           "R3와 R4(c)의 회계연도에 씁니다.")
    with st.expander("R1 금액 분포"):
        on = {"R1": rule_switch(st, "R1")}
        r1_multiplier = st.number_input("R1 IQR 배수", 0.5, 10.0, defaults.r1_multiplier, 0.5, key="det_r1_mult")
        r1_min_lines = st.number_input("R1 계정·방향별 최소 라인 수", 5, 1000, defaults.r1_min_lines, key="det_r1_min")
    with st.expander("R2(a) 거래처 이전 거래"):
        on["R2(a)"] = rule_switch(st, "R2(a)")
        r2_ratio = st.number_input("R2(a) 이전 거래 중앙값 대비 배수", 1.5, 100.0, defaults.r2_ratio, 0.5,
                                   key="det_r2_ratio")
        r2_min_prior = st.number_input("R2(a) 최소 이전 거래 수", 2, 100, defaults.r2_min_prior, key="det_r2_min")
    with st.expander("R2(b) 계정-월 이상 기간"):
        on["R2(b)"] = rule_switch(st, "R2(b)")
        r2_month_high = st.number_input("R2(b) 상한 배수 (이상이면 급증)", 1.5, 20.0, defaults.r2_month_high, 0.5,
                                        key="det_r2_high")
        r2_month_low = st.number_input("R2(b) 하한 배수 (이하이면 급감)", 0.01, 0.9, round(defaults.r2_month_low, 2), 0.01,
                                       key="det_r2_low")
    with st.expander("R3 시점"):
        on["R3"] = rule_switch(st, "R3")
        r3_window = st.number_input("R3(a) 전후 영업일 수", 0, 10, defaults.r3_window, key="det_r3_window")
        r3_month_end = st.checkbox("R3(a-2) 매월 말일 전후 (보조 옵션)", defaults.r3_month_end, key="det_r3_month_end",
                                   help="기본은 결산일 전후만 봅니다. 켜면 매달 말일 전후 전표가 (a-2) 조건으로 추가됩니다.")
    with st.expander("R4 수기·조정"):
        on["R4"] = rule_switch(st, "R4")
        src_opts = sorted(set(file_values("source")) | set(defaults.r4_sources))
        type_opts = sorted(set(file_values("je_type")) | set(defaults.r4_types))
        r4_sources = st.multiselect("R4(a) 수기로 볼 전표 원천 값", src_opts, list(defaults.r4_sources), key="det_r4_src",
                                    help="ERP마다 '수기'를 나타내는 값이 다르므로 파일에 있는 값 중에서 고릅니다.")
        r4_types = st.multiselect("R4(a) 조정으로 볼 전표 유형 값", type_opts, list(defaults.r4_types), key="det_r4_type")
        r4_rare_pct = st.number_input("R4(c) 입력자 전표 비율 기준 (% 미만)", 0.1, 50.0, defaults.r4_rare_share * 100, 0.5,
                                      key="det_r4_rare")
        r4_keywords = st.text_input("R4(d) 적요 키워드 (쉼표로 구분)", ", ".join(defaults.r4_keywords), key="det_r4_kw")
    with st.expander("R5 신규 거래처"):
        on["R5"] = rule_switch(st, "R5")
        r5_threshold = st.number_input("R5 기준금액 (원, 입력해야 검사)", min_value=0, value=None, step=1_000_000,
                                       key="det_r5_threshold", placeholder="예: 50000000",
                                       help="설계서: 기준금액은 감사인의 중요성 판단에 따라 정합니다. 기본값이 없습니다.")
        r5_months = st.number_input("R5 분석 기간 마지막 n개월", 1, 12, defaults.r5_months, key="det_r5_months")
    with st.expander("R6 중복 가능"):
        on["R6"] = rule_switch(st, "R6")
        r6_days = st.number_input("R6 회계처리일 차이 (일 이내)", 0, 60, defaults.r6_days, key="det_r6_days")

settings = DetectionSettings(
    r1_multiplier=float(r1_multiplier), r1_min_lines=int(r1_min_lines), r2_ratio=float(r2_ratio),
    r2_min_prior=int(r2_min_prior), r2_month_high=float(r2_month_high),
    # 입력칸은 소수 둘째 자리(0.33)까지만 보여 주므로, 기본값 그대로면 설계서 기본값 1/3을 씁니다.
    r2_month_low=defaults.r2_month_low if round(float(r2_month_low), 2) == round(defaults.r2_month_low, 2)
    else float(r2_month_low),
    r3_window=int(r3_window), r3_month_end=bool(r3_month_end), closing_month=int(closing_month),
    closing_day=int(closing_day),
    r4_sources=tuple(r4_sources), r4_types=tuple(r4_types), r4_rare_share=float(r4_rare_pct) / 100,
    r4_keywords=tuple(k.strip() for k in r4_keywords.split(",") if k.strip()),
    r5_threshold=None if r5_threshold is None else float(r5_threshold), r5_months=int(r5_months), r6_days=int(r6_days),
    **{RULE_SWITCHES[rule]: bool(v) for rule, v in on.items()},
)
changed = settings.changed()
off_rules = settings.disabled_rules()
with status_box:
    st.metric("기본값과 다른 항목", f"{len(changed)}개")
    if off_rules:
        st.warning(f"꺼진 규칙: {', '.join(off_rules)}")
    st.caption("기본값과 적용값 비교는 본문 5-3 '탐지 기준과 한계'에 있습니다.")
st.caption(f"탐지 기준은 왼쪽 사이드바의 '탐지 기준 설정'에서 바꿀 수 있습니다. 기본값과 다른 항목 {len(changed)}개"
           + (f", 꺼진 규칙: {', '.join(off_rules)}" if off_rules else ", 꺼진 규칙 없음") + ".")


@st.cache_data(show_spinner="탐지 규칙을 적용하는 중입니다...")
def cached_detection(frame, settings_items):
    return run_detection(frame, DetectionSettings(**dict(settings_items)))


det = cached_detection(df, tuple(sorted(vars(settings).items())))


def rule_box(code):
    """규칙 설명을 보여 주고, 규칙이 켜져 있으면 True. 꺼져 있으면 그 사실을 표시하고 False를 돌려줍니다."""
    rule = RULES[code]
    st.markdown(f"**{code}. {rule['이름']}** (판단 단위: {rule['단위']})")
    st.caption(f"목적: {rule['목적']}  \n조건: {rule['조건']}  \n한계: {rule['한계']}")
    if code in off_rules:
        st.warning(f"**{code} 규칙이 꺼져 있습니다.** 이 규칙은 계산하지 않았고, 결과는 종합(전표별)과 위 건수에도 들어가지 "
                   "않았습니다. 꺼져 있어서 결과가 없는 것이며, 해당 거래가 없다는 뜻은 아닙니다. 다시 켜려면 왼쪽 사이드바 "
                   f"'탐지 기준 설정'의 {code} 상자에서 '{code} 사용'을 켜 주세요.", icon="⏸️")
        return False
    return True


def rule_count(code, n):
    return "꺼짐" if code in off_rules else f"{n:,}"


def show_table(table, money_cols, date_cols=("회계처리일",), key=None):
    shown = table.copy()
    for col in date_cols:
        if col in shown:
            shown[col] = pd.to_datetime(shown[col]).dt.strftime("%Y-%m-%d").fillna("")
    if "입력일시" in shown:
        shown["입력일시"] = pd.to_datetime(shown["입력일시"]).dt.strftime("%Y-%m-%d %H:%M").fillna("")
    styled = money(shown, money_cols)
    if "중앙값 대비 배수" in shown:
        styled = styled.format("{:,.2f}", subset=["중앙값 대비 배수"])
    st.dataframe(styled, hide_index=True, use_container_width=True,
                 column_config={"탐지 이유": st.column_config.TextColumn(width="large"),
                                "제외 사유": st.column_config.TextColumn(width="large")}, key=key)


# ---- 5-1. 개별 거래 탐지 결과
with st.container(border=True):
    st.subheader("5-1. 개별 거래 탐지 결과 (R1 · R2(a) · R3 · R4 · R5 · R6)")
    st.caption(f"전표나 분개 라인 하나하나에 대한 결과입니다. 판정: {DETECTION_LABEL}. 수기·조정 여부만 해당하는 전표는 "
               f"'{SUPPORT_LABEL}'로 표시합니다.")
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    priority = det.summary[det.summary["검토 구분"] == PRIORITY_GROUP]
    support = det.summary[det.summary["검토 구분"] == SUPPORT_GROUP]
    m1.metric("우선 검토 전표", f"{len(priority):,}",
              help=f"종합 전표 {len(det.summary):,}개 중 수기·조정 여부만 해당하는 {len(support):,}개(보조 목록)를 뺀 수입니다.")
    m2.metric("R1 라인", rule_count("R1", len(det.r1)))
    m3.metric("R2(a) 라인", rule_count("R2(a)", len(det.r2a)))
    m4.metric("R3 전표", rule_count("R3", len(det.r3)))
    m5.metric("R4 전표", rule_count("R4", len(det.r4)))
    m6.metric("R5 거래처·조합", rule_count("R5", len(det.r5)))
    m7.metric("R6 전표 쌍", rule_count("R6", len(det.r6)))

    tab_sum, tab_r1, tab_r2a, tab_r3, tab_r4, tab_r5, tab_r6 = st.tabs(
        ["종합 (전표별)", "R1 금액 분포", "R2(a) 거래처 이전 거래", "R3 시점", "R4 수기·조정", "R5 신규 거래처", "R6 중복 가능"])

    with tab_sum:
        st.caption("종합은 규칙별 결과를 전표 단위로 모은 표입니다. 한 전표가 여러 규칙에 해당하면 규칙과 세부 조건을 모두 "
                   "적고, 해당 규칙 수 → 해당 세부 조건 수가 많은 순서로 보여 줍니다. 점수나 위험도 등급은 매기지 않습니다. "
                   "규칙별 원래 결과는 옆의 규칙 탭에 있고, R2(b)는 아래 5-2에 따로 있습니다.")
        st.caption("수기·조정 전표라는 사실만으로는 오류나 부정을 뜻하지 않습니다. 그래서 R4(a) 수기·조정 여부 하나만 해당하는 "
                   "전표는 아래 보조 목록으로 나누고, 다른 규칙이나 R4의 다른 조건과 함께 나타난 전표를 우선 검토 목록에 "
                   f"두었습니다. 보조 목록 전표의 판정은 '{SUPPORT_LABEL}'이며, R4 결과와 건수에는 그대로 남아 있습니다.")
        if det.summary.empty:
            st.success("현재 기준으로 R1~R6에 해당하는 전표가 없습니다. 탐지되지 않았다고 문제가 없다는 뜻은 아닙니다.")
        else:
            st.markdown(f"**우선 검토 목록** · {len(priority):,}개 전표")
            if priority.empty:
                st.info("다른 규칙이나 조건과 함께 나타난 전표가 없습니다. 보조 목록만 있습니다.")
            else:
                show_table(priority, ["전표 금액"], key="det_summary")
            with st.expander(f"보조 목록: 수기·조정 여부(R4(a))만 해당하는 전표 {len(support):,}개"):
                st.caption("다른 탐지 규칙과 R4의 다른 조건 (b)~(d)에는 해당하지 않은 전표입니다. 수기 분개를 검토 모집단으로 "
                           "보려면 이 목록을 함께 확인해 주세요.")
                show_table(support, ["전표 금액"], key="det_summary_support")
            pick = st.selectbox("상세를 볼 전표", det.summary["전표번호"], key="det_pick")
            row = det.summary.set_index("전표번호").loc[pick]
            st.markdown(f"**{pick}** · {row['검토 구분']} · 해당 규칙 {row['해당 규칙 수']}개 ({row['해당 규칙']}) · "
                        f"세부 조건 {row['해당 세부 조건 수']}개 ({row['해당 세부 조건']}) · {row['판정']}")
            st.dataframe(det.reasons[det.reasons["전표번호"] == pick].drop(columns="전표번호"), hide_index=True,
                         use_container_width=True, column_config={"탐지 이유": st.column_config.TextColumn(width="large")})
            st.caption("규칙별·라인별 탐지 사유를 모두 한 행씩 보여 줍니다. 아래는 이 전표의 전체 라인입니다.")
            show_voucher_lines(pick, result.excluded_detail)

    with tab_r1:
        if rule_box("R1"):
            if det.r1.empty:
                st.info("현재 기준으로 R1에 해당하는 라인이 없습니다.")
            else:
                show_table(det.r1, ["금액", "계정 중앙값", "상한 금액"], key="det_r1")
            if len(det.r1_excluded):
                with st.expander(f"금액 비교에서 뺀 라인 {len(det.r1_excluded):,}건 (0원·음수·차대 동시 기재)"):
                    st.caption("로그는 0보다 큰 금액에만 쓸 수 있어 이 라인들은 R1 비교에 넣지 않았습니다. 금액은 파일 값 그대로이며, "
                               "음수 금액(역분개 등)은 따로 확인해 주세요.")
                    show_table(det.r1_excluded, ["금액"], key="det_r1_excluded")
            if len(det.r1_skipped):
                with st.expander(f"검사하지 않은 계정·방향 {len(det.r1_skipped)}개 (라인 수 부족)"):
                    st.dataframe(det.r1_skipped, hide_index=True, use_container_width=True)

    with tab_r2a:
        if rule_box("R2(a)"):
            st.markdown("**처리 현황**: 모든 라인이 아래 구분 중 하나에 들어갑니다.")
            st.dataframe(det.r2a_status, hide_index=True, use_container_width=True,
                         column_config={"설명": st.column_config.TextColumn(width="large")})
            st.caption(f"적용 방법: 같은 거래처·계정·방향에서 회계처리일이 이 라인보다 앞선 거래만 기준으로 씁니다. 이 라인과 "
                       "같은 날의 거래와 뒤의 거래(미래 거래)는 기준에 넣지 않습니다. 앞선 거래가 "
                       f"{settings.r2_min_prior}건 이상일 때만 비교하고, 그 금액들의 중앙값 × {settings.r2_ratio:g}를 초과하면 탐지합니다.")
            if det.r2a.empty:
                st.info("현재 기준으로 R2(a)에 해당하는 라인이 없습니다.")
            else:
                show_table(det.r2a, ["금액", "이전 거래 중앙값"], key="det_r2a")
            if len(det.r2a_skipped):
                with st.expander(f"비교하지 않은 라인 {len(det.r2a_skipped):,}건과 사유"):
                    show_table(det.r2a_skipped, ["금액"], key="det_r2a_skipped")

    with tab_r3:
        if rule_box("R3"):
            for note in det.r3_notes:
                st.caption(f"참고: {note}")
            st.markdown("**조건별 전표 수**")
            st.dataframe(det.r3_counts, hide_index=True, use_container_width=True)
            st.caption(f"적용 기준: 결산일 매년 {settings.closing_month}월 {settings.closing_day}일, 전후 {settings.r3_window}영업일. "
                       "(a)는 회계처리일 기준, (b)·(c)는 입력일시 기준이며 입력일시가 없는 전표는 (b)·(c)를 검사하지 않습니다. "
                       "(a-2)는 보조 옵션이며 꺼져 있어도 해당 전표 수를 위 표에 보여 줍니다. 한 전표가 여러 조건에 해당할 수 "
                       "있어 조건별 수의 합은 R3 전표 수보다 클 수 있습니다.")
            if det.r3.empty:
                st.info("현재 기준으로 R3에 해당하는 전표가 없습니다.")
            else:
                conditions = ["전체"] + sorted({c for v in det.r3["해당 조건"] for c in v.split(", ")})
                cond = st.radio("조건별 보기", conditions, horizontal=True, key="det_r3_cond")
                table = det.r3 if cond == "전체" else det.r3[det.r3["해당 조건"].str.contains(cond, regex=False)]
                st.caption(f"{len(table):,}개 전표")
                show_table(table, ["전표 금액"], key="det_r3")

    with tab_r4:
        if rule_box("R4"):
            for note in det.r4_notes:
                st.caption(f"참고: {note}")
            st.markdown("**조건별 전표 수**")
            st.dataframe(det.r4_counts, hide_index=True, use_container_width=True)
            st.caption("한 전표가 여러 조건에 해당할 수 있어 조건별 수의 합은 R4 전표 수보다 클 수 있습니다. 값은 앞뒤 공백만 빼고 "
                       "파일에 적힌 그대로 비교합니다.")
            if det.r4.empty:
                st.info("현재 기준으로 R4에 해당하는 전표가 없습니다.")
            else:
                conditions = ["전체"] + sorted({c for v in det.r4["해당 조건"] for c in v.split(", ")})
                cond = st.radio("조건별 보기", conditions, horizontal=True, key="det_r4_cond")
                table = det.r4 if cond == "전체" else det.r4[det.r4["해당 조건"].str.contains(cond, regex=False)]
                only_manual = set(support["전표번호"])
                n_only = int(det.r4["전표번호"].isin(only_manual).sum())
                combos = {"전체": None, f"(a)만 해당 ({n_only:,}개, 종합의 보조 목록)": True,
                          f"다른 조건·규칙과 함께 ({len(det.r4) - n_only:,}개, 종합의 우선 검토 목록)": False}
                combo = st.radio("다른 조건과의 결합", list(combos), horizontal=True, key="det_r4_combo")
                if combos[combo] is not None:
                    table = table[table["전표번호"].isin(only_manual) == combos[combo]]
                st.caption(f"{len(table):,}개 전표")
                show_table(table, ["전표 금액"], key="det_r4")

    with tab_r5:
        if rule_box("R5"):
            for note in det.r5_notes:
                st.caption(f"참고: {note}")
            if settings.r5_threshold is None:
                st.info("R5는 기준금액을 입력해야 검사합니다. 왼쪽 사이드바 '탐지 기준 설정'의 R5 상자에서 "
                        "기준금액(원)을 입력해 주세요.")
            elif det.r5.empty:
                st.info("현재 기준으로 R5에 해당하는 거래처·계정 조합이 없습니다.")
            else:
                show_table(det.r5, ["합계 금액", "기준금액"], date_cols=("최초 등장일",), key="det_r5")
            if len(det.r5_candidates):
                with st.expander(f"판단 기간에 처음 나타난 거래처·계정 조합 {len(det.r5_candidates):,}개 (기준금액 미만 포함, 참고용)"):
                    show_table(det.r5_candidates, ["합계 금액"], date_cols=("최초 등장일",), key="det_r5_cand")

    with tab_r6:
        if rule_box("R6"):
            for note in det.r6_notes:
                st.caption(f"참고: {note}")
            if det.r6.empty:
                st.info("현재 기준으로 R6에 해당하는 전표 쌍이 없습니다.")
            else:
                st.caption("한 쌍에서 일치한 라인을 모두 '일치 라인'에 적었습니다. 두 전표는 종합(전표별)에 모두 R6으로 들어갑니다.")
                show_table(det.r6, ["일치 금액"], date_cols=("회계처리일 A", "회계처리일 B"), key="det_r6")

# ---- 5-2. 이상 기간 탐지 결과 (R2(b))
with st.container(border=True):
    st.subheader("5-2. 이상 기간 탐지 결과 (R2(b) · 계정-월)")
    st.info(f"이 결과는 개별 거래가 아니라 계정과 월의 조합에 대한 징후입니다. 판정: {PERIOD_LABEL}. "
            "그 달의 거래 하나하나를 검토 대상 거래로 올리지 않았으며, 위 5-1의 종합 전표 수에도 들어가지 않습니다.",
            icon="📅")
    st.metric("검토 대상 기간 (계정-월)", rule_count("R2(b)", det.r2b[['계정코드', '연월']].drop_duplicates().shape[0]))
    if rule_box("R2(b)"):
        st.caption(f"적용 기준: 다른 달 중앙값의 {settings.r2_month_high:g}배 이상이면 급증, {settings.r2_month_low:.2f}배 이하이면 급감.")
        if det.r2b.empty:
            st.info("현재 기준으로 R2(b)에 해당하는 계정-월이 없습니다.")
        else:
            shown = det.r2b.copy()
            shown["이 달 값"] = [f"{v:,.0f}" for v in shown["이 달 값"]]
            shown["다른 달 중앙값"] = [f"{v:,.0f}" for v in shown["다른 달 중앙값"]]
            st.dataframe(shown.style.format("{:,.2f}", subset=["중앙값 대비 배수"]), hide_index=True,
                         use_container_width=True, column_config={"탐지 이유": st.column_config.TextColumn(width="large")})
            st.caption("값의 단위는 '항목'이 라인 수이면 건, 금액이면 원입니다.")

            options = [f"{r.계정코드} {r.계정과목명} · {r.연월}" for r in det.r2b.drop_duplicates(["계정코드", "연월"])
                       .itertuples(index=False)]
            choice = st.selectbox("그 달의 거래 보기 (참고용)", options, key="det_r2b_pick")
            code, ym = choice.split(" ")[0], choice.split(" · ")[-1]
            month_lines = df[(df["account_code"] == code) & (df["year_month"] == ym)]
            lines_shown = month_lines[["file_row", "je_id", "posting_date", "account_code", "account_name", "debit", "credit"]
                                      + (["vendor"] if "vendor" in month_lines else [])]
            lines_shown = lines_shown.rename(columns={**COLUMN_LABELS, "file_row": "파일 행 번호"})
            show_table(lines_shown, ["차변금액", "대변금액"], key="det_r2b_lines")
            st.caption(f"{ym} {code} 라인 {len(month_lines):,}건. 기간을 검토할 때 참고하도록 보여 주는 목록이며, "
                       "이 라인들이 개별적으로 검토 대상이라는 뜻은 아닙니다.")
            vouchers = period_vouchers(df, det.summary, code, ym)
            groups = vouchers["종합 검토 구분"].value_counts()
            st.markdown(f"**이 기간의 전표와 종합 결과** · {len(vouchers):,}개 전표")
            st.caption(f"위 라인이 속한 전표를 한 행씩 모으고, 각 전표가 5-1 종합의 어느 목록에 있는지 붙였습니다. 우선 검토 목록 "
                       f"{groups.get(PRIORITY_GROUP, 0):,}개, 보조 목록 {groups.get(SUPPORT_GROUP, 0):,}개, 개별 규칙에 해당하지 "
                       f"않은 전표 {groups.get('해당 없음', 0):,}개입니다. '판정'과 '해당 규칙'은 종합의 결과를 그대로 옮긴 것이며, "
                       "R2(b) 때문에 이 전표들을 개별 검토 대상 거래로 판정한 것은 아닙니다.")
            show_table(vouchers, ["이 계정 금액", "전표 금액"], key="det_r2b_vouchers")

# ---- 5-3. 기준·한계
with st.expander("5-3. 탐지 기준과 한계"):
    st.markdown(f"**현재 적용한 기준값** · 기본값과 다른 항목 {len(changed)}개")
    st.dataframe(settings.as_table().astype(str), hide_index=True, use_container_width=True, key="det_settings_table")
    st.caption("'기본값과 다름'이 '예'인 항목이 사용자가 바꾼 설정입니다. 왼쪽 사이드바의 '기본값으로 되돌리기'로 한 번에 "
               "기본값으로 돌아갑니다.")
    st.markdown("**규칙별 목적·조건·한계**")
    st.dataframe(pd.DataFrame([{"규칙": k, **v} for k, v in RULES.items()]), hide_index=True,
                 use_container_width=True)
    st.caption(COMMON_LIMITS)


# ---------------------------------------------------------------- 6. 결과 다운로드
@st.cache_data(show_spinner="다운로드 파일을 만드는 중입니다...", max_entries=4)
def cached_export(_det, _result, _df, _notes, key):
    """화면의 탐지 결과(det)를 그대로 파일로 옮깁니다. key(원본·열 연결·탐지 기준)가 같으면 다시 만들지 않습니다."""
    created = now_kst()
    tables = build_tables(_det, _result, _df, key[0], created, _notes)
    return to_excel_bytes(tables), to_csv_zip_bytes(tables), created


st.header("6. 결과 다운로드")
export_key = (file_name, sheet, hashlib.sha256(data).hexdigest(), tuple(sorted(mapping.items())),
              tuple(sorted(vars(settings).items())))
xlsx_bytes, zip_bytes, export_time = cached_export(det, result, df, prep_notes, export_key)
st.caption(f"위 5단계 화면과 같은 결과를 파일로 내려받습니다(다시 계산하지 않음). 지금 적용한 탐지 기준(기본값과 다른 항목 "
           f"{len(changed)}개, 꺼진 규칙: {', '.join(off_rules) if off_rules else '없음'})이 '적용 기준' 시트에 함께 남습니다. "
           f"파일 내용을 만든 시각: {export_time:%Y-%m-%d %H:%M} (한국 시간).")
st.info("두 파일 모두 첫 장이 '안내'입니다. 판정의 뜻과 한계, 원본 파일 이름, 분석 행 수를 적었습니다. 파일의 결과도 "
        "오류나 부정의 결론이 아니라 추가 검토가 필요할 수 있는 후보입니다.", icon="📄")
d1, d2 = st.columns(2)
d1.download_button("Excel 파일 내려받기 (.xlsx)", xlsx_bytes, export_file_name(file_name, export_time, "xlsx"),
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx",
                   use_container_width=True)
d2.download_button("CSV 묶음 내려받기 (.zip)", zip_bytes, export_file_name(file_name, export_time, "zip"),
                   "application/zip", key="dl_zip", use_container_width=True,
                   help="ZIP 안에 안내 txt와 시트별 CSV(UTF-8)가 들어 있습니다. =, +, -, @로 시작하는 글자 값 앞에는 "
                        "작은따옴표(')를 붙였습니다.")
with st.expander("파일에 들어가는 시트"):
    st.dataframe(pd.DataFrame(SHEETS, columns=["Excel 시트", "CSV 묶음 안의 파일", "내용"]), hide_index=True,
                 use_container_width=True)
    st.caption("꺼진 규칙이나 기준금액을 넣지 않아 검사하지 않은 R5도 시트는 만들고, 결과가 없는 이유를 적습니다. 기초 분석(4단계) "
               "표는 넣지 않았습니다.")
