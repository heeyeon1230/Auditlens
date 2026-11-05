"""파일 읽기 테스트."""

import io

import pandas as pd
import pytest

from auditlens.loader import LoadError, list_excel_sheets, read_uploaded_file


def _csv(text, encoding="utf-8-sig"):
    return text.encode(encoding)


def test_UTF8_CSV():
    df, info = read_uploaded_file("a.csv", _csv("전표번호,차변금액\nJ1,\"1,000\"\n"))
    assert info["encoding"] == "utf-8-sig"
    assert df.loc[0, "차변금액"] == "1,000"  # 원본 그대로 읽음


def test_CP949_CSV_자동_인식():
    df, info = read_uploaded_file("a.csv", _csv("전표번호,계정과목명\nJ1,보통예금\n", "cp949"))
    assert info["encoding"] == "cp949"
    assert df.loc[0, "계정과목명"] == "보통예금"


def test_계정코드_앞자리_0_보존():
    df, _ = read_uploaded_file("a.csv", _csv("계정코드\n0103\n"))
    assert df.loc[0, "계정코드"] == "0103"


def test_열_이름_앞뒤_공백_제거():
    df, _ = read_uploaded_file("a.csv", _csv(" 전표번호 ,차변금액\nJ1,1\n"))
    assert list(df.columns) == ["전표번호", "차변금액"]


def test_Excel_시트_선택():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"안내": ["가상"]}).to_excel(writer, sheet_name="안내", index=False)
        pd.DataFrame({"전표번호": ["J1"], "계정코드": [103]}).to_excel(writer, sheet_name="분개장", index=False)
    data = buffer.getvalue()
    assert list_excel_sheets(data) == ["안내", "분개장"]
    df, info = read_uploaded_file("a.xlsx", data, "분개장")
    assert info["sheet"] == "분개장" and df.loc[0, "전표번호"] == "J1"
    with pytest.raises(LoadError, match="시트가 없습니다"):
        read_uploaded_file("a.xlsx", data, "없는시트")


@pytest.mark.parametrize("name, data, message", [
    ("a.xls", b"x", "구형 Excel"),
    ("a.txt", b"x", "지원하지 않는 파일 형식"),
    ("a.csv", b"", "빈 파일"),
    ("a.xlsx", b"not an excel file", "Excel 파일을 열 수 없습니다"),
])
def test_읽을_수_없는_파일은_안내_메시지(name, data, message):
    with pytest.raises(LoadError, match=message):
        read_uploaded_file(name, data)


# ---------------------------------------------------------------- 5단계: 열 이름 겹침, 구분 기호
def test_앞뒤_공백만_다른_같은_이름의_열은_번호를_붙여_구분():
    df, info = read_uploaded_file("a.csv", _csv("전표번호,적요,적요 \nJ1,가,나\n"))
    assert list(df.columns) == ["전표번호", "적요", "적요.1"]
    assert info["renamed"] == [("적요", "적요.1")]
    assert df.loc[0, "적요.1"] == "나"  # 값은 그대로


def test_번호를_붙일_때_파일에_있는_이름과_겹치지_않음():
    df, info = read_uploaded_file("a.csv", _csv("적요,적요 ,적요.1\n가,나,다\n"))
    assert list(df.columns) == ["적요", "적요.2", "적요.1"]


def test_열_이름이_겹치지_않으면_바꾸지_않음():
    _, info = read_uploaded_file("a.csv", _csv("전표번호,적요\nJ1,가\n"))
    assert "renamed" not in info


@pytest.mark.parametrize("sep, name", [(";", "세미콜론"), ("\t", "탭")])
def test_쉼표가_아닌_구분_기호로_저장한_CSV는_안내(sep, name):
    text = sep.join(["전표번호", "회계처리일", "계정코드"]) + "\n" + sep.join(["J1", "2025-01-01", "101"]) + "\n"
    with pytest.raises(LoadError, match=name):
        read_uploaded_file("a.csv", _csv(text))


def test_열이_하나뿐인_정상_CSV는_그대로_읽음():
    df, _ = read_uploaded_file("a.csv", _csv("전표번호\nJ1\n"))
    assert list(df.columns) == ["전표번호"]
