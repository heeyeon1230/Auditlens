"""가상 분개장 생성기 테스트."""

import io

import pandas as pd
import pytest

from auditlens.sample_data import (
    ACCOUNTS, CURRENT_YEAR, FICTIONAL_NOTICE, PRIOR_YEAR, generate_sample_journal,
    journal_to_csv_bytes, journal_to_excel_bytes,
)


@pytest.fixture(scope="module")
def sample():
    return generate_sample_journal()


def test_같은_시드면_같은_데이터(sample):
    journal, answer_key = sample
    again, again_key = generate_sample_journal()
    pd.testing.assert_frame_equal(journal, again)
    pd.testing.assert_frame_equal(answer_key, again_key)


def test_전기_당기_2개년(sample):
    journal, _ = sample
    years = set(journal["posting_date"].dt.year)
    assert years == {PRIOR_YEAR, CURRENT_YEAR}
    for year in years:
        months = journal.loc[journal["posting_date"].dt.year == year, "posting_date"].dt.month
        assert set(months) == set(range(1, 13))


def test_모든_전표_차대_균형(sample):
    journal, _ = sample
    totals = journal.groupby("je_id")[["debit", "credit"]].sum()
    assert (totals["debit"] == totals["credit"]).all()


def test_한_행에는_차변_또는_대변_하나만(sample):
    journal, _ = sample
    assert not ((journal["debit"] > 0) & (journal["credit"] > 0)).any()
    assert ((journal["debit"] > 0) | (journal["credit"] > 0)).all()


def test_제조업_주요_계정_포함(sample):
    journal, _ = sample
    names = set(journal["account_name"])
    for name in ["원재료", "재공품", "제품", "원재료비", "임금", "제품매출", "제품매출원가"]:
        assert name in names
    assert set(journal["account_code"]) <= set(ACCOUNTS)


def test_제조원가_흐름_원재료에서_매출원가까지(sample):
    journal, _ = sample
    flows = journal.groupby("description")["account_code"].apply(set)
    assert {"501", "131"} <= flows["1월 원재료 출고"]
    assert {"133", "501", "504"} <= flows["1월 제조원가 재공품 대체"]
    assert {"135", "133"} <= flows["1월 완성품 제품 대체"]
    assert {"455", "135"} <= flows["1월 제품 매출원가"]


def test_가상_데이터_표시(sample):
    journal, _ = sample
    assert journal["je_id"].str.startswith("SIM-").all()
    assert "가상" in FICTIONAL_NOTICE
    excel = journal_to_excel_bytes(journal)
    notice = pd.read_excel(io.BytesIO(excel), sheet_name="안내")
    assert "가상" in " ".join(notice["안내"].astype(str))


def test_정답표의_전표가_실제로_존재(sample):
    journal, answer_key = sample
    ids = set(journal["je_id"])
    for _, row in answer_key.iterrows():
        for je_id in row["전표번호 전체"].split(", "):
            assert je_id in ids, f"{row['시나리오']}의 전표 {je_id}가 분개장에 없습니다"
    assert set(answer_key["시나리오"]) >= {f"A0{i}" for i in range(1, 10)}


def test_정답표는_부정_오류로_단정하지_않음(sample):
    _, answer_key = sample
    text = " ".join(answer_key.astype(str).values.ravel())
    for word in ["부정", "횡령", "분식", "오류"]:
        assert word not in text


def test_테스트_패턴_내용_확인(sample):
    journal, answer_key = sample
    ids = dict(zip(answer_key["시나리오"], answer_key["전표번호 전체"]))
    a01 = journal[journal["je_id"] == ids["A01"]]
    assert a01["debit"].max() == 46_800_000
    a04 = journal[journal["je_id"] == ids["A04"]].iloc[0]
    assert a04["posting_date"] == pd.Timestamp("2025-12-31")
    assert a04["entry_date"] > pd.Timestamp("2025-12-31 23:59")
    a05 = journal[journal["je_id"] == ids["A05"]].iloc[0]
    assert a05["entry_date"].weekday() == 5  # 토요일
    a06 = journal[journal["je_id"] == ids["A06"]].iloc[0]
    assert a06["created_by"] == a06["approved_by"]
    a07 = journal[journal["je_id"] == ids["A07"]].iloc[0]
    assert a07["description"] == ""
    first_seen = journal[journal["vendor"] == "신규컨설팅(가상)"]["posting_date"].min()
    assert first_seen >= pd.Timestamp("2025-11-01")
    a09 = journal[journal["je_id"].isin(ids["A09"].split(", ")) & (journal["debit"] > 0)]
    assert a09["debit"].nunique() == 1 and len(a09) == 2


def test_CSV_저장_후_다시_읽기(sample):
    journal, _ = sample
    df = pd.read_csv(io.BytesIO(journal_to_csv_bytes(journal)), encoding="utf-8-sig", dtype=str)
    assert len(df) == len(journal)
    assert list(df.columns)[:6] == ["전표번호", "회계처리일", "입력일시", "계정코드", "계정과목명", "차변금액"]
