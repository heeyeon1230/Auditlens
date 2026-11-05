"""업로드한 CSV·Excel 파일을 읽어 표(DataFrame)로 만듭니다.

이 단계에서는 값을 고치거나 형식을 바꾸지 않고 파일에 적힌 그대로 읽습니다.
형식 검사와 변환은 validation.py가 맡습니다.
"""

import csv
import io

import pandas as pd

CSV_ENCODINGS = ["utf-8-sig", "cp949"]  # UTF-8, 한글 윈도우 엑셀 기본 저장 형식 순서로 시도


class LoadError(Exception):
    """파일을 읽을 수 없을 때 사용자에게 보여 줄 메시지를 담는 오류."""


def list_excel_sheets(data: bytes):
    """Excel 파일의 시트 이름 목록을 돌려줍니다."""
    try:
        return pd.ExcelFile(io.BytesIO(data), engine="openpyxl").sheet_names
    except Exception as exc:  # 손상된 파일, 암호 걸린 파일 등
        raise LoadError(
            "Excel 파일을 열 수 없습니다. 파일이 손상되었거나 암호가 걸려 있을 수 있습니다. "
            "Excel에서 파일을 열어 암호를 해제한 뒤 .xlsx로 다시 저장해 주세요."
        ) from exc


def read_uploaded_file(file_name: str, data: bytes, sheet_name=None):
    """파일 이름의 확장자를 보고 CSV 또는 Excel로 읽습니다.

    반환값: (DataFrame, 정보 dict)  정보에는 형식, 인코딩, 시트 이름이 들어 있습니다.
    읽을 수 없으면 LoadError를 일으킵니다.
    """
    lower = file_name.lower()
    if not data:
        raise LoadError("빈 파일입니다. 내용이 있는 분개장 파일을 올려 주세요.")

    if lower.endswith(".csv"):
        df, info = _read_csv(data)
    elif lower.endswith((".xlsx", ".xlsm")):
        df, info = _read_excel(data, sheet_name)
    elif lower.endswith(".xls"):
        raise LoadError(
            "구형 Excel 형식(.xls)은 지원하지 않습니다. Excel에서 파일을 열고 "
            "[다른 이름으로 저장] → 'Excel 통합 문서(*.xlsx)'로 저장한 뒤 다시 올려 주세요."
        )
    else:
        raise LoadError(
            f"지원하지 않는 파일 형식입니다: {file_name}. CSV(.csv) 또는 Excel(.xlsx) 파일을 올려 주세요."
        )

    names, renamed = _unique_names([str(c).strip() for c in df.columns])
    df.columns = names
    if renamed:
        info["renamed"] = renamed
    return df, info


def _unique_names(names):
    """앞뒤 공백을 지운 열 이름이 겹치면 뒤의 열에 '.1', '.2'를 붙여 구분합니다(pandas가 같은 이름에 붙이는 방식과 같음).

    같은 이름의 열이 둘 이상이면 열 연결에서 한 열을 고를 수 없어 화면이 멈추므로, 값은 그대로 두고 이름만 구분합니다.
    반환값: (새 이름 목록, [(원래 이름, 바꾼 이름), ...])
    """
    existing, used, out, renamed = set(names), set(), [], []
    for name in names:
        new, n = name, 0
        while new in used or (new != name and new in existing):  # 파일에 이미 있는 이름과도 겹치지 않게 번호를 올림
            n += 1
            new = f"{name}.{n}"
        used.add(new)
        out.append(new)
        if new != name:
            renamed.append((name, new))
    return out, renamed


def _read_csv(data: bytes):
    last_error = None
    for encoding in CSV_ENCODINGS:
        try:
            trailing = _trailing_columns(data.decode(encoding))
            # dtype=str: 계정코드 앞자리 0, 금액의 쉼표 등을 파일에 적힌 그대로 보존
            # 행 끝의 빈 추가 칸이 있으면 머리글 열까지만 읽습니다(usecols). 추가 칸이 없으면 이전과 똑같이 읽습니다.
            extra = {"usecols": list(range(trailing["after"]))} if trailing else {}
            df = pd.read_csv(io.BytesIO(data), dtype=str, encoding=encoding,
                             keep_default_na=False, skipinitialspace=True, **extra)
            _check_delimiter(df)
            info = {"format": "CSV", "encoding": encoding, "sheet": None}
            if trailing:
                info["trimmed"] = trailing
            return df, info
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.EmptyDataError as exc:
            raise LoadError("CSV 파일에 열 이름(머리글) 행이 없습니다. 첫 줄에 열 이름을 넣어 주세요.") from exc
        except pd.errors.ParserError as exc:
            raise LoadError(
                "CSV 파일의 형식이 올바르지 않습니다. 행마다 열 개수가 같은지, "
                "쉼표가 들어간 값은 큰따옴표로 감쌌는지 확인해 주세요. "
                f"(상세: {exc})"
            ) from exc
    raise LoadError(
        "CSV 파일의 글자 인코딩을 알 수 없습니다. UTF-8 또는 CP949(Excel 기본 'CSV' 저장)로 저장해 주세요."
    ) from last_error


def _trailing_columns(text):
    """데이터 행이 머리글보다 열(칸)이 많은지 확인합니다.

    pandas는 모든 데이터 행 끝에 칸이 하나 더 있으면(예: 'J1,101,') 첫 열을 행 이름으로 써서 열이 한 칸씩 밀린 표를
    오류 없이 만듭니다. 이를 막기 위해 읽기 전에 칸 수를 셉니다.
    - 추가 칸이 없으면 None
    - 추가 칸이 모두 비어 있으면 {"before": 가장 많은 칸 수, "after": 머리글 칸 수, "rows": 추가 칸이 있는 행 수}
    - 추가 칸에 값이 하나라도 있으면 LoadError (어느 열이 어느 항목인지 알 수 없으므로 임의로 지우지 않음)
    """
    try:
        rows = csv.reader(io.StringIO(text, newline=""), skipinitialspace=True)
        header = next(rows, None)
        if not header:
            return None
        n = len(header)
        widest, n_rows, filled = n, 0, []
        for number, row in enumerate(rows, start=2):  # 파일 행 번호: 머리글이 1행
            if len(row) <= n:
                continue
            n_rows += 1
            widest = max(widest, len(row))
            if any(cell.strip() for cell in row[n:]):
                filled.append(number)
    except csv.Error:
        return None  # 따옴표가 맞지 않는 파일 등은 pandas가 읽으면서 기존 오류 안내를 냅니다.
    if not n_rows:
        return None
    if filled:
        shown = ", ".join(f"{r}행" for r in filled[:5]) + (f" 외 {len(filled) - 5}개" if len(filled) > 5 else "")
        raise LoadError(
            f"CSV 파일의 열 구조가 맞지 않습니다. 머리글(1행)은 {n}개 열인데, 머리글보다 열이 많고 그 추가 열에 값이 들어 있는 "
            f"행이 {len(filled)}개 있습니다(예: {shown}, 가장 많은 행은 {widest}개 열). 어느 값이 어느 열의 값인지 알 수 없어 "
            "추가 열을 지우지 않고 읽기를 멈췄습니다. 수정 방법: 머리글에 빠진 열 이름을 추가하거나, 해당 행의 값을 고쳐 열 개수를 "
            "머리글과 같게 맞춘 뒤 다시 올려 주세요. 쉼표가 들어간 값(예: 1,000)은 큰따옴표로 감싸야 합니다."
        )
    return {"before": widest, "after": n, "rows": n_rows}


def _check_delimiter(df):
    """열이 하나뿐이고 그 이름에 세미콜론(;)이나 탭이 있으면, 쉼표가 아닌 구분 기호로 저장된 파일로 보고 안내합니다.

    구분 기호를 추측해 다시 읽지는 않습니다. 열이 하나뿐이면 필수 열을 연결할 수 없어 어차피 분석할 수 없습니다.
    """
    if len(df.columns) != 1:
        return
    header = str(df.columns[0])
    for mark, name in ((";", "세미콜론(;)"), ("\t", "탭")):
        if mark in header:
            raise LoadError(
                f"CSV 파일의 열이 하나로만 읽혔습니다. 열 이름에 {name}이 있어 쉼표(,)가 아닌 {name}으로 열을 나눈 파일로 "
                "보입니다. 이 프로그램은 쉼표로 구분한 CSV를 읽습니다. Excel에서 파일을 열어 'CSV UTF-8(쉼표로 분리)'로 다시 "
                "저장하거나 .xlsx로 저장해 올려 주세요."
            )


def _read_excel(data: bytes, sheet_name):
    sheets = list_excel_sheets(data)
    if sheet_name is None:
        sheet_name = sheets[0]
    if sheet_name not in sheets:
        raise LoadError(f"'{sheet_name}' 시트가 없습니다. 파일에 있는 시트: {', '.join(sheets)}")
    try:
        df = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise LoadError(f"'{sheet_name}' 시트를 읽는 중 문제가 생겼습니다. (상세: {exc})") from exc
    return df, {"format": "Excel", "encoding": None, "sheet": sheet_name, "sheets": sheets}
