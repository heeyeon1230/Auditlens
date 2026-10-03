"""업로드한 CSV·Excel 파일을 읽어 표(DataFrame)로 만듭니다.

이 단계에서는 값을 고치거나 형식을 바꾸지 않고 파일에 적힌 그대로 읽습니다.
형식 검사와 변환은 validation.py가 맡습니다.
"""

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

    df.columns = [str(c).strip() for c in df.columns]
    return df, info


def _read_csv(data: bytes):
    last_error = None
    for encoding in CSV_ENCODINGS:
        try:
            # dtype=str: 계정코드 앞자리 0, 금액의 쉼표 등을 파일에 적힌 그대로 보존
            df = pd.read_csv(io.BytesIO(data), dtype=str, encoding=encoding,
                             keep_default_na=False, skipinitialspace=True)
            return df, {"format": "CSV", "encoding": encoding, "sheet": None}
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
