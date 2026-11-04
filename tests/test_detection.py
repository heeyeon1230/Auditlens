"""탐지 규칙 R1~R3 단위 테스트.

업종과 무관하게 동작하는지 보기 위해, 샘플 제조업 데이터가 아닌 임의 계정코드·계정명
(예: 'X100 출장비')으로 만든 작은 표를 씁니다. 마지막 묶음만 샘플 데이터의 정답표를 확인합니다.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from auditlens.analysis import prepare
from auditlens.detection import (
    PERIOD_LABEL, REVIEW_LABEL, DetectionSettings, business_days_between, closing_date, r1_amount_outliers,
    r2_monthly_pattern, r2_vendor_history, r3_timing, run_detection,
)


def journal(entries):
    """entries: (전표번호, 회계처리일, [(계정코드, 계정명, 차변, 대변)], 추가 열 사전) 목록 → 분석용 표."""
    rows = []
    for je, day, lines, extra in entries:
        for code, name, dr, cr in lines:
            rows.append({"je_id": je, "posting_date": pd.Timestamp(day), "account_code": code,
                         "account_name": name, "debit": float(dr), "credit": float(cr), **extra})
    raw = pd.DataFrame(rows)
    raw.insert(0, "file_row", range(2, len(raw) + 2))
    df, _ = prepare(raw)
    return df


def pair(je, day, amount, debit=("X100", "출장비"), credit=("Y900", "현금성자산"), **extra):
    return (je, day, [(*debit, amount, 0), (*credit, 0, amount)], extra)


def spread(n, base=100_000, start=date(2025, 1, 6)):
    """평범한 금액 n건, 매일 하나씩. 로그 기준으로 기준 금액의 10^-0.2~10^0.2배(약 0.63~1.58배)에 고르게 퍼뜨립니다."""
    return [(start + timedelta(days=i), int(base * 10 ** (-0.2 + 0.4 * i / max(n - 1, 1)))) for i in range(n)]


# ---------------------------------------------------------------- R1
def test_r1_flags_line_far_above_account_distribution():
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(100))]
    entries.append(pair("BIG", date(2025, 6, 2), 2_000_000))  # 중앙값의 약 20배
    hits, skipped, _ = r1_amount_outliers(journal(entries))
    flagged = hits[hits["계정코드"] == "X100"]
    assert flagged["전표번호"].tolist() == ["BIG"]
    row = flagged.iloc[0]
    assert row["판정"] == REVIEW_LABEL
    assert row["방향"] == "차변" and row["금액"] == 2_000_000 and row["비교 라인 수"] == 101
    assert row["상한 금액"] < 2_000_000 < row["상한 금액"] * 20
    assert "중앙값" in row["탐지 이유"] and "상한" in row["탐지 이유"]


def test_r1_does_not_flag_ordinary_large_line():
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(100))]
    entries.append(pair("SLIGHT", date(2025, 6, 2), 120_000))  # 중앙값의 1.2배: 정상 범위
    hits = r1_amount_outliers(journal(entries))[0]
    assert hits.empty


def test_r1_skips_small_account_and_reports_it():
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(10))]
    entries.append(pair("BIG", date(2025, 6, 2), 50_000_000))
    hits, skipped, _ = r1_amount_outliers(journal(entries))
    assert hits.empty
    assert set(skipped["계정코드"]) == {"X100", "Y900"}
    assert (skipped["라인 수"] == 11).all() and skipped["사유"].str.contains("30건 미만").all()


def test_r1_compares_debit_and_credit_separately():
    # 같은 계정에서 차변은 소액 100건, 대변은 큰 금액 40건. 방향을 섞으면 대변이 모두 걸리게 됩니다.
    entries = [("D%d" % i, d, [("A1", "미지급비용", a, 0), ("Z1", "현금", 0, a)], {})
               for i, (d, a) in enumerate(spread(100))]
    entries += [("C%d" % i, d, [("Z1", "현금", a, 0), ("A1", "미지급비용", 0, a)], {})
                for i, (d, a) in enumerate(spread(40, base=10_000_000))]
    hits = r1_amount_outliers(journal(entries))[0]
    assert hits.empty


def test_r1_zero_negative_and_two_sided_lines_are_listed_not_dropped():
    """로그로 바꿀 수 없는 라인은 비교에서 빼되, 금액을 바꾸지 않고 사유와 함께 따로 돌려줘야 합니다."""
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(100))]
    entries.append(pair("BIG", date(2025, 6, 2), 2_000_000))
    entries.append(("REV", date(2025, 6, 3), [("X100", "출장비", -2_000_000, 0), ("Y900", "현금성자산", 0, -2_000_000)], {}))
    entries.append(("ZERO", date(2025, 6, 4), [("X100", "출장비", 0, 0), ("Y900", "현금성자산", 0, 0)], {}))
    entries.append(("BOTH", date(2025, 6, 5), [("X100", "출장비", 500, 300), ("Y900", "현금성자산", 0, 200)], {}))
    hits, skipped, excluded = r1_amount_outliers(journal(entries))
    # 정상 비교는 그대로: 탐지는 BIG뿐이고 비교 라인 수에 제외 라인이 섞이지 않음
    x100 = hits[hits["계정코드"] == "X100"]
    assert x100["전표번호"].tolist() == ["BIG"] and x100.iloc[0]["비교 라인 수"] == 101
    # 제외 라인 5개가 사유와 함께 남아 있고, 음수 금액은 절대값으로 바뀌지 않음
    by = {(r["전표번호"], r["계정코드"]): r for r in excluded.to_dict("records")}
    assert len(excluded) == 5
    assert by[("REV", "X100")]["금액"] == -2_000_000 and by[("REV", "X100")]["방향"] == "차변"
    assert "음수 금액" in by[("REV", "X100")]["제외 사유"]
    assert by[("REV", "Y900")]["방향"] == "대변"
    assert "0원" in by[("ZERO", "X100")]["제외 사유"]
    assert "차변과 대변이 모두" in by[("BOTH", "X100")]["제외 사유"]
    assert ("BOTH", "Y900") not in by  # 대변 200원만 있는 정상 라인은 비교 대상


def test_r1_settings_change_threshold():
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(100))]
    entries.append(pair("MID", date(2025, 6, 2), 300_000))  # 중앙값의 3배
    df = journal(entries)
    assert r1_amount_outliers(df)[0].empty
    loose = r1_amount_outliers(df, DetectionSettings(r1_multiplier=1.0))[0]
    assert "MID" in loose["전표번호"].tolist()


# ---------------------------------------------------------------- R2 (a)
def vendor_history(amounts, vendor="거래처A", start=date(2025, 1, 6)):
    return [pair(f"V{i}", start + timedelta(days=7 * i), a, vendor=vendor) for i, a in enumerate(amounts)]


def test_r2a_flags_amount_far_above_vendor_history():
    entries = vendor_history([1_000_000, 1_100_000, 900_000, 1_050_000, 950_000, 10_000_000])
    hits = r2_vendor_history(journal(entries))[0]
    debit_hits = hits[hits["방향"] == "차변"]
    assert debit_hits["전표번호"].tolist() == ["V5"]
    row = debit_hits.iloc[0]
    assert row["이전 거래 수"] == 5 and row["이전 거래 중앙값"] == 1_000_000
    assert row["중앙값 대비 배수"] == pytest.approx(10)
    assert row["이전 거래 기간"] == "2025-01-06 ~ 2025-02-03"
    assert "거래처A" in row["탐지 이유"] and "기준 5건 이상" in row["탐지 이유"]
    assert "5배(5,000,000원) 초과" in row["탐지 이유"]


def test_r2a_exactly_five_times_median_is_not_flagged():
    """기준은 중앙값의 5배 '초과'이므로 정확히 5배는 탐지하지 않습니다."""
    hits = r2_vendor_history(journal(vendor_history([1_000_000] * 5 + [5_000_000])))[0]
    assert hits.empty
    hits = r2_vendor_history(journal(vendor_history([1_000_000] * 5 + [5_000_001])))[0]
    assert len(hits[hits["방향"] == "차변"]) == 1


def test_r2a_uses_only_earlier_lines_as_history():
    """6번째 라인은 앞선 5건과 비교되고, 같은 묶음의 뒤쪽 거래는 비교 대상에 들어가지 않습니다."""
    amounts = [1_000_000] * 5 + [6_000_000] + [50_000_000] * 10
    hits = r2_vendor_history(journal(vendor_history(amounts)))[0]
    debit = hits[hits["방향"] == "차변"].set_index("전표번호")
    assert debit.loc["V5", "이전 거래 수"] == 5 and debit.loc["V5", "이전 거래 중앙값"] == 1_000_000


def test_r2a_does_not_flag_within_ratio():
    entries = vendor_history([1_000_000, 1_100_000, 900_000, 1_050_000, 950_000, 4_900_000])
    hits = r2_vendor_history(journal(entries))[0]
    assert hits.empty


def test_r2a_skips_when_prior_history_too_short():
    entries = vendor_history([1_000_000, 1_000_000, 1_000_000, 1_000_000, 50_000_000])  # 이전 거래 4건
    hits, status, skipped = r2_vendor_history(journal(entries))
    assert hits.empty
    counts = status.set_index("구분")["라인 수"]
    assert counts["이전 거래 부족"] == 10 and counts["비교함"] == 0  # 차변·대변 각 5줄
    assert skipped["제외 사유"].str.contains("기준 5건 미만").all()
    assert "이전 거래 4건" in skipped.iloc[-1]["제외 사유"]


def test_r2a_same_day_lines_are_not_prior_history():
    day = date(2025, 3, 3)
    entries = [pair(f"S{i}", day, 1_000_000, vendor="거래처A") for i in range(5)]
    entries.append(pair("S5", day, 50_000_000, vendor="거래처A"))
    hits = r2_vendor_history(journal(entries))[0]
    assert hits.empty


def test_r2a_separates_vendors_and_reports_blank_vendor():
    entries = vendor_history([1_000_000] * 5, vendor="거래처A")
    entries.append(pair("B1", date(2025, 6, 2), 20_000_000, vendor="거래처B"))  # 다른 거래처와 비교하지 않음
    entries.append(pair("NOV", date(2025, 6, 3), 20_000_000, vendor=""))
    hits, status, skipped = r2_vendor_history(journal(entries))
    assert hits.empty
    assert status.set_index("구분").loc["거래처 없음", "라인 수"] == 2
    assert sorted(skipped[skipped["제외 사유"].str.contains("거래처가 비어")]["전표번호"]) == ["NOV", "NOV"]


def test_r2a_status_accounts_for_every_line():
    entries = vendor_history([1_000_000] * 5 + [9_000_000])
    entries.append(pair("NOV", date(2025, 6, 3), 1_000, vendor=""))
    entries.append(("ZERO", date(2025, 6, 4), [("X100", "출장비", 0, 0), ("Y900", "현금성자산", 0, 0)], {"vendor": "거래처A"}))
    hits, status, skipped = r2_vendor_history(journal(entries))
    counts = status.set_index("구분")["라인 수"]
    assert counts["전체 라인"] == 16  # 거래처A 6전표×2 + NOV 2 + ZERO 2
    assert counts["금액 문제로 비교하지 않음"] + counts["거래처 없음"] + counts["이전 거래 부족"] + counts["비교함"] == 16
    assert counts["그중 탐지"] == len(hits) == 2 and counts["금액 문제로 비교하지 않음"] == 2


def test_r2a_without_vendor_column_is_off_with_note():
    entries = [pair(f"N{i}", d, a) for i, (d, a) in enumerate(spread(10))]
    hits, status, skipped = r2_vendor_history(journal(entries))
    assert hits.empty
    row = status.set_index("구분").loc["거래처 없음"]
    assert row["라인 수"] == 20 and "거래처 열 자체가 없음" in row["설명"]


# ---------------------------------------------------------------- R2 (b)
def monthly(counts, amount=100_000):
    entries = []
    for m, n in enumerate(counts, start=1):
        for i in range(n):
            entries.append(pair(f"M{m}-{i}", date(2025, m, 1 + i % 27), amount))
    return entries


def test_r2b_flags_month_with_spike_and_month_with_drop():
    counts = [10] * 12
    counts[5] = 40  # 6월 4배
    counts[8] = 2   # 9월 1/5
    table = r2_monthly_pattern(journal(monthly(counts)))
    x100 = table[(table["계정코드"] == "X100") & (table["항목"] == "라인 수")]
    assert set(zip(x100["연월"], x100["구분"])) == {("2025-06", "급증"), ("2025-09", "급감")}
    june = x100[x100["연월"] == "2025-06"].iloc[0]
    assert june["이 달 값"] == 40 and june["다른 달 중앙값"] == 10 and june["비교한 달 수"] == 11
    assert june["판정"] == PERIOD_LABEL  # 개별 거래가 아니라 계정-월(기간)에 대한 표시


def test_r2b_does_not_flag_normal_variation():
    table = r2_monthly_pattern(journal(monthly([10, 12, 9, 11, 14, 8, 10, 13, 9, 11, 12, 10])))
    assert table.empty


def test_r2b_counts_month_without_transactions_as_zero():
    counts = [10] * 12
    counts[3] = 0  # 4월 거래 없음
    table = r2_monthly_pattern(journal(monthly(counts)))
    april = table[(table["연월"] == "2025-04") & (table["계정코드"] == "X100") & (table["항목"] == "라인 수")]
    assert april["구분"].tolist() == ["급감"] and april["이 달 값"].tolist() == [0]


def test_r2b_needs_at_least_two_months():
    assert r2_monthly_pattern(journal(monthly([10]))).empty


# ---------------------------------------------------------------- R3
def test_business_days_and_closing_date_helpers():
    assert business_days_between(date(2025, 12, 31), date(2025, 12, 31)) == 0
    assert business_days_between(date(2025, 12, 26), date(2025, 12, 31)) == 3  # 금 → 월·화·수
    assert business_days_between(date(2025, 12, 31), date(2026, 1, 5)) == 3    # 목·금·월
    assert closing_date(2025, 2, 30) == date(2025, 2, 28)


def timing(entries_spec, **settings):
    entries = [pair(je, p, 1_000_000, entry_date=pd.Timestamp(e) if e else pd.NaT)
               for je, p, e in entries_spec]
    table, notes, _ = r3_timing(journal(entries), DetectionSettings(**settings))
    return table, notes


def test_r3a_closing_window_boundaries():
    table, _ = timing([
        ("D1229", date(2025, 12, 29), None),  # 결산일 2영업일 전: 탐지
        ("D1226", date(2025, 12, 26), None),  # 3영업일 전: 탐지(경계 포함)
        ("D1224", date(2025, 12, 24), None),  # 5영업일 전: 탐지 안 함
        ("D1220", date(2025, 12, 19), None),  # 탐지 안 함
        ("D0105", date(2026, 1, 5), None),    # 결산일 3영업일 후: 탐지
        ("D0106", date(2026, 1, 6), None),    # 4영업일 후: 탐지 안 함
    ])
    assert sorted(table["전표번호"]) == ["D0105", "D1226", "D1229"]
    assert table["해당 조건"].eq("(a) 결산일 전후").all()
    assert "2영업일" in table.set_index("전표번호").loc["D1229", "탐지 이유"]


def test_r3a_month_end_only_when_option_on():
    spec = [("M0530", date(2025, 5, 30), None)]  # 5월 말일(토) 직전 금요일
    assert timing(spec)[0].empty
    on, _ = timing(spec, r3_month_end=True)
    assert on["해당 조건"].tolist() == ["(a-2) 매월 말일 전후"]


def test_r3_counts_month_end_separately_even_when_off():
    """보조 옵션이 꺼져 있어도 (a-2) 해당 전표 수를 따로 세어, 켰을 때 얼마나 늘어나는지 보여 줍니다."""
    entries = [pair("M0530", date(2025, 5, 30), 1_000_000), pair("D1230", date(2025, 12, 30), 1_000_000),
               pair("MID", date(2025, 5, 15), 1_000_000)]
    df = journal(entries)
    off, _, counts_off = r3_timing(df)
    on, _, counts_on = r3_timing(df, DetectionSettings(r3_month_end=True))
    c_off = counts_off.set_index("조건")
    assert c_off.loc["(a) 결산일 전후", "해당 전표 수"] == 1        # 12월 말일은 결산일이므로 (a)로만 셈
    assert c_off.loc["(a-2) 매월 말일 전후", "해당 전표 수"] == 1
    assert "꺼짐" in c_off.loc["(a-2) 매월 말일 전후", "탐지 결과 포함"]
    assert off["전표번호"].tolist() == ["D1230"]
    assert sorted(on["전표번호"]) == ["D1230", "M0530"]
    assert counts_on.set_index("조건").loc["(a-2) 매월 말일 전후", "탐지 결과 포함"].startswith("포함")


def test_r3a_uses_user_closing_date():
    spec = [("J0630", date(2025, 6, 30), None)]
    assert timing(spec)[0].empty
    table, _ = timing(spec, closing_month=6, closing_day=30)
    assert table["전표번호"].tolist() == ["J0630"]


def test_r3b_entry_after_closing():
    table, _ = timing([
        ("LATE", date(2025, 12, 31), datetime(2026, 1, 20, 10, 12)),
        ("ONTIME", date(2025, 11, 14), datetime(2025, 11, 14, 9, 0)),   # 같은 날 입력
        ("NEXTFY", date(2026, 1, 20), datetime(2026, 1, 20, 9, 0)),     # 새 회계연도 거래
    ])
    late = table.set_index("전표번호")
    assert "(b) 결산 후 입력" in late.loc["LATE", "해당 조건"]
    assert "20일 뒤" in late.loc["LATE", "탐지 이유"]
    assert "ONTIME" not in late.index and "NEXTFY" not in late.index


def test_r3c_weekend_entry():
    table, _ = timing([
        ("SAT", date(2025, 9, 12), datetime(2025, 9, 13, 22, 40)),
        ("SUN", date(2025, 9, 12), datetime(2025, 9, 14, 8, 0)),
        ("FRI", date(2025, 9, 12), datetime(2025, 9, 12, 18, 0)),
    ])
    by = table.set_index("전표번호")
    assert set(by.index) == {"SAT", "SUN"}
    assert "토요일" in by.loc["SAT", "탐지 이유"] and "일요일" in by.loc["SUN", "탐지 이유"]


def test_r3_without_entry_date_turns_off_b_and_c():
    entries = [pair("SAT", date(2025, 9, 12), 1_000_000)]
    table, notes, counts = r3_timing(journal(entries))
    assert table.empty and "(b)" in notes[0] and "(c)" in notes[0]
    assert "검사 안 함" in counts.set_index("조건").loc["(c) 주말 입력", "탐지 결과 포함"]


def test_r3_missing_entry_for_some_vouchers_is_reported():
    table, notes = timing([("A", date(2025, 9, 12), datetime(2025, 9, 12, 9, 0)),
                           ("B", date(2025, 9, 12), None)])
    assert table.empty and any("입력일시가 없는 전표 1개" in n for n in notes)


def test_r3_reports_voucher_details():
    table, _ = timing([("SAT", date(2025, 9, 12), datetime(2025, 9, 13, 22, 40))])
    row = table.iloc[0]
    assert row["포함 계정"] == "X100 출장비, Y900 현금성자산"
    assert row["파일 행 번호"] == "2, 3" and row["전표 금액"] == 1_000_000


# ---------------------------------------------------------------- 종합·공통
def test_empty_input_does_not_crash():
    df, _ = prepare(pd.DataFrame())
    result = run_detection(df)
    for table in [result.r1, result.r2a, result.r2b, result.r3, result.summary]:
        assert table.empty


def test_summary_sorts_by_number_of_rules():
    entries = [pair(f"N{i}", d, a, vendor="거래처A", entry_date=pd.Timestamp(d) + pd.Timedelta(hours=9))
               for i, (d, a) in enumerate(spread(100))]
    entries.append(pair("MULTI", date(2025, 12, 31), 5_000_000, vendor="거래처A",
                        entry_date=pd.Timestamp(2026, 1, 20, 10)))
    entries.append(pair("WEEKEND", date(2025, 7, 4), 100_000, vendor="거래처A",
                        entry_date=pd.Timestamp(2025, 7, 5, 10)))
    summary = run_detection(journal(entries)).summary
    assert summary.iloc[0]["전표번호"] == "MULTI"
    assert summary.iloc[0]["해당 규칙"] == "R1, R2(a), R3"
    assert summary.iloc[0]["포함 계정"] == "X100 출장비, Y900 현금성자산"
    assert summary.iloc[0]["전표 금액"] == 5_000_000
    assert summary.set_index("전표번호").loc["WEEKEND", "해당 규칙 수"] == 1
    assert (summary["판정"] == REVIEW_LABEL).all()


def test_sample_data_answer_key_is_detected():
    """샘플 데이터에 일부러 넣은 R1~R3 패턴이 탐지되는지 확인합니다(제조업 샘플 한정 확인)."""
    from auditlens.sample_data import generate_sample_journal
    from auditlens.schema import suggest_mapping
    from auditlens.validation import validate_journal

    raw, _ = generate_sample_journal()
    df, _ = prepare(validate_journal(raw, suggest_mapping(raw.columns)).clean)
    result = run_detection(df)
    r1 = set(result.r1["전표번호"])
    r2a = set(result.r2a["전표번호"])
    r3 = result.r3.set_index("전표번호")["해당 조건"]

    def je(day, account, amount):
        hit = df[(df["posting_date"] == pd.Timestamp(day)) & (df["account_code"] == account)
                 & (df["line_amount"] == amount)]
        return hit["je_id"].iloc[0]

    assert je("2025-08-19", "830", 46_800_000) in r1                       # A01 소모품비 거액
    assert je("2025-11-12", "131", 38_500_000) in r2a                      # A02 거래처 대비 거액
    june = result.r2b[(result.r2b["계정코드"] == "824") & (result.r2b["항목"] == "라인 수")]
    assert "2025-06" in june["연월"].tolist()                               # A03 6월 운반비 급증
    assert "(b) 결산 후 입력" in r3[je("2025-12-31", "404", 280_000_000)]   # A04
    assert "(c) 주말 입력" in r3[je("2025-09-12", "831", 6_500_000)]        # A05
    rent = df[df["line_amount"] == 15_000_000]["je_id"]
    assert not set(rent) & (r1 | r2a)                                       # N03 매달 같은 임차료


# ---------------------------------------------------------------- 최종 검토 확인 (0원·음수, 미래 거래, 입력일시 기준)
def test_negative_and_zero_lines_keep_original_data_across_rules():
    """R1·R2(a)에서 비교하지 않은 음수·0원 라인도 원래 금액과 거래 정보가 남고, R2(b)·R3에서는 그대로 쓰입니다."""
    entries = vendor_history([1_000_000] * 6)
    entries.append(("REV", date(2025, 3, 1), [("X100", "출장비", -1_000_000, 0), ("Y900", "현금성자산", 0, -1_000_000)],
                    {"vendor": "거래처A", "description": "2월 출장비 역분개",
                     "entry_date": pd.Timestamp(2025, 3, 1, 14, 0)}))  # 토요일 입력
    entries.append(("ZERO", date(2025, 3, 3), [("X100", "출장비", 0, 0), ("Y900", "현금성자산", 0, 0)],
                    {"vendor": "거래처A", "description": "금액 미정", "entry_date": pd.Timestamp(2025, 3, 3, 9, 0)}))
    df = journal(entries)
    result = run_detection(df)

    for table in (result.r1_excluded, result.r2a_skipped):
        rev = table[table["전표번호"] == "REV"].set_index("계정코드")
        assert rev.loc["X100", "금액"] == -1_000_000 and rev.loc["X100", "방향"] == "차변"
        assert rev.loc["Y900", "금액"] == -1_000_000 and rev.loc["Y900", "방향"] == "대변"
        assert rev.loc["X100", "회계처리일"] == pd.Timestamp(2025, 3, 1)
        assert rev.loc["X100", "거래처"] == "거래처A" and rev.loc["X100", "적요"] == "2월 출장비 역분개"
        assert rev.loc["X100", "입력일시"] == pd.Timestamp(2025, 3, 1, 14, 0)
        assert rev.loc["X100", "파일 행 번호"] == df[(df["je_id"] == "REV") & (df["account_code"] == "X100")]["file_row"].iloc[0]
        assert "음수 금액" in rev.loc["X100", "제외 사유"]
        assert "0원" in table[table["전표번호"] == "ZERO"]["제외 사유"].iloc[0]
    # 금액 비교 규칙(R1·R2(a))의 탐지 결과에는 들어가지 않음
    assert "REV" not in set(result.r1["전표번호"]) | set(result.r2a["전표번호"])
    # 시점 규칙 R3는 금액과 무관하므로 역분개 전표도 검사하고, 전표 금액은 원래 값(음수)으로 표시
    r3 = result.r3.set_index("전표번호")
    assert "(c) 주말 입력" in r3.loc["REV", "해당 조건"] and r3.loc["REV", "전표 금액"] == -1_000_000
    assert "REV" in set(result.summary["전표번호"])


def test_r2b_counts_negative_lines_in_monthly_figures():
    """R2(b) 월 라인 수에는 역분개(음수) 라인도 그대로 들어갑니다."""
    entries = monthly([10] * 12)
    entries += [("NEG%d" % i, date(2025, 6, 2 + i % 20), [("X100", "출장비", -100_000, 0), ("Y900", "현금성자산", 0, -100_000)], {})
                for i in range(30)]
    table = r2_monthly_pattern(journal(entries))
    june = table[(table["계정코드"] == "X100") & (table["연월"] == "2025-06") & (table["항목"] == "라인 수")]
    assert june["이 달 값"].tolist() == [40] and june["구분"].tolist() == ["급증"]


def test_r2a_ignores_future_lines_even_if_file_order_is_reversed():
    """파일에서 뒤 날짜 거래가 먼저 나와도, 기준에는 회계처리일이 앞선 거래만 들어갑니다."""
    amounts = [1_000_000] * 5 + [6_000_000]
    entries = vendor_history(amounts)
    future = [pair(f"F{i}", date(2025, 12, 1) + timedelta(days=i), 50_000_000, vendor="거래처A") for i in range(10)]
    df = journal(future + entries)  # 미래 거래를 파일 앞쪽에 둠
    hits = r2_vendor_history(df)[0]
    v5 = hits[(hits["전표번호"] == "V5") & (hits["방향"] == "차변")].iloc[0]
    assert v5["이전 거래 수"] == 5 and v5["이전 거래 중앙값"] == 1_000_000
    assert v5["이전 거래 기간"] == "2025-01-06 ~ 2025-02-03"   # V5(2025-02-10)보다 뒤 날짜는 없음
    assert "뒤의 거래는 제외" in v5["탐지 이유"]
    # 미래 거래가 기준에 섞였다면 중앙값이 커져 V5는 탐지되지 않았을 것


def test_r2a_same_day_lines_are_excluded_from_each_others_history():
    """같은 날 거래는 서로의 기준에 들어가지 않고, 다음 날 거래부터 기준에 들어갑니다."""
    history = vendor_history([1_000_000] * 5)                           # 2025-01-06 ~ 02-03
    same_day = [pair(f"S{i}", date(2025, 3, 3), 1_000_000, vendor="거래처A") for i in range(20)]
    big_same_day = [pair("BIG", date(2025, 3, 3), 6_000_000, vendor="거래처A")]
    next_day = [pair("NEXT", date(2025, 3, 4), 6_000_000, vendor="거래처A")]
    hits = r2_vendor_history(journal(history + same_day + big_same_day + next_day))[0]
    debit = hits[hits["방향"] == "차변"].set_index("전표번호")
    assert debit.loc["BIG", "이전 거래 수"] == 5          # 같은 날 21건은 세지 않음
    assert debit.loc["NEXT", "이전 거래 수"] == 26        # 3월 3일 21건은 다음 날부터 기준에 들어감


def test_r3b_uses_entry_datetime_not_posting_date():
    """회계처리일이 같아도 입력일시가 결산일 다음 날 이후인 전표만 (b)에 해당합니다."""
    table, _ = timing([
        ("SAMEDAY", date(2025, 12, 31), datetime(2025, 12, 31, 23, 59)),   # 결산일 당일 밤 입력: 해당 없음
        ("NEXTDAY", date(2025, 12, 31), datetime(2026, 1, 1, 0, 1)),       # 결산일 다음 날 입력: 해당
        ("EARLY", date(2025, 6, 30), datetime(2026, 2, 2, 10, 0)),         # 회계처리일이 오래전이어도 입력 기준
        ("PREENTRY", date(2025, 12, 31), datetime(2025, 12, 20, 9, 0)),    # 미리 입력: 해당 없음
    ])
    by = table.set_index("전표번호")["해당 조건"]
    assert "(b) 결산 후 입력" in by["NEXTDAY"] and "(b) 결산 후 입력" in by["EARLY"]
    assert "(b)" not in by.get("SAMEDAY", "") and "(b)" not in by.get("PREENTRY", "")
    assert "입력일시는 2026-01-01 00:01" in table.set_index("전표번호").loc["NEXTDAY", "탐지 이유"]


def test_r3b_not_applied_when_entry_datetime_missing():
    """입력일시가 없는 전표는 회계처리일이 결산일이어도 (b)·(c)를 적용하지 않습니다."""
    table, notes, counts = r3_timing(journal([
        pair("NOENTRY", date(2025, 12, 31), 1_000_000, entry_date=pd.NaT),
        pair("HASENTRY", date(2025, 12, 31), 1_000_000, entry_date=pd.Timestamp(2026, 1, 20, 10)),
    ]))
    by = table.set_index("전표번호")["해당 조건"]
    assert by["NOENTRY"] == "(a) 결산일 전후"                # (a)만 해당, (b)는 적용 안 함
    assert "(b) 결산 후 입력" in by["HASENTRY"]
    assert counts.set_index("조건").loc["(b) 결산 후 입력", "해당 전표 수"] == 1
    assert any("입력일시가 없는 전표 1개" in n for n in notes)
