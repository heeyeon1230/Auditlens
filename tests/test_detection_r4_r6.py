"""탐지 규칙 R4~R6 단위 테스트와 예외 상황 테스트.

R1~R3 테스트처럼 업종과 무관한 임의 계정(X100 출장비 등)·임의 값으로 만든 작은 표를 씁니다.
마지막 묶음만 제조업 샘플 데이터의 정답표를 확인합니다.
"""

from datetime import date, timedelta

import pandas as pd

from auditlens.analysis import prepare
from auditlens.detection import (
    PRIORITY_GROUP, REVIEW_LABEL, SUPPORT_GROUP, DetectionSettings, r4_manual_entries, r5_new_vendors, r6_duplicates,
    run_detection,
)
from tests.test_detection import journal, pair


def r4(entries, **settings):
    table, notes, counts = r4_manual_entries(journal(entries), DetectionSettings(**settings))
    return table.set_index("전표번호") if len(table) else table, notes, counts.set_index("조건")


def auto(je, day=date(2025, 3, 10), **extra):
    """평범한 자동 전표: 원천 '자동', 유형 '일반', 입력자 batch, 승인자 system, 적요 있음."""
    base = {"source": "자동", "je_type": "일반", "created_by": "batch", "approved_by": "system",
            "description": "정기 거래"}
    return pair(je, day, 100_000, **{**base, **extra})


# ---------------------------------------------------------------- R4
def test_r4a_flags_selected_source_and_type_values_only():
    table, _, counts = r4([
        auto(f"N{i}") for i in range(200)
    ] + [auto("MAN", source="수기"), auto("ADJ", je_type="조정"), auto("CLS", je_type="결산"),
         auto("ETC", je_type="기타")])
    assert set(table.index) == {"MAN", "ADJ", "CLS"}
    assert table.loc["MAN", "해당 조건"] == "(a) 수기·조정 전표"
    assert "전표 원천 '수기'" in table.loc["MAN", "탐지 이유"] and "전표 유형 '조정'" in table.loc["ADJ", "탐지 이유"]
    assert counts.loc["(a) 수기·조정 전표", "해당 전표 수"] == 3
    assert (table["판정"] == REVIEW_LABEL).all()


def test_r4a_works_with_other_erp_codes_chosen_by_user():
    entries = [auto(f"N{i}", source="SYS") for i in range(200)] + [auto("M1", source="MANUAL")]
    assert r4(entries)[0].empty  # 기본값('수기')과 다른 코드를 쓰는 ERP에서는 아무것도 걸리지 않음
    table = r4(entries, r4_sources=("MANUAL",), r4_types=())[0]
    assert list(table.index) == ["M1"]


def test_r4a_off_when_no_values_selected():
    table, notes, counts = r4([auto(f"N{i}") for i in range(200)] + [auto("MAN", source="수기")],
                              r4_sources=(), r4_types=())
    assert table.empty and "선택한 값 없음" in counts.loc["(a) 수기·조정 전표", "적용 여부"]


def test_r4b_same_creator_and_approver():
    table, notes, _ = r4([auto(f"N{i}") for i in range(200)] + [
        auto("SAME", created_by="kim", approved_by="kim"),
        auto("SPACE", created_by="lee ", approved_by=" lee"),   # 앞뒤 공백만 다른 같은 사람
        auto("DIFF", created_by="kim", approved_by="park"),
        auto("NOAPP", created_by="kim", approved_by=""),
    ], r4_rare_share=0.0)
    assert set(table.index) == {"SAME", "SPACE"}
    assert "'kim'" in table.loc["SAME", "탐지 이유"]
    assert any("비어 있는 전표 1개" in n for n in notes)


def test_r4c_rare_creator_share_boundary():
    # 회계연도 전표 200개: 1개(0.5%)는 1% 미만, 2개(1.0%)는 1% 미만이 아님
    entries = [auto(f"N{i}") for i in range(197)] + [auto("RARE", created_by="temp")] + \
              [auto(f"TWO{i}", created_by="two") for i in range(2)]
    table, _, _ = r4(entries)
    assert list(table.index) == ["RARE"]
    reason = table.loc["RARE", "탐지 이유"]
    assert "1개로 그해 전체 200개의 0.50%" in reason and "기준 1% 미만" in reason


def test_r4c_counts_each_fiscal_year_separately():
    # 2024년에는 자주 쓰던 사람이 2025년에는 1건만 입력하면 2025년 전표만 걸림
    entries = [auto(f"A{i}", day=date(2024, 5, 1), created_by="kim") for i in range(50)] + \
              [auto(f"B{i}", day=date(2025, 5, 1)) for i in range(150)] + \
              [auto("KIM25", day=date(2025, 6, 2), created_by="kim")]
    table, _, _ = r4(entries)
    assert list(table.index) == ["KIM25"]
    assert "2025-12-31 결산 회계연도" in table.loc["KIM25", "탐지 이유"]


def test_r4d_blank_description_and_keywords():
    table, _, _ = r4([auto(f"N{i}") for i in range(200)] + [
        auto("BLANK", description=""), auto("KW", description="전기 오류 수정분"),
        auto("NORMAL", description="사무용품 구입"),
    ])
    assert set(table.index) == {"BLANK", "KW"}
    assert table.loc["BLANK", "해당 조건"] == "(d) 적요 없음"
    assert "'수정'" in table.loc["KW", "탐지 이유"] and "'오류'" in table.loc["KW", "탐지 이유"]
    custom = r4([auto(f"N{i}") for i in range(200)] + [auto("KW2", description="가계정 대체")], r4_keywords=("가계정",))[0]
    assert list(custom.index) == ["KW2"]


def test_r4_turns_off_sub_conditions_without_columns():
    entries = [pair(f"N{i}", date(2025, 3, 10), 100_000) for i in range(5)]  # 선택 열이 하나도 없음
    table, notes, counts = r4(entries)
    assert table.empty
    assert counts["적용 여부"].str.startswith("검사 안 함").all()
    assert len(notes) == 4


def test_r4_multiple_conditions_are_all_listed():
    table, _, _ = r4([auto(f"N{i}") for i in range(200)] + [
        auto("ALL", source="수기", created_by="temp", approved_by="temp", description="임시 처리")])
    assert table.loc["ALL", "해당 조건"] == "(a) 수기·조정 전표, (b) 입력자 = 승인자, (c) 드문 입력자, (d) 적요 키워드"


# ---------------------------------------------------------------- R5
def vendor_year(vendor="기존거래처", start=date(2025, 1, 6), n=40, amount=1_000_000):
    return [pair(f"{vendor}-{i}", start + timedelta(days=7 * i), amount, vendor=vendor) for i in range(n)]


def test_r5_flags_new_vendor_above_threshold_only():
    entries = vendor_year(start=date(2025, 1, 6), n=50)  # 2025-01 ~ 2025-12 (마지막 거래 12월 15일)
    entries += [pair("NEW1", date(2025, 11, 20), 45_000_000, vendor="새거래처"),
                pair("NEW2", date(2025, 12, 15), 38_000_000, vendor="새거래처"),
                pair("SMALL", date(2025, 11, 25), 1_000_000, vendor="작은신규")]
    table, notes, candidates = r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=50_000_000))
    assert table["거래처"].tolist() == ["새거래처"]
    row = table.iloc[0]
    assert row["합계 금액"] == 83_000_000  # 전표마다 한 번만 셈 (차변·대변 라인 모두에 거래처가 있어도 두 배가 되지 않음)
    assert row["전표번호"] == "NEW1, NEW2" and row["최초 등장일"] == pd.Timestamp(2025, 11, 20)
    assert row["판단 기간"].startswith("2025-11-01 ~ 2025-12-15")
    assert set(candidates["거래처"]) == {"새거래처", "작은신규"}  # 기준금액 미만도 후보 목록에는 보임
    assert candidates.set_index("거래처").loc["작은신규", "기준금액 이상"] == "아니오"


def test_r5_vendor_present_since_earlier_month_is_not_new():
    entries = vendor_year(n=50)
    entries.append(pair("BIG", date(2025, 12, 1), 900_000_000, vendor="기존거래처"))
    table, _, candidates = r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=1))
    assert table.empty and candidates.empty


def test_r5_without_threshold_lists_candidates_but_flags_nothing():
    entries = vendor_year(n=50) + [pair("NEW1", date(2025, 11, 20), 45_000_000, vendor="새거래처")]
    table, notes, candidates = r5_new_vendors(journal(entries))
    assert table.empty and candidates["기준금액 이상"].tolist() == ["기준금액 미입력"]
    assert any("기준금액을 입력하지 않아" in n for n in notes)


def test_r5_new_account_combination_for_existing_vendor():
    entries = vendor_year(n=50)
    entries.append(("COMBO", date(2025, 12, 1), [("Z500", "광고선전비", 30_000_000, 0), ("Y900", "현금성자산", 0, 30_000_000)],
                    {"vendor": "기존거래처"}))
    table, _, _ = r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=10_000_000))
    assert table["구분"].tolist() == ["기존 거래처의 신규 계정"]
    assert table.iloc[0]["계정"] == "Z500 광고선전비" and table.iloc[0]["합계 금액"] == 30_000_000


def test_r5_blank_or_missing_vendor_is_reported():
    entries = vendor_year(n=50) + [pair("NOV", date(2025, 12, 1), 90_000_000, vendor="")]
    table, notes, _ = r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=1))
    assert table.empty and any("거래처가 비어 있는 라인 2건" in n for n in notes)
    no_col = r5_new_vendors(journal([pair("A", date(2025, 1, 1), 1)]), DetectionSettings(r5_threshold=1))
    assert no_col[0].empty and "검사하지 않았습니다" in no_col[1][0]


def test_r5_month_window_setting():
    entries = vendor_year(n=50) + [pair("OCT", date(2025, 10, 15), 90_000_000, vendor="10월신규")]
    assert r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=1))[0].empty
    three = r5_new_vendors(journal(entries), DetectionSettings(r5_threshold=1, r5_months=3))[0]
    assert three["거래처"].tolist() == ["10월신규"]


# ---------------------------------------------------------------- R6
def purchase(je, day, amount=17_350_000, vendor="거래처P", desc="원재료 매입"):
    return (je, day, [("X200", "원재료", amount, 0), ("Y300", "외상매입금", 0, amount)],
            {"vendor": vendor, "description": desc})


def test_r6_flags_same_lines_within_seven_days_as_one_voucher_pair():
    table, _ = r6_duplicates(journal([purchase("P1", date(2025, 5, 14)), purchase("P2", date(2025, 5, 17))]))
    assert len(table) == 1
    row = table.iloc[0]
    assert (row["전표번호 A"], row["전표번호 B"]) == ("P1", "P2") and row["일수 차이"] == 3
    assert row["일치 라인"] == "X200 원재료 차변 17,350,000원; Y300 외상매입금 대변 17,350,000원"
    assert row["적요 일치"] == "일치" and row["판정"] == REVIEW_LABEL


def test_r6_day_boundary_and_monthly_rent():
    assert len(r6_duplicates(journal([purchase("A", date(2025, 5, 1)), purchase("B", date(2025, 5, 8))]))[0]) == 1
    assert r6_duplicates(journal([purchase("A", date(2025, 5, 1)), purchase("B", date(2025, 5, 9))]))[0].empty
    rent = [purchase(f"R{m}", date(2025, m, 25), 15_000_000, "임대인", "월 임차료") for m in range(1, 13)]
    assert r6_duplicates(journal(rent))[0].empty  # 매달 같은 금액(30일 간격)은 걸리지 않음


def test_r6_ignores_lines_in_same_voucher_and_different_amount_or_direction():
    same_voucher = ("S1", date(2025, 5, 1), [("X200", "원재료", 1_000, 0), ("X200", "원재료", 1_000, 0),
                                             ("Y300", "외상매입금", 0, 2_000)], {"vendor": "거래처P"})
    assert r6_duplicates(journal([same_voucher]))[0].empty
    diff_amount = [purchase("A", date(2025, 5, 1), 1_000_000), purchase("B", date(2025, 5, 2), 1_000_001)]
    assert r6_duplicates(journal(diff_amount))[0].empty
    reverse = [purchase("A", date(2025, 5, 1)),
               ("B", date(2025, 5, 2), [("Y300", "외상매입금", 17_350_000, 0), ("X200", "원재료", 0, 17_350_000)],
                {"vendor": "거래처P"})]
    assert r6_duplicates(journal(reverse))[0].empty  # 같은 금액이지만 방향이 반대(취소 분개 등)


def test_r6_blank_vendor_and_zero_amount_are_not_compared():
    table, notes = r6_duplicates(journal([
        purchase("A", date(2025, 5, 1), vendor="거래처P"),
        purchase("B1", date(2025, 5, 2), vendor=""), purchase("B2", date(2025, 5, 3), vendor=""),
        purchase("Z1", date(2025, 5, 2), 0), purchase("Z2", date(2025, 5, 3), 0),
    ]))
    assert table.empty
    assert any("거래처가 비어 있어" in n and "4건" in n for n in notes)
    assert any("0원이라" in n and "4건" in n for n in notes)


def test_r6_description_difference_is_shown():
    table, _ = r6_duplicates(journal([purchase("A", date(2025, 5, 1), desc="1차 납품"),
                                      purchase("B", date(2025, 5, 2), desc="2차 납품")]))
    assert table.iloc[0]["적요 일치"] == "다름"


# ---------------------------------------------------------------- 종합·원본 보존
def test_summary_includes_r4_r6_with_all_reasons():
    entries = [auto(f"N{i}", vendor="거래처Q") for i in range(200)]
    entries += [purchase("P1", date(2025, 5, 14)), purchase("P2", date(2025, 5, 16))]
    result = run_detection(journal(entries))
    summary = result.summary.set_index("전표번호")
    assert "R6" in summary.loc["P1", "해당 규칙"] and "R6" in summary.loc["P2", "해당 규칙"]
    reasons = result.reasons[result.reasons["전표번호"] == "P1"]
    assert reasons["규칙"].tolist() == ["R6"]
    assert "짝 전표 P2" in reasons[reasons["규칙"] == "R6"]["대상"].iloc[0]


def manual(je, day=date(2025, 3, 10), **extra):
    """평범한 수기 전표: 원천만 '수기'이고 입력자·승인자·적요는 자동 전표와 같은 평범한 값."""
    return auto(je, day, **{"source": "수기", **extra})


def test_summary_puts_manual_only_vouchers_in_support_group():
    entries = [auto(f"N{i}") for i in range(200)]
    entries += [manual("ONLY"), manual("SAME", created_by="batch", approved_by="batch"),
                manual("NODESC", description=""), auto("AUTO_SAME", created_by="batch", approved_by="batch")]
    summary = run_detection(journal(entries)).summary.set_index("전표번호")
    assert summary.loc["ONLY", "검토 구분"] == SUPPORT_GROUP
    assert summary.loc["ONLY", "해당 세부 조건"] == "R4(a)"
    for je in ["SAME", "NODESC", "AUTO_SAME"]:  # (a)와 다른 조건이 함께 있거나, (a)가 아닌 조건만 있는 전표
        assert summary.loc[je, "검토 구분"] == PRIORITY_GROUP
    assert summary.loc["SAME", "해당 세부 조건"] == "R4(a), R4(b)"
    assert (summary["판정"] == REVIEW_LABEL).all()  # 보조 목록도 검토 대상 거래 문구 그대로


def test_summary_manual_with_other_rule_is_priority():
    entries = [auto(f"N{i}", vendor="거래처Q") for i in range(200)]
    p1 = purchase("P1", date(2025, 5, 14))
    entries += [(*p1[:3], {**p1[3], "source": "수기"}), purchase("P2", date(2025, 5, 16))]
    summary = run_detection(journal(entries)).summary.set_index("전표번호")
    assert summary.loc["P1", "해당 규칙"] == "R4, R6" and summary.loc["P1", "검토 구분"] == PRIORITY_GROUP
    assert summary.loc["P1", "해당 세부 조건 수"] == 2


def test_summary_orders_by_rules_then_sub_conditions_then_date():
    entries = [auto(f"N{i}") for i in range(200)]
    entries += [manual("EARLY_ONLY", day=date(2025, 2, 3)),                                   # 보조: 날짜가 가장 빨라도 맨 뒤
                auto("ONE", day=date(2025, 2, 4), created_by="batch", approved_by="batch"),   # R4 (b) 1개
                manual("MANY", day=date(2025, 8, 1), created_by="batch", approved_by="batch",
                       description="임시 처리")]                                               # R4 (a)(b)(d) 3개
    summary = run_detection(journal(entries)).summary
    assert summary["전표번호"].tolist() == ["MANY", "ONE", "EARLY_ONLY"]
    assert summary["해당 규칙 수"].tolist() == [1, 1, 1]
    assert summary["해당 세부 조건 수"].tolist() == [3, 1, 1]


def test_summary_without_source_columns_has_no_support_group():
    entries = [pair(f"N{i}", date(2025, 3, 10), 100_000) for i in range(50)]
    entries += [pair("P1", date(2025, 5, 14), 777_000, vendor="V"), pair("P2", date(2025, 5, 16), 777_000, vendor="V")]
    summary = run_detection(journal(entries)).summary
    assert len(summary) and (summary["검토 구분"] == PRIORITY_GROUP).all()


def test_detection_does_not_change_input_data():
    entries = [auto(f"N{i}", vendor="거래처Q") for i in range(50)] + [purchase("P1", date(2025, 5, 14)),
                                                                   purchase("P2", date(2025, 5, 16))]
    df = journal(entries)
    before = df.copy(deep=True)
    run_detection(df, DetectionSettings(r5_threshold=1))
    pd.testing.assert_frame_equal(df, before)


def test_empty_input_for_r4_r6():
    df, _ = prepare(pd.DataFrame())
    result = run_detection(df, DetectionSettings(r5_threshold=1))
    assert result.r4.empty and result.r5.empty and result.r6.empty


def test_sample_answer_key_for_r4_r6():
    """샘플 데이터에 넣은 R4~R6 패턴(A04·A06·A07, A08, A09)이 탐지되고 매달 임차료(N03)는 R6에 걸리지 않습니다."""
    from auditlens.sample_data import generate_sample_journal
    from auditlens.schema import suggest_mapping
    from auditlens.validation import validate_journal

    raw, _ = generate_sample_journal()
    df, _ = prepare(validate_journal(raw, suggest_mapping(raw.columns)).clean)
    result = run_detection(df, DetectionSettings(r5_threshold=50_000_000))

    def je(day, account, amount):
        hit = df[(df["posting_date"] == pd.Timestamp(day)) & (df["account_code"] == account)
                 & (df["line_amount"] == amount)]
        return hit["je_id"].iloc[0]

    r4 = result.r4.set_index("전표번호")["해당 조건"]
    assert "(a)" in r4[je("2025-12-31", "404", 280_000_000)]                     # A04 수기 매출 조정
    a06 = r4[je("2025-10-27", "455", 54_000_000)]
    assert "(b) 입력자 = 승인자" in a06 and "(c) 드문 입력자" in a06               # A06
    assert "(d) 적요 없음" in r4[je("2025-03-31", "831", 12_000_000)]             # A07
    assert result.r5["거래처"].tolist() == ["신규컨설팅(가상)"]                    # A08
    assert result.r5.iloc[0]["합계 금액"] == 83_000_000
    pair_ids = {(r["전표번호 A"], r["전표번호 B"]) for r in result.r6.to_dict("records")}
    a09 = tuple(sorted([je("2025-05-14", "131", 17_350_000), je("2025-05-19", "131", 17_350_000)]))
    assert a09 in pair_ids                                                        # A09
    rent = set(df[df["line_amount"] == 15_000_000]["je_id"])
    assert not rent & ({a for a, _ in pair_ids} | {b for _, b in pair_ids})      # N03


def test_sample_answer_key_review_groups():
    """샘플 정답표: A03(운반비 월 급증, R2(b) 기간 결과로 보는 패턴)만 보조 목록이고, 나머지 비정상 패턴은 우선 검토 목록입니다.
    매달 임차료(N03)는 결산일 전후인 1개를 빼고 보조 목록입니다."""
    from auditlens.sample_data import generate_sample_journal
    from auditlens.schema import suggest_mapping
    from auditlens.validation import validate_journal

    raw, key = generate_sample_journal()
    df, _ = prepare(validate_journal(raw, suggest_mapping(raw.columns)).clean)
    summary = run_detection(df, DetectionSettings(r5_threshold=50_000_000)).summary.set_index("전표번호")
    ids = {r["시나리오"]: r["전표번호 전체"].split(", ") for r in key.to_dict("records")}
    group = summary["검토 구분"]
    for case in ["A01", "A02", "A04", "A05", "A06", "A07", "A08", "A09"]:
        assert (group.loc[ids[case]] == PRIORITY_GROUP).all(), case
    assert (group.loc[ids["A03"]] == SUPPORT_GROUP).all()
    assert (group.loc[ids["N03"]] == SUPPORT_GROUP).sum() == 11
    assert (group == PRIORITY_GROUP).sum() == 137 and (group == SUPPORT_GROUP).sum() == 570
    order = summary.index.tolist()
    assert order.index(ids["A06"][0]) < 30  # R4 조건 4개 전표가 1개 규칙 전표 중 앞쪽에 옴
