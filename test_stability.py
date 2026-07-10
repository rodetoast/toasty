import os
import sys
import tempfile

import main
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_lua_quote():
    assert_equal(main.lua_quote('Tag "Quote"'), '"Tag \\"Quote\\""', "quote escaping")
    assert_equal(main.lua_quote(r"Back\Slash"), '"Back\\\\Slash"', "backslash preserved")
    assert_equal(main.lua_quote("공격_開始"), '"공격_開始"', "unicode preserved")
    assert_equal(
        main.lua_path_quote(r"C:\Temp Folder\한글\file.aseprite"),
        '"C:/Temp Folder/한글/file.aseprite"',
        "path slash conversion",
    )


def test_dictionary_and_settings_fallback():
    old_dict_path = main.DICT_FILE_PATH
    old_settings_path = main.SETTINGS_FILE_PATH
    old_log_path = main.LOG_FILE_PATH

    with tempfile.TemporaryDirectory() as temp_dir:
        missing_dict = os.path.join(temp_dir, "missing_dictionary.json")
        bad_dict = os.path.join(temp_dir, "bad_dictionary.json")
        bad_settings = os.path.join(temp_dir, "bad_settings.json")
        main.LOG_FILE_PATH = os.path.join(temp_dir, "test_error.log")

        main.DICT_FILE_PATH = missing_dict
        assert "Attack" in main.load_dictionary()

        with open(bad_dict, "w", encoding="utf-8") as f:
            f.write("{ broken json")
        main.DICT_FILE_PATH = bad_dict
        assert "Attack" in main.load_dictionary()

        main.SETTINGS_FILE_PATH = bad_settings
        with open(bad_settings, "w", encoding="utf-8") as f:
            f.write("{ broken json")
        assert_equal(main.load_settings(), {}, "bad settings fallback")
    main.DICT_FILE_PATH = old_dict_path
    main.SETTINGS_FILE_PATH = old_settings_path
    main.LOG_FILE_PATH = old_log_path


def test_normalize_words():
    words = [" Attack ", "attack", "", "Groggy", "groggy", "Loop"]
    assert_equal(main.normalize_words(words), ["Attack", "Groggy", "Loop"], "normalize words")


def test_formatting_examples():
    custom_dict = main.DEFAULT_DICTIONARY.copy()
    assert_equal(main.format_tag_name("chase_attck_End", custom_dict), ("Chase_Attack_End", False, True), "spell correction")
    assert_equal(main.format_tag_name("attck", custom_dict), ("Attack", False, True), "explicit typo correction")
    assert_equal(main.format_tag_name("powerwave", custom_dict), ("PowerWave", False, False), "wordninja compound")
    assert_equal(main.format_tag_name("Idle_(Loop)", custom_dict), ("Idle_(Loop)", True, False), "underscore loop")
    assert_equal(main.format_tag_name("Idle(Loop)", custom_dict), ("Idle_(Loop)", True, False), "inline loop")


def test_wing_is_not_corrected_to_swing():
    custom_dict = main.DEFAULT_DICTIONARY.copy()
    assert_equal(main.format_tag_name("Wing", custom_dict), ("Wing", False, False), "Wing exact")
    assert_equal(main.format_tag_name("wing", custom_dict), ("Wing", False, False), "wing exact")
    assert_equal(main.format_tag_name("Swing", custom_dict), ("Swing", False, False), "Swing exact")
    assert_equal(main.format_tag_name("swing", custom_dict), ("Swing", False, False), "swing exact")


def test_custom_dictionary_extends_defaults():
    old_dict_path = main.DICT_FILE_PATH
    old_log_path = main.LOG_FILE_PATH

    with tempfile.TemporaryDirectory() as temp_dir:
        main.DICT_FILE_PATH = os.path.join(temp_dir, "custom_dictionary.json")
        main.LOG_FILE_PATH = os.path.join(temp_dir, "test_error.log")
        with open(main.DICT_FILE_PATH, "w", encoding="utf-8") as f:
            f.write('["CustomOnlyWord", "attack"]')

        loaded = main.load_dictionary()
        loaded_lower = {word.lower(): word for word in loaded}
        assert "wing" in loaded_lower
        assert "customonlyword" in loaded_lower
        assert_equal(loaded_lower["attack"], "Attack", "default casing wins")

    main.DICT_FILE_PATH = old_dict_path
    main.LOG_FILE_PATH = old_log_path


def test_conservative_fuzzy_guards():
    corrected, was_corrected = main.correct_spelling("wing", ["Swing"])
    assert_equal((corrected, was_corrected), ("Wing", False), "first-letter and prefix guard")

    corrected, was_corrected = main.correct_spelling("ring", ["Bring"])
    assert_equal((corrected, was_corrected), ("Ring", False), "suffix guard")

    corrected, was_corrected = main.correct_spelling("attck", main.DEFAULT_DICTIONARY)
    assert_equal((corrected, was_corrected), ("Attack", True), "explicit typo survives guards")


def test_spelling_correction_types():
    assert_equal(
        main.correct_spelling_detailed("attck", main.DEFAULT_DICTIONARY),
        ("Attack", main.CORRECTION_EXPLICIT_TYPO),
        "explicit typo correction type",
    )
    assert_equal(
        main.correct_spelling_detailed("recovey", ["Recovery"]),
        ("Recovery", main.CORRECTION_FUZZY_SUGGESTION),
        "fuzzy suggestion correction type",
    )
    assert_equal(
        main.correct_spelling_detailed("wing", ["Swing"]),
        ("Wing", main.CORRECTION_NONE),
        "blocked fuzzy correction type",
    )


def test_spelling_report_entry_defaults():
    explicit_entries = main.build_report_entries(
        {"sample.aseprite": [{"name": "attck", "repeats": 0}]},
        main.DEFAULT_DICTIONARY,
    )
    assert explicit_entries, "explicit typo entry missing"
    explicit_entry = explicit_entries[0]
    assert main.EXPLICIT_TYPO_ISSUE in explicit_entry["issues"]
    assert_equal(explicit_entry["checked"], True, "explicit typo defaults checked")

    fuzzy_entries = main.build_report_entries(
        {"sample.aseprite": [{"name": "recovey", "repeats": 0}]},
        ["Recovery"],
    )
    assert fuzzy_entries, "fuzzy suggestion entry missing"
    fuzzy_entry = fuzzy_entries[0]
    assert main.FUZZY_SUGGESTION_ISSUE in fuzzy_entry["issues"]
    assert_equal(fuzzy_entry["checked"], False, "fuzzy suggestion defaults unchecked")


def test_collision_report_entry_defaults_off():
    analysis = {
        "sample.aseprite": [
            {"name": "Attack", "repeats": 0},
            {"name": "Attck", "repeats": 0},
        ]
    }
    entries = main.build_report_entries(analysis, main.DEFAULT_DICTIONARY)
    collision_entries = [entry for entry in entries if entry["old_name"] == "Attck"]
    assert collision_entries, "collision entry was not created"
    entry = collision_entries[0]
    assert main.COLLISION_ISSUE in entry["issues"]
    assert_equal(entry["checked"], False, "collision defaults unchecked")


def test_apply_time_collision_detection_blocks_save():
    analysis = {
        "sample.aseprite": [
            {"name": "Attack", "repeats": 0},
            {"name": "Attck", "repeats": 0},
        ]
    }
    mods = {"sample.aseprite": [("Attck", "Attack", False)]}
    collisions = main.detect_modification_collisions(analysis, mods)
    assert_equal(len(collisions), 1, "collision count")
    assert_equal(collisions[0]["new_name"], "Attack", "collision target name")
    assert_equal(collisions[0]["old_names"], ["Attack", "Attck"], "collision original tags")


def test_normal_modification_has_no_collision():
    analysis = {
        "sample.aseprite": [
            {"name": "Turn", "repeats": 0},
            {"name": "Recovery", "repeats": 0},
        ]
    }
    mods = {"sample.aseprite": [("Turn", "Turn_Test", False)]}
    assert_equal(main.detect_modification_collisions(analysis, mods), [], "normal change collision")


def test_report_toggle_all_does_not_enable_collision_items():
    app = QApplication.instance() or QApplication(sys.argv)
    analysis = {
        "sample.aseprite": [
            {"name": "Attack", "repeats": 0},
            {"name": "Attck", "repeats": 0},
            {"name": "Turn", "repeats": 0},
        ]
    }
    dialog = main.ReportDialog(None, analysis, main.DEFAULT_DICTIONARY)
    dialog.toggle_all(Qt.CheckState.Checked.value)
    collision_items = [item for item in dialog.checkboxes if item.get("is_collision")]
    assert collision_items, "collision UI item was not created"
    for item in collision_items:
        assert_equal(item["checkbox"].isChecked(), False, "collision stays unchecked")
        assert_equal(item["checkbox"].isEnabled(), False, "collision is disabled")


def test_report_summary_counts():
    analysis = {
        "sample.aseprite": [
            {"name": "attck", "repeats": 0},
            {"name": "recovey", "repeats": 0},
            {"name": "Idle_(Loop)", "repeats": 0},
        ]
    }
    entries = main.build_report_entries(analysis, main.DEFAULT_DICTIONARY)
    summary = main.build_report_summary(analysis, entries, ["Error reading broken.aseprite"])
    assert_equal(summary["file_count"], 1, "summary file count")
    assert_equal(summary["tag_count"], 3, "summary tag count")
    assert_equal(summary["analysis_failure_count"], 1, "summary failure count")
    assert summary["issue_counts"].get(main.EXPLICIT_TYPO_ISSUE, 0) >= 1
    assert summary["issue_counts"].get(main.FUZZY_SUGGESTION_ISSUE, 0) >= 1
    assert summary["issue_counts"].get(main.LOOP_REPEAT_ISSUE, 0) >= 1


def test_no_issues_report_helpers():
    analysis = {
        "sample.aseprite": [
            {"name": "Idle_(Loop)", "repeats": 1},
            {"name": "Attack", "repeats": 0},
        ]
    }
    assert_equal(main.build_report_entries(analysis, main.DEFAULT_DICTIONARY), [], "no issue entries")
    assert_equal(main.count_tags(analysis), 2, "tag count")

    message = main.format_no_issues_message(analysis)
    for expected in [
        "문제가 발견되지 않았습니다.",
        "분석 파일 수: 1",
        "분석 태그 수: 2",
        "발견된 문제 수: 0",
    ]:
        if expected not in message:
            raise AssertionError(f"no issue message missing {expected!r}: {message}")


def test_analysis_incomplete_message():
    analysis = {"sample.aseprite": [{"name": "Attack", "repeats": 0}]}
    failures = ["Error reading broken.aseprite: boom"]
    message = main.format_analysis_incomplete_message(analysis, failures)
    for expected in [
        "전체를 문제 없음으로 볼 수 없습니다.",
        "분석 성공 파일 수: 1",
        "분석 실패 파일 수: 1",
        "broken.aseprite",
    ]:
        if expected not in message:
            raise AssertionError(f"analysis incomplete message missing {expected!r}: {message}")


def test_apply_summary_message():
    summary = {
        "success_files": ["ok.aseprite"],
        "failed_files": [{"file_path": "bad.aseprite", "reason": "timeout"}],
        "changed_tags": 2,
        "backup_verified_files": ["ok.aseprite"],
        "save_verified_files": ["ok.aseprite"],
    }
    message = main.format_apply_summary_message(summary)
    for expected in [
        "성공 파일 수: 1",
        "실패 파일 수: 1",
        "변경 태그 수: 2",
        "백업 생성/검증 성공 파일 수: 1",
        "저장 후 검증 성공 파일 수: 1",
        "bad.aseprite: timeout",
    ]:
        if expected not in message:
            raise AssertionError(f"apply summary missing {expected!r}: {message}")


if __name__ == "__main__":
    test_lua_quote()
    test_dictionary_and_settings_fallback()
    test_normalize_words()
    test_formatting_examples()
    test_wing_is_not_corrected_to_swing()
    test_custom_dictionary_extends_defaults()
    test_conservative_fuzzy_guards()
    test_spelling_correction_types()
    test_spelling_report_entry_defaults()
    test_collision_report_entry_defaults_off()
    test_apply_time_collision_detection_blocks_save()
    test_normal_modification_has_no_collision()
    test_report_toggle_all_does_not_enable_collision_items()
    test_report_summary_counts()
    test_no_issues_report_helpers()
    test_analysis_incomplete_message()
    test_apply_summary_message()
    print("stability tests ok")
