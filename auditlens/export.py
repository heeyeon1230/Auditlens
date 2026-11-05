"""탐지 결과 내려받기 (설계서 F7): Excel 한 파일과 CSV 묶음(ZIP).

화면이 이미 계산한 결과(DetectionResult)와 검증 결과를 그대로 받아 파일로 옮깁니다. 다시 계산하지 않으므로
파일 내용은 화면과 같습니다. 열은 기존 결과 표의 열을 그대로 쓰며, 특정 업종의 계정 목록이나 계정코드를 가정하지 않습니다.

- 모든 파일의 첫 장은 '안내'(판정의 뜻, 한계, 어떤 자료와 기준으로 만든 결과인지)입니다(설계서 8절).
- 금액은 숫자, 날짜는 날짜로 저장합니다. 원본 값과 결과 표는 바꾸지 않습니다.
- Excel은 '='로 시작하는 글자 값도 수식이 아닌 글자로 저장합니다(값은 그대로).
- CSV는 =, +, -, @로 시작하는 글자 값 앞에 작은따옴표(')를 붙여 표 프로그램이 수식으로 실행하지 않게 합니다.
"""

import io
import math
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from auditlens.detection import (
    OFF_NOTE, PERIOD_LABEL, PRIORITY_GROUP, REVIEW_LABEL, RULES, SUPPORT_GROUP, SUPPORT_LABEL,
)
from auditlens.schema import COLUMN_LABELS

KST = timezone(timedelta(hours=9), "KST")  # 한국은 일광절약시간이 없어 고정 +9시간으로 충분합니다.

# 화면(5단계)과 파일에 같은 문구를 쓰기 위해 여기에 둡니다.
REVIEW_NOTICE = (
    f"여기 표시되는 거래는 '{REVIEW_LABEL}'입니다. 정해진 기준에서 벗어나 감사인이 먼저 살펴볼 만한 거래라는 뜻이며, "
    "오류나 부정으로 확정한 것이 아닙니다. 각 거래의 탐지 이유와 판단 근거, 규칙의 한계를 함께 확인해 주세요."
)
MISS_NOTICE = ("탐지되지 않은 거래가 정상 거래라는 뜻도 아닙니다. 규칙은 정해진 몇 가지 패턴만 보며, 기준에 걸리지 않는 방식의 "
               "오류나 부정은 찾지 못합니다.")
COMMON_LIMITS = (
    "공통 한계: 탐지 기준은 올린 파일 안의 비교에 근거하며 회사의 업무 내용, 증빙, 내부통제를 알지 못합니다. "
    "탐지되지 않은 거래가 문제가 없다는 뜻이 아니며(미탐), 탐지된 거래 대부분은 정상일 수 있습니다(오탐). "
    "감사기준서 240의 관련 문단은 원문을 확인한 뒤 인용할 예정이며, 이 화면에는 아직 인용하지 않았습니다."
)

NOTE_COLUMN = "안내"
LIST_COLUMN = "목록"
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
EXCEL_MAX_ROWS = 1_048_576  # 머리글 포함
EXCEL_MAX_CELL_CHARS = 32_767
CUT_MARK = " …(Excel 한 칸 글자 수 한도를 넘어 이하 생략. CSV 파일에는 전체가 있습니다.)"

# (시트 이름, CSV 파일 이름, 설명). 시트 이름은 31자 이하이며 사용자 값으로 만들지 않습니다.
SHEETS = [
    ("안내", "00_안내.txt", "판정의 뜻, 한계, 원본 파일과 생성 정보, 시트 설명"),
    ("적용 기준", "01_적용기준.csv", "이 파일을 만들 때 적용한 탐지 기준(기본값, 적용값, 기본값과 다른 항목, 꺼진 규칙)"),
    ("우선 검토", "02_우선검토.csv", "종합(전표별) 우선 검토 목록. 화면과 같은 순서"),
    ("보조 목록", "03_보조목록.csv", f"수기·조정 여부(R4(a))만 해당하는 전표. 판정은 '{SUPPORT_LABEL}'"),
    ("탐지 사유", "04_탐지사유.csv", "종합 전표의 탐지 사유를 규칙·라인마다 한 행씩"),
    ("R1", "05_R1.csv", "R1 결과 (분개 라인)"),
    ("R2(a)", "06_R2a.csv", "R2(a) 결과 (분개 라인)"),
    ("R2(b) 이상 기간", "07_R2b_이상기간.csv", f"R2(b) 결과. 판정은 '{PERIOD_LABEL}'이며 개별 거래 판정이 아님"),
    ("R3", "08_R3.csv", "R3 결과 (전표)"),
    ("R4", "09_R4.csv", "R4 결과 (전표). 보조 목록 전표도 포함"),
    ("R5", "10_R5.csv", "R5 결과 (거래처·계정 조합)"),
    ("R6", "11_R6.csv", "R6 결과 (전표 쌍)"),
    ("비교 제외 라인", "12_비교제외라인.csv", "규칙 비교에서 빼거나 비교하지 않은 라인과 R5 참고 후보(미탐 가능성 확인용)"),
    ("검토 대상 전표 라인", "13_검토대상전표라인.csv", "우선 검토·보조 목록 전표의 전체 라인과 파일 행 번호"),
    ("검증 결과", "14_검증결과.csv", "3단계 검증 결과와 분석에서 제외한 행"),
]
SHEET_NAMES = [s[0] for s in SHEETS]


def _note(text):
    return pd.DataFrame({NOTE_COLUMN: [text]})


def _rule_sheet(code, table, det):
    """꺼진 규칙·검사하지 않은 규칙·결과 없음을 서로 구분해 적습니다."""
    if code in det.settings.disabled_rules():
        return _note(f"{code} 규칙이 꺼져 있어 결과가 없습니다. {OFF_NOTE} 해당 거래가 없다는 뜻은 아닙니다.")
    if code == "R5" and det.settings.r5_threshold is None:
        return _note("R5 기준금액을 입력하지 않아 검사하지 않았습니다. 해당 거래가 없다는 뜻은 아닙니다.")
    if table.empty:
        return _note(f"현재 기준으로 {code}에 해당하는 결과가 없습니다. 탐지되지 않았다고 문제가 없다는 뜻은 아닙니다.")
    return table


def _stack(parts, empty_text):
    """열 구성이 다른 표들을 '목록' 열로 구분해 한 시트에 이어 붙입니다."""
    parts = [t.assign(**{LIST_COLUMN: name})[[LIST_COLUMN, *t.columns]] for name, t in parts if len(t)]
    if not parts:
        return _note(empty_text)
    return pd.concat(parts, ignore_index=True)


def review_lines(df, summary):
    """우선 검토·보조 목록 전표의 전체 라인. 종합과 같은 전표 순서, 전표 안에서는 파일 행 순서입니다.

    값은 분석에 쓴 그대로이며, 열은 사용자가 연결한 표준 열만 나옵니다(연결하지 않은 선택 열은 없음).
    """
    cols = ["file_row"] + [c for c in COLUMN_LABELS if c in df.columns]
    rank = {je: i for i, je in enumerate(summary["전표번호"])}
    group = dict(zip(summary["전표번호"], summary["검토 구분"]))
    lines = df.loc[df["je_id"].isin(rank), cols].copy()
    lines = lines.assign(_rank=lines["je_id"].map(rank)).sort_values(["_rank", "file_row"], kind="stable")
    lines.insert(0, "검토 구분", lines["je_id"].map(group))
    lines = lines.drop(columns="_rank").rename(columns={**COLUMN_LABELS, "file_row": "파일 행 번호"})
    front = ["검토 구분", "전표번호", "파일 행 번호"]
    return lines[front + [c for c in lines.columns if c not in front]].reset_index(drop=True)


def _validation_sheet(validation):
    excluded = validation.excluded_detail if validation.excluded_detail is not None else pd.DataFrame()
    excluded = excluded.rename(columns={**COLUMN_LABELS, "file_row": "파일 행 번호"})
    return _stack([("검증 결과", validation.issues_table()), ("분석에서 제외한 행", excluded)],
                  "검증 경고와 분석에서 제외한 행이 없습니다.")


def _period_text(df):
    dates = pd.to_datetime(df["posting_date"]) if len(df) else pd.Series(dtype="datetime64[ns]")
    if dates.dropna().empty:
        return "확인할 수 없음"
    return f"{dates.min():%Y-%m-%d} ~ {dates.max():%Y-%m-%d} (회계처리일 기준)"


def _rule_notes(det):
    off = set(det.settings.disabled_rules())
    rows = []
    if "R1" not in off and len(det.r1_skipped):
        codes = ", ".join(f"{r.계정코드}({r.방향})" for r in det.r1_skipped.itertuples(index=False))
        rows.append(("참고: R1", f"라인 수가 최소 건수보다 적어 검사하지 않은 계정·방향 {len(det.r1_skipped)}개: {codes}"))
    for code, notes in [("R3", det.r3_notes), ("R4", det.r4_notes), ("R5", det.r5_notes), ("R6", det.r6_notes)]:
        for n in notes or []:
            if n != OFF_NOTE:
                rows.append((f"참고: {code}", n))
    return rows


def notice_table(det, validation, df, source_name, created_at, analysis_notes=()):
    """첫 시트 '안내'. 항목·내용 두 열입니다."""
    s = det.settings
    off = s.disabled_rules()
    summary = det.summary
    n_priority = int((summary["검토 구분"] == PRIORITY_GROUP).sum())
    n_support = int((summary["검토 구분"] == SUPPORT_GROUP).sum())
    n_periods = "꺼짐" if "R2(b)" in off else f"{det.r2b[['계정코드', '연월']].drop_duplicates().shape[0]:,}개"
    vs = validation.summary or {}
    rows = [
        ("이 파일", "AuditLens가 만든 검토 결과 파일입니다. 감사인의 추가 검토 대상 선정을 돕는 자료이며, 부정이나 회계 오류를 "
                  "확정하지 않고 감사인의 전문가적 판단을 대체하지 않습니다."),
        ("판정의 뜻", REVIEW_NOTICE),
        ("미탐", MISS_NOTICE),
        ("보조 목록", f"수기·조정 전표라는 사실만으로는 오류나 부정을 뜻하지 않습니다. 수기·조정 여부(R4(a)) 하나만 해당하는 전표는 "
                  f"'보조 목록' 시트에 따로 두었고 판정은 '{SUPPORT_LABEL}'입니다. 순서는 해당 규칙 수, 해당 세부 조건 수가 많은 "
                  "순서이며 점수나 등급을 매기지 않습니다."),
        ("R2(b)", f"R2(b)는 개별 거래가 아니라 계정과 월의 조합에 대한 징후입니다(판정: {PERIOD_LABEL}). 종합 전표 수에 "
                  "들어가지 않습니다. '이 달 값'과 '다른 달 중앙값'의 단위는 '항목'이 라인 수이면 건, 금액이면 원입니다."),
        ("공통 한계", COMMON_LIMITS.replace("이 화면에는", "이 파일에는")),
        *[(f"한계: {code} {r['이름']}", r["한계"]) for code, r in RULES.items()],
        *_rule_notes(det),
        ("원본 파일", source_name or "(이름 없음)"),
        ("파일 내용을 만든 시각", f"{created_at:%Y-%m-%d %H:%M} (한국 시간)"),
        ("전체 행", f"{vs.get('전체 행', len(df)):,}"),
        ("분석 대상 행", f"{len(df):,} (검증에서 제외된 행은 탐지 대상이 아님)"),
        ("검증에서 제외한 행", f"{len(validation.excluded_rows):,}"),
        *[(f"분석 제외: {n['항목']}", f"{n['행 수']:,}행. {n['처리']}") for n in analysis_notes],
        ("분석 기간", _period_text(df)),
        ("우선 검토 전표", f"{n_priority:,}개"),
        ("보조 목록 전표", f"{n_support:,}개"),
        ("검토 대상 기간 (계정-월)", n_periods),
        ("기본값과 다른 기준", f"{len(s.changed())}개 (자세한 값은 '적용 기준' 시트)"),
        ("꺼진 규칙", ", ".join(off) if off else "없음"),
        ("금액 단위", "원. 금액은 숫자로 저장했습니다."),
        ("파일 행 번호", "올린 파일에서의 행 번호입니다(머리글이 1행)."),
        ("수식 방지", "Excel 파일은 '='로 시작하는 글자도 값 그대로 글자로 저장했습니다. CSV 파일은 =, +, -, @로 시작하는 글자 "
                   "값 앞에 작은따옴표(')를 붙였습니다(숫자 칸은 그대로). 원본과 대조할 때 이 한 글자를 빼고 보세요."),
        ("CSV를 Excel로 열 때", "CSV 파일을 Excel로 바로 열면 계정코드·전표번호의 앞자리 0이 사라지거나 긴 숫자가 바뀌어 보일 수 "
                             "있습니다. 값을 그대로 보려면 Excel 파일(.xlsx)을 쓰세요."),
        *[(f"시트: {name}", desc) for name, _, desc in SHEETS],
    ]
    return pd.DataFrame(rows, columns=["항목", "내용"])


def build_tables(det, validation, df, source_name, created_at, analysis_notes=()):
    """시트 이름 → 표. 순서는 SHEETS와 같고 첫 표는 '안내'입니다."""
    summary = det.summary
    off = set(det.settings.disabled_rules())
    tables = {
        "안내": notice_table(det, validation, df, source_name, created_at, analysis_notes),
        "적용 기준": det.settings.as_table(),
        "우선 검토": summary[summary["검토 구분"] == PRIORITY_GROUP].reset_index(drop=True),
        "보조 목록": summary[summary["검토 구분"] == SUPPORT_GROUP].reset_index(drop=True),
        "탐지 사유": det.reasons.reset_index(drop=True),
        "R1": _rule_sheet("R1", det.r1, det),
        "R2(a)": _rule_sheet("R2(a)", det.r2a, det),
        "R2(b) 이상 기간": _rule_sheet("R2(b)", det.r2b, det),
        "R3": _rule_sheet("R3", det.r3, det),
        "R4": _rule_sheet("R4", det.r4, det),
        "R5": _rule_sheet("R5", det.r5, det),
        "R6": _rule_sheet("R6", det.r6, det),
        "비교 제외 라인": _stack(
            [("R1 금액 비교에서 뺀 라인 (0원·음수·차대 동시 기재)", det.r1_excluded if "R1" not in off else pd.DataFrame()),
             ("R2(a) 비교하지 않은 라인", det.r2a_skipped if "R2(a)" not in off else pd.DataFrame()),
             ("R5 판단 기간에 처음 나타난 거래처·계정 조합 (기준금액 미만 포함, 참고용)",
              det.r5_candidates if "R5" not in off else pd.DataFrame())],
            "비교에서 뺀 라인이 없습니다." + (f" 꺼진 규칙({', '.join(sorted(off))})은 계산하지 않았습니다." if off else "")),
        "검토 대상 전표 라인": review_lines(df, summary) if len(summary) else _note("검토 대상 전표가 없습니다."),
        "검증 결과": _validation_sheet(validation),
    }
    for name in ["우선 검토", "보조 목록", "탐지 사유"]:
        if tables[name].empty:
            tables[name] = _note(f"현재 기준으로 '{name}'에 해당하는 전표가 없습니다.")
    return {name: tables[name] for name in SHEET_NAMES}


# ---------------------------------------------------------------- 파일 쓰기

def _plain(v):
    """표 값 하나를 파일에 쓸 값으로. 빈 값은 None, 날짜는 datetime, 숫자는 숫자 그대로입니다."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.datetime64):
        return None if np.isnat(v) else pd.Timestamp(v).to_pydatetime()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (str, int, float, bool, datetime, date)):
        return v
    return str(v)


def _date_format(column):
    return "yyyy-mm-dd hh:mm:ss" if "입력일시" in str(column) else "yyyy-mm-dd"


def to_excel_bytes(tables):
    wb = Workbook()
    wb.remove(wb.active)
    for name, table in tables.items():
        ws = wb.create_sheet(name)
        if len(table) + 1 > EXCEL_MAX_ROWS:
            table = _note(f"행이 {len(table):,}개로 Excel 한 시트의 한도를 넘어 이 시트에는 담지 못했습니다. CSV 묶음 파일을 "
                          "이용해 주세요.")
        columns = list(table.columns)
        ws.append([str(c) for c in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        formats = [_date_format(c) for c in columns]
        for r, row in enumerate(table.itertuples(index=False, name=None), start=2):
            for c, value in enumerate(row, start=1):
                value = _plain(value)
                if value is None:
                    continue
                cell = ws.cell(r, c)
                if isinstance(value, str):
                    if len(value) > EXCEL_MAX_CELL_CHARS:
                        value = value[:EXCEL_MAX_CELL_CHARS - len(CUT_MARK)] + CUT_MARK
                    cell.value = value
                    cell.data_type = "s"  # '='로 시작해도 수식이 아닌 글자로 저장
                else:
                    cell.value = value
                    if isinstance(value, (datetime, date)):
                        cell.number_format = formats[c - 1]
        ws.freeze_panes = "A2"
        for c, col in enumerate(columns, start=1):
            letter = ws.cell(1, c).column_letter
            wide = col in ("탐지 이유", "내용", NOTE_COLUMN, "제외 사유", "문제", "수정 방법", "설명")
            ws.column_dimensions[letter].width = 80 if wide else min(max(len(str(col)) * 2 + 2, 12), 40)
            if wide:
                for cell in ws.iter_cols(min_col=c, max_col=c, min_row=2):
                    for x in cell:
                        x.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_safe(table):
    """CSV용 사본. =, +, -, @로 시작하는 글자 값 앞에 작은따옴표를 붙입니다(숫자·날짜는 그대로, 원래 표는 바꾸지 않음)."""
    out = table.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = [f"'{v}" if isinstance(v, str) and v.startswith(CSV_FORMULA_PREFIXES) else v for v in out[col]]
    return out


def notice_text(notice):
    return "\n\n".join(f"[{r.항목}]\n{r.내용}" for r in notice.itertuples(index=False)) + "\n"


def to_csv_zip_bytes(tables):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, file_name, _ in SHEETS:
            table = tables[name]
            if name == "안내":
                zf.writestr(file_name, notice_text(table).encode("utf-8-sig"))
            else:
                zf.writestr(file_name, csv_safe(table).to_csv(index=False).encode("utf-8-sig"))
    return buf.getvalue()


def export_file_name(source_name, created_at, ext):
    """예: AuditLens_검토결과_분개장_20261105_1230.xlsx (시각은 한국 시간)."""
    stem = Path(re.sub(r'[\\/:*?"<>|\s]+', "_", source_name or "")).stem.strip("._") or "파일"
    return f"AuditLens_검토결과_{stem}_{created_at:%Y%m%d_%H%M}.{ext}"


def now_kst():
    return datetime.now(KST)
