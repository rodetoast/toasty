"""Qt interaction tests; --integration saves only OS-temp sample copies."""

import argparse
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from functools import partial

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

import split_engine as engine
from split_dialog import SplitDialog, STATUS_LABELS, format_result

APP = QApplication.instance() or QApplication([])
TEST_MAX_FILE_SIZE = 2_000_000


def wait_dialog(dialog, timeout=10):
    deadline = time.monotonic() + timeout
    while dialog.busy and time.monotonic() < deadline:
        QTest.qWait(10)
    if dialog.busy:
        raise AssertionError("Worker did not complete")
    APP.processEvents()


def ready(path, count=4):
    tags = [dict(name="Same" if i < 2 else f"Tag{i}", first=i * 10 + 1, last=(i + 1) * 10,
                 repeats=0, direction=0) for i in range(count)]
    return engine.SplitResult("ready", str(path), details=dict(bytes=13_000_000, tags=tags,
                              frame_count=count*10, tag_count=count, sha256_before="preview hash"))


def clicked(approved):
    def choose(box):
        text = "실행" if approved else "취소"
        return next(b for b in box.buttons() if b.text() == text)
    return choose


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atm_gui_mock_")
        self.source = Path(self.temp.name) / "Sample.aseprite"
        self.source.write_bytes(b"mock input - never sent to Aseprite")
        self.dialog = SplitDialog(None, [self.source], "mock", auto_inspect=False)
        self.dialog.records[0] = ready(self.source)
        self.dialog.file_table.selectRow(0)
        self.dialog._select_file()

    def tearDown(self):
        wait_dialog(self.dialog)
        self.dialog.close()
        self.temp.cleanup()

    def approve_execute(self, approved):
        return [patch.object(QMessageBox, "exec", return_value=0),
                patch.object(QMessageBox, "clickedButton", new=clicked(approved))]

    def test_default_auto_and_provisional_plan(self):
        self.assertTrue(self.dialog.auto_radio.isChecked())
        text = self.dialog.preview_text.toPlainText()
        self.assertIn("초기", text)
        self.assertIn("저장 후 확인", text)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.source])

    def test_manual_one_multiple_last_and_duplicate_names(self):
        self.dialog.manual_radio.setChecked(True)
        self.assertFalse(self.dialog.execute_button.isEnabled())
        self.dialog.tag_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(self.dialog.boundaries(), [1])
        self.assertTrue(self.dialog.execute_button.isEnabled())
        self.dialog.tag_table.item(2, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(self.dialog.boundaries(), [1, 3])
        self.assertIn("3개 구간", self.dialog.preview_text.toPlainText())
        last = self.dialog.tag_table.item(3, 0)
        self.assertFalse(last.flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertEqual(self.dialog.tag_table.item(0, 2).text(), self.dialog.tag_table.item(1, 2).text())

    def test_boundary_indicators_have_visible_distinct_states(self):
        self.dialog.manual_radio.setChecked(True)
        self.dialog.show()
        APP.processEvents()
        table = self.dialog.tag_table

        def colors(row):
            rect = table.visualItemRect(table.item(row, 0))
            image = table.viewport().grab(rect).toImage()
            return {image.pixelColor(x, y).name()
                    for x in range(image.width()) for y in range(image.height())}

        self.assertIn("#c8c8c8", colors(0))
        table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        APP.processEvents()
        self.assertIn("#43a047", colors(0))
        self.assertIn("#626262", colors(3))
        self.assertFalse(table.item(3, 0).flags() & Qt.ItemFlag.ItemIsEnabled)

    def test_reject_confirmation_does_not_split(self):
        a, b = self.approve_execute(False)
        with a, b, patch("split_dialog.engine.split_file") as split:
            self.dialog.execute()
        split.assert_not_called()
        self.assertFalse(self.dialog.busy)

    def test_approval_worker_response_and_duplicate_execution(self):
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(5)
        threads = []
        def split(path, *args, **kwargs):
            threads.append(QThread.currentThread() != APP.thread())
            time.sleep(0.12)
            return engine.SplitResult("output_conflict", str(path), "existing output")
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", side_effect=split) as operation:
            self.dialog.execute()
            self.assertTrue(self.dialog.busy)
            self.assertFalse(self.dialog.execute_button.isEnabled())
            self.assertFalse(self.dialog.close_button.isEnabled())
            self.dialog.execute()
            self.dialog.reject()
            wait_dialog(self.dialog)
        timer.stop()
        self.assertEqual(operation.call_count, 1)
        self.assertTrue(all(threads))
        self.assertGreater(len(ticks), 2)
        self.assertIn("기존 출력 폴더 존재", self.dialog.result_text.toPlainText())
        self.assertEqual(operation.call_args.kwargs["expected_sha256"], "preview hash")

    def test_manual_oversize_keeps_boundaries_and_retry_available(self):
        self.dialog.manual_radio.setChecked(True)
        self.dialog.tag_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        result = engine.SplitResult("manual_oversize", str(self.source), details=dict(
            oversized_ranges=[dict(tag_range=[1, 2], bytes=10_100_000)]))
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", return_value=result) as split:
            self.dialog.execute()
            wait_dialog(self.dialog)
        self.assertEqual(split.call_args.kwargs["boundaries"], [2])
        self.assertEqual(self.dialog.boundaries(), [2])
        self.assertTrue(self.dialog.execute_button.isEnabled())
        self.assertIn("태그 1~2", self.dialog.result_text.toPlainText())
        self.assertIn("경계를 다시", self.dialog.result_text.toPlainText())

    def test_55_tags_scroll_boundaries_release_and_failed_save(self):
        self.dialog.records[0] = ready(self.source, 55)
        self.dialog._select_file()
        self.dialog.show()
        self.dialog.manual_radio.setChecked(True)
        for row in (0, 19, 53):
            self.dialog.tag_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(self.dialog.boundaries(), [1, 20, 54])
        self.assertIn("4개 구간", self.dialog.preview_text.toPlainText())
        self.dialog.tag_table.scrollToItem(self.dialog.tag_table.item(54, 2))
        APP.processEvents()
        self.assertGreater(self.dialog.tag_table.verticalScrollBar().value(), 0)
        self.assertFalse(self.dialog.tag_table.item(54, 0).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.dialog.tag_table.item(19, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertIn("3개 구간", self.dialog.preview_text.toPlainText())
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", return_value=engine.SplitResult("save_failed", str(self.source))) as op:
            self.dialog.execute()
            wait_dialog(self.dialog)
        self.assertEqual(op.call_args.kwargs["boundaries"], [1, 54])
        self.assertEqual(self.dialog.boundaries(), [1, 54])

    def test_size_skip(self):
        self.dialog.records[0] = engine.SplitResult("not_needed", str(self.source), details=dict(bytes=10_000_000))
        self.dialog._select_file()
        self.assertIn("분할 불필요", self.dialog.preview_text.toPlainText())
        self.assertIn("10,000,000", self.dialog.info_label.text())
        self.assertFalse(self.dialog.execute_button.isEnabled())

    def test_error_labels_and_single_tag_context(self):
        for status in ("no_tags", "unsupported_structure", "save_failed", "reopen_failed",
                       "verification_failed", "source_changed", "invalid_boundary"):
            self.assertIn(STATUS_LABELS[status], format_result(engine.SplitResult(status, str(self.source))))
        text = format_result(engine.SplitResult("single_tag_too_large", str(self.source),
                             details=dict(tag_name="Heavy", bytes=10_500_000)))
        self.assertIn("Heavy", text)
        self.assertIn("10,500,000", text)

    def test_failure_report_identifies_original_path(self):
        result = engine.SplitResult("save_failed", str(self.source), "Save failed")
        self.assertIn(str(self.source), format_result(result))

    def test_success_details_and_no_unverified_success_message(self):
        output = dict(path=str(self.source.parent / "Sample_split" / "Sample_1.aseprite"), bytes=1_000_000,
                      tag_range=[1, 2], frame_range=[1, 20], verification=dict(pixels=True))
        result = engine.SplitResult("manual_success", str(self.source), outputs=[output], original_unchanged=True)
        text = format_result(result)
        self.assertIn("직접 분할 완료", text)
        self.assertIn("1,000,000", text)
        self.assertIn("검증 성공", text)
        result.original_unchanged = False
        self.assertNotIn("통과했습니다", format_result(result))

    def test_multiple_files_manual_runs_selected_and_keeps_separate_boundaries(self):
        other = self.source.parent / "Second.aseprite"
        other.write_bytes(b"mock")
        self.dialog.close()
        self.dialog = SplitDialog(None, [self.source, other], "mock", auto_inspect=False)
        self.dialog.records = {0: ready(self.source), 1: ready(other)}
        self.dialog.file_table.selectRow(0)
        self.dialog._select_file()
        self.assertTrue(self.dialog.manual_radio.isEnabled())
        self.dialog.manual_radio.setChecked(True)
        self.dialog.tag_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.dialog.file_table.selectRow(1)
        self.assertEqual(self.dialog.boundaries(), [])
        self.assertFalse(self.dialog.execute_button.isEnabled())
        self.dialog.tag_table.item(2, 0).setCheckState(Qt.CheckState.Checked)
        self.dialog.file_table.selectRow(0)
        self.assertEqual(self.dialog.boundaries(), [1])
        self.dialog.file_table.selectRow(1)
        self.assertEqual(self.dialog.boundaries(), [3])
        a, b = self.approve_execute(True)
        result = engine.SplitResult("manual_oversize", str(other))
        with a, b, patch("split_dialog.engine.split_file", return_value=result) as split:
            self.dialog.execute()
            wait_dialog(self.dialog)
        split.assert_called_once()
        self.assertEqual(str(split.call_args.args[0]), str(other))
        self.assertEqual(split.call_args.kwargs["boundaries"], [3])
        self.assertEqual(split.call_args.kwargs["mode"], "manual")
        self.assertEqual(self.dialog.boundaries(), [3])
        self.dialog.auto_radio.setChecked(True)
        self.assertTrue(self.dialog.auto_radio.isChecked())

    def test_multiple_auto_files_continue_after_failure(self):
        other = self.source.parent / "Second.aseprite"
        other.write_bytes(b"mock")
        self.dialog.close()
        self.dialog = SplitDialog(None, [self.source, other], "mock", auto_inspect=False)
        self.dialog.records = {0: ready(self.source), 1: ready(other)}
        self.dialog.file_table.selectRow(0)
        self.dialog._select_file()
        self.assertTrue(self.dialog.manual_radio.isEnabled())
        def split(path, *args, **kwargs):
            if str(path) == str(self.source):
                return engine.SplitResult("save_failed", str(path))
            output = dict(path=str(other.parent / "Second_split" / "Second_1.aseprite"), bytes=1_000_000,
                          tag_range=[1, 2], frame_range=[1, 20], verification=dict(pixels=True))
            return engine.SplitResult("auto_success", str(path), outputs=[output], original_unchanged=True)
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", side_effect=split) as operation:
            self.dialog.execute()
            wait_dialog(self.dialog)
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(self.dialog.results[0].status, "save_failed")
        self.assertEqual(self.dialog.results[1].status, "auto_success")
        self.assertIn("저장 실패", self.dialog.result_text.toPlainText())
        self.assertIn("자동 분할 완료", self.dialog.result_text.toPlainText())

    def test_main_button_and_existing_analysis(self):
        import main
        window = main.MainWindow()
        try:
            self.assertEqual(window.btn_split.text(), "파일 용량 검사 및 분할")
            window.add_files([str(self.source)])
            window.file_list.item(0).setSelected(True)
            analysis = {str(self.source): [dict(name="attck", repeats=0)]}
            with patch.object(main.ReportDialog, "exec", return_value=0) as report:
                window.show_report(analysis)
            report.assert_called_once()
            self.assertEqual(main.APP_VERSION, "0.4.0")
        finally:
            window.close()

    def test_main_initial_size_and_path_wrap(self):
        import main
        window = main.MainWindow()
        try:
            available = window.screen().availableGeometry()
            window.show()
            APP.processEvents()
            self.assertEqual(window.width(), min(720, available.width() - 40))
            self.assertEqual(window.height(), min(980, available.height() - 60))
            self.assertTrue(window.path_label.wordWrap())
        finally:
            window.close()

    def test_main_controls_accessible_at_720_height(self):
        import main
        window = main.MainWindow()
        try:
            window.show()
            target_height = round(660 / window.devicePixelRatioF())
            window.resize(round(1000 / window.devicePixelRatioF()), target_height)
            APP.processEvents()
            self.assertLessEqual(window.height(), target_height)
            button = window.btn_split
            self.assertTrue(window.rect().contains(button.mapTo(window, button.rect().bottomRight())))
        finally:
            window.close()

    def test_dialog_initial_height_fits_available_screen(self):
        self.assertLessEqual(self.dialog.height(), APP.primaryScreen().availableGeometry().height() - 40)

    def test_split_controls_accessible_at_720_height(self):
        self.dialog.records[0] = ready(self.source, 55)
        self.dialog._select_file()
        self.dialog.show()
        ratio = self.dialog.devicePixelRatioF()
        target_height = round(660 / ratio)
        self.dialog.resize(round(1220 / ratio), target_height)
        APP.processEvents()
        self.assertLessEqual(self.dialog.height(), target_height)
        for button in (self.dialog.execute_button, self.dialog.close_button):
            self.assertTrue(self.dialog.rect().contains(button.mapTo(self.dialog, button.rect().bottomRight())))

    def test_accept_does_not_end_dialog_during_worker(self):
        def operation(path, *args, **kwargs):
            time.sleep(0.15)
            return engine.SplitResult("save_failed", str(path))
        self.dialog.show()
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", side_effect=operation):
            self.dialog.execute()
            try:
                self.dialog.accept()
                self.assertTrue(self.dialog.isVisible())
                self.dialog.done(1)
                self.assertTrue(self.dialog.isVisible())
                self.assertIn("작업 완료 후", self.dialog.progress_label.text())
            finally:
                wait_dialog(self.dialog)

    def test_parent_and_dialog_close_blocked_until_worker_finished(self):
        import main
        window = main.MainWindow()
        window.split_dialog = self.dialog
        window.show()
        self.dialog.show()
        def operation(path, *args, **kwargs):
            time.sleep(0.15)
            return engine.SplitResult("save_failed", str(path))
        a, b = self.approve_execute(True)
        try:
            with a, b, patch("split_dialog.engine.split_file", side_effect=operation):
                self.dialog.execute()
                self.assertFalse(window.close())
                self.assertFalse(self.dialog.close())
                self.assertTrue(self.dialog.worker.isRunning())
                self.assertTrue(window.isVisible())
                wait_dialog(self.dialog)
            self.assertFalse(self.dialog.worker.isRunning())
            self.assertTrue(window.close())
            self.assertTrue(self.dialog.close())
        finally:
            wait_dialog(self.dialog)
            window.close()

    def test_retry_clears_stale_success_before_work(self):
        self.dialog.result_text.setPlainText("OLD SUCCESS")
        def operation(path, *args, **kwargs):
            time.sleep(0.1)
            return engine.SplitResult("save_failed", str(path))
        a, b = self.approve_execute(True)
        with a, b, patch("split_dialog.engine.split_file", side_effect=operation):
            self.dialog.execute()
            try:
                self.assertNotIn("OLD SUCCESS", self.dialog.result_text.toPlainText())
            finally:
                wait_dialog(self.dialog)

    def test_inspection_runs_off_gui_thread_and_recovers(self):
        seen = []
        def inspect(path, *args, **kwargs):
            seen.append(QThread.currentThread() != APP.thread())
            time.sleep(0.1)
            return ready(path)
        with patch("split_dialog.engine.inspect_file", side_effect=inspect):
            self.dialog.start_inspection()
            wait_dialog(self.dialog)
        self.assertEqual(seen, [True])
        self.assertTrue(self.dialog.execute_button.isEnabled())

    def test_existing_tag_workers_also_block_main_close(self):
        import main
        class SlowWorker(QThread):
            def run(self):
                time.sleep(0.15)
        for name in ("analysis_thread", "apply_thread"):
            window = main.MainWindow()
            worker = SlowWorker()
            setattr(window, name, worker)
            window.show()
            worker.start()
            try:
                self.assertFalse(window.close(), name)
            finally:
                worker.wait()
                window.close()


def integration(executable, sample):
    # Only this integration fixture uses the former 2 MB limit.
    split = partial(engine.split_file, max_size=TEST_MAX_FILE_SIZE)
    with patch.object(engine, "MAX_FILE_SIZE", TEST_MAX_FILE_SIZE), patch.object(engine, "split_file", split):
        return _integration(executable, sample)


def _integration(executable, sample):
    before = engine.sha256(sample)
    root = Path(tempfile.mkdtemp(prefix="atm_gui_integration_"))
    report = []
    for mode in ("auto", "manual"):
        folder = root / mode
        folder.mkdir()
        source = folder / Path(sample).name
        shutil.copy2(sample, source)
        dialog = SplitDialog(None, [source], executable)
        dialog.show()
        wait_dialog(dialog, 120)
        assert dialog.records[0].status == "ready", dialog.records[0]
        assert not source.with_name(source.stem + "_split").exists()
        if mode == "manual":
            dialog.manual_radio.setChecked(True)
            dialog.tag_table.item(29, 0).setCheckState(Qt.CheckState.Checked)
            assert dialog.boundaries() == [30]
        assert "저장 후 확인" in dialog.preview_text.toPlainText()
        with patch.object(QMessageBox, "exec", return_value=0), patch.object(
                QMessageBox, "clickedButton", new=clicked(True)):
            dialog.execute()
            wait_dialog(dialog, 120)
        result = dialog.results[0]
        assert result.status == mode + "_success", result
        assert all(o["bytes"] <= engine.MAX_FILE_SIZE and all(o["verification"].values()) for o in result.outputs)
        assert engine.sha256(source) == before
        assert "검증 성공" in dialog.result_text.toPlainText()
        report.append(dict(mode=mode, outputs=result.outputs))
        if mode == "auto":
            small = engine.inspect_file(result.outputs[0]["path"], executable, include_small=True)
            assert small.status == "not_needed" and small.details["tag_count"] == 30
            assert small.details["frame_count"] == 190
        dialog.close()
    assert engine.sha256(sample) == before
    import json
    # Exercise the existing tag save path only on another disposable copy.
    import main
    regression = root / "tag_regression"
    regression.mkdir()
    tag_copy = regression / Path(sample).name
    shutil.copy2(sample, tag_copy)
    save = main.ApplyThread({}, executable).apply_to_file(str(tag_copy),
                [("Turn", "Turn_Audit", False), ("Recovery", "Recovery_(Loop)", True)])
    saved_tags = main.AnalysisThread([], executable).get_tags_from_file(str(tag_copy))
    assert any(t["name"] == "Turn_Audit" for t in saved_tags)
    assert any(t["name"] == "Recovery_(Loop)" and t["repeats"] == 1 for t in saved_tags)
    backups = list((regression / ".aseprite_backup").glob("*.aseprite"))
    assert len(backups) == 1 and engine.sha256(backups[0]) == before
    assert save["backup_verified"] and save["save_verified"]
    assert engine.sha256(sample) == before
    data = dict(temp_folder=str(root), sha256_before=before, sha256_after=engine.sha256(sample), results=report,
                tag_save_regression=dict(backup_verified=True, backup_sha256=engine.sha256(backups[0]),
                                         save_verified=True, changed_tags=save["changed_tags"]))
    (root / "gui_result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--aseprite", default=r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\aseprite.exe")
    parser.add_argument("--sample", default=str(Path(__file__).resolve().parent / "test_assets" / "Bedivere_Phase1.aseprite"))
    args, rest = parser.parse_known_args()
    tests = unittest.main(argv=[__file__, *rest], exit=False)
    if not tests.result.wasSuccessful():
        raise SystemExit(1)
    if args.integration:
        integration(args.aseprite, args.sample)
