"""탐지 기준 설정(규칙 켜기/끄기, 기본값 비교) 회귀 테스트.

기본값으로는 이전과 같은 결과가 나오는지, 규칙 하나를 끄면 그 규칙의 결과만 빠지는지,
다시 켜면 기본 결과가 그대로 돌아오는지를 가상 샘플 데이터로 확인합니다.
"""

from dataclasses import replace

import pandas as pd
import pytest

from auditlens.analysis import prepare
from auditlens.detection import (
    OFF_NOTE, PRIORITY_GROUP, RULE_SWITCHES, SUPPORT_GROUP, DetectionSettings, run_detection,
)

# 규칙별 결과 표 (DetectionResult 필드 이름)
RULE_TABLES = {"R1": ["r1"], "R2(a)": ["r2a"], "R2(b)": ["r2b"], "R3": ["r3"], "R4": ["r4"], "R5": ["r5"], "R6": ["r6"]}
ALL_TABLES = ["r1", "r1_skipped", "r1_excluded", "r2a", "r2a_status", "r2a_skipped", "r2b", "r3", "r3_counts",
              "r4", "r4_counts", "r5", "r5_candidates", "r6", "summary", "reasons"]
R5_ON = DetectionSettings(r5_threshold=50_000_000)  # R5까지 결과가 나오도록 기준금액만 넣은 설정


@pytest.fixture(scope="module")
def sample_df():
    from auditlens.sample_data import generate_sample_journal
    from auditlens.schema import suggest_mapping
    from auditlens.validation import validate_journal

    raw, _ = generate_sample_journal()
    df, _ = prepare(validate_journal(raw, suggest_mapping(raw.columns)).clean)
    return df


@pytest.fixture(scope="module")
def baseline(sample_df):
    return run_detection(sample_df, R5_ON)


def test_defaults_all_rules_on_and_nothing_changed():
    s = DetectionSettings()
    assert s.disabled_rules() == [] and s.changed() == []
    assert all(getattr(s, f) is True for f in RULE_SWITCHES.values())
    table = s.as_table()
    assert list(table.columns) == ["기준", "기본값", "적용값", "기본값과 다름"]
    assert (table["기본값과 다름"] == "").all() and (table["기본값"] == table["적용값"]).all()
    assert set(table.loc[table["기준"].str.endswith(" 사용"), "적용값"]) == {"켜짐"}


def test_changed_items_are_counted_and_marked():
    s = DetectionSettings(r1_multiplier=2.0, r6_enabled=False, r4_keywords=("수정",))
    assert sorted(s.changed()) == ["r1_multiplier", "r4_keywords", "r6_enabled"]
    assert s.disabled_rules() == ["R6"]
    table = s.as_table().set_index("기준")
    assert table.loc["R6 사용", "적용값"] == "꺼짐" and table.loc["R6 사용", "기본값"] == "켜짐"
    assert (table["기본값과 다름"] == "예").sum() == 3
    assert table.loc["R1 IQR 배수", "기본값"] == 3.0 and table.loc["R1 IQR 배수", "적용값"] == 2.0


def test_default_settings_give_same_result_as_before(sample_df):
    """기본값 그대로면 이전 결과(우선 검토 137개, 보조 570개)와 같습니다."""
    groups = run_detection(sample_df, DetectionSettings()).summary["검토 구분"].value_counts()
    assert groups[PRIORITY_GROUP] == 137 and groups[SUPPORT_GROUP] == 570


@pytest.mark.parametrize("rule", list(RULE_SWITCHES))
def test_turning_off_one_rule_removes_only_that_rule(sample_df, baseline, rule):
    off = run_detection(sample_df, replace(R5_ON, **{RULE_SWITCHES[rule]: False}))
    # 1) 끈 규칙의 결과는 비고, 다른 규칙의 탐지 결과는 그대로입니다.
    for name in RULE_TABLES[rule]:
        assert getattr(off, name).empty and len(getattr(baseline, name)) > 0
    for other, names in RULE_TABLES.items():
        if other == rule:
            continue
        for name in names:
            before, after = getattr(baseline, name), getattr(off, name)
            if name == "r4":  # R4의 판정 문구는 종합의 검토 구분을 따르므로 탐지 행만 비교합니다.
                before, after = before.drop(columns="판정"), after.drop(columns="판정")
            pd.testing.assert_frame_equal(before, after)
    # 2) 종합에서는 그 규칙의 사유만 빠지고, 그 규칙에만 해당하던 전표만 사라집니다.
    assert rule not in set(off.reasons["규칙"])
    before = baseline.summary.set_index("전표번호")["해당 규칙"].str.split(", ")
    expected = {je: [r for r in rules if r != rule] for je, rules in before.items()}
    expected = {je: rules for je, rules in expected.items() if rules}
    after = off.summary.set_index("전표번호")["해당 규칙"].str.split(", ").to_dict()
    assert after == expected
    # 3) 꺼진 규칙은 그 사실을 남깁니다(전표 단위 규칙의 처리 내역).
    notes = {"R3": off.r3_notes, "R4": off.r4_notes, "R5": off.r5_notes, "R6": off.r6_notes}
    if rule in notes:
        assert notes[rule] == [OFF_NOTE]


@pytest.mark.parametrize("rule", list(RULE_SWITCHES))
def test_turning_rule_back_on_restores_default_result(sample_df, baseline, rule):
    off_settings = replace(R5_ON, **{RULE_SWITCHES[rule]: False})
    run_detection(sample_df, off_settings)
    back_on = replace(off_settings, **{RULE_SWITCHES[rule]: True})
    assert back_on == R5_ON
    restored = run_detection(sample_df, back_on)
    for name in ALL_TABLES:
        pd.testing.assert_frame_equal(getattr(restored, name), getattr(baseline, name))
    assert restored.r3_notes == baseline.r3_notes and restored.r6_notes == baseline.r6_notes


def test_all_rules_off_gives_empty_summary_without_error(sample_df):
    s = replace(R5_ON, **{f: False for f in RULE_SWITCHES.values()})
    result = run_detection(sample_df, s)
    assert result.summary.empty and result.reasons.empty
    assert s.disabled_rules() == list(RULE_SWITCHES)


def test_turning_off_rules_does_not_change_input_data(sample_df):
    before = sample_df.copy(deep=True)
    run_detection(sample_df, replace(R5_ON, r1_enabled=False, r4_enabled=False))
    pd.testing.assert_frame_equal(sample_df, before)
