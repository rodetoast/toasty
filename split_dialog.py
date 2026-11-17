"""Independent split UI; inspection and saving always run off the GUI thread."""

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
                             QTabWidget, QTextEdit, QVBoxLayout)

import split_engine as engine


STATUS_LABELS = {
    "ready": "분할 필요", "not_needed": "분할 불필요",
    "auto_success": "자동 분할 완료", "manual_success": "직접 분할 완료",
    "no_tags": "태그 없는 파일", "unsupported_structure": "지원하지 않는 파일 구조",
    "single_tag_too_large": "단일 태그 용량 초과", "invalid_boundary": "잘못된 분할 경계",
    "manual_oversize": "직접 분할 결과 용량 초과", "output_conflict": "기존 출력 폴더 존재",
    "save_failed": "저장 실패", "reopen_failed": "Aseprite 재열기 실패",
    "verification_failed": "데이터 보존 검증 실패", "source_changed": "원본 파일 변경 감지",
    "invalid_input": "잘못된 입력", "io_failed": "파일 처리 실패", "publish_failed": "결과 공개 실패",
}


def format_result(result):
    lines = [Path(result.source).name, f"원본 경로: {result.source}", STATUS_LABELS.get(result.status, result.status)]
    if result.status in ("auto_success", "manual_success"):
        if not result.original_unchanged or not result.outputs or not all(
                o["verification"] and all(o["verification"].values()) for o in result.outputs):
            return "데이터 보존 검증 실패: 완료 결과의 검증 정보가 없습니다."
        lines.append(f"생성 파일 수: {len(result.outputs)}")
        for output in result.outputs:
            a, b = output["tag_range"]
            first, last = output["frame_range"]
            lines.extend([f"{Path(output['path']).name}: {output['bytes']:,} bytes",
                          f"태그 {a}~{b} / 원본 프레임 {first}~{last} / 검증 성공"])
        lines.extend(["모든 결과 파일이 용량 및 데이터 검증을 통과했습니다.",
                      f"출력 폴더: {Path(result.outputs[0]['path']).parent}", "원본 SHA-256 불변 확인"])
        if result.details.get("cleanup_warning"):
            lines.append(f"임시 폴더 정리 경고: {result.details['cleanup_warning']}")
            lines.append(result.details.get("staging_path", ""))
    else:
        if result.message:
            lines.append(result.message)
        details = result.details
        if "tag_name" in details:
            lines.append(f"태그: {details['tag_name']} / {details.get('bytes', 0):,} bytes")
        for item in details.get("oversized_ranges", []):
            a, b = item["tag_range"]
            lines.append(f"초과 구간: 태그 {a}~{b} / {item['bytes']:,} bytes")
        if result.status == "manual_oversize":
            lines.append("경계를 다시 선택해 주세요. 결과 폴더는 생성되지 않았습니다.")
        extra = {k: v for k, v in details.items() if k not in
                 ("tags", "sha256_before", "sha256_after", "oversized_ranges")}
        if extra:
            lines.append(json.dumps(extra, ensure_ascii=False, indent=2))
    return "\n".join(lines)


class SplitWorker(QThread):
    progress = pyqtSignal(int, str)
    result_ready = pyqtSignal(int, object)

    def __init__(self, jobs, aseprite_path, *, inspect=False, mode="auto", boundaries=None, parent=None):
        super().__init__(parent)
        self.jobs = list(jobs)
        self.aseprite_path = aseprite_path
        self.inspect_only = inspect
        self.mode = mode
        self.boundaries = list(boundaries or [])

    def run(self):
        for index, path, digest in self.jobs:
            self.progress.emit(index, "읽기 전용 검사 중" if self.inspect_only else "분할 및 검증 중")
            try:
                if self.inspect_only:
                    result = engine.inspect_file(path, self.aseprite_path, include_small=True)
                else:
                    result = engine.split_file(path, self.aseprite_path, mode=self.mode,
                                               boundaries=self.boundaries, expected_sha256=digest)
            except Exception as exc:
                result = engine.SplitResult("io_failed", str(path), str(exc))
            self.result_ready.emit(index, result)


class SplitDialog(QDialog):
    log_signal = pyqtSignal(str)

    def __init__(self, parent, files, aseprite_path, *, auto_inspect=True):
        super().__init__(parent)
        self.setWindowTitle("파일 용량 검사 및 분할")
        available = self.screen().availableGeometry()
        self.resize(min(1060, available.width() - 40), min(820, available.height() - 60))
        self.setStyleSheet("""
            QTabBar::tab { background: #383838; color: #dddddd; padding: 6px 12px; }
            QTabBar::tab:selected { background: #505050; color: #ffffff; }
            QRadioButton::indicator { width: 12px; height: 12px; border-radius: 7px;
                border: 1px solid #aaaaaa; background: #202020; }
            QRadioButton::indicator:checked { background: #4caf50; border: 1px solid #dddddd; }
            QRadioButton::indicator:disabled { background: #444444; border-color: #666666; }
            QTableWidget#splitTagTable::indicator {
                width: 16px; height: 16px; border: 2px solid #c8c8c8;
                border-radius: 2px; background: #252525;
            }
            QTableWidget#splitTagTable::indicator:checked {
                background: #43a047; border-color: #ffffff;
            }
            QTableWidget#splitTagTable::indicator:unchecked:hover { border-color: #ffffff; }
            QTableWidget#splitTagTable::indicator:disabled {
                background: #353535; border-color: #626262;
            }
        """)
        self.files = list(dict.fromkeys(str(Path(f).resolve()) for f in files))
        self.aseprite_path = aseprite_path
        self.records = {}
        self.results = {}
        self.worker = None
        self.busy = False
        self._populating = False
        self._tag_file_index = None
        self._file_boundaries = {}
        layout = QVBoxLayout(self)
        self.file_table = QTableWidget(len(self.files), 5)
        self.file_table.setHorizontalHeaderLabels(["파일명", "실제 용량 (bytes)", "상태", "태그 수", "프레임 수"])
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            self.file_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.setMaximumHeight(170)
        for row, path in enumerate(self.files):
            for col, text in enumerate([Path(path).name, "검사 대기", "검사 대기", "-", "-"]):
                item = QTableWidgetItem(text)
                item.setToolTip(path)
                self.file_table.setItem(row, col, item)
        self.file_table.currentCellChanged.connect(self._select_file)
        layout.addWidget(self.file_table)
        self.info_label = QLabel(f"파일당 제한: {engine.MAX_FILE_SIZE:,} bytes")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        modes = QHBoxLayout()
        self.auto_radio = QRadioButton("자동 분할")
        self.manual_radio = QRadioButton("직접 분할")
        self.auto_radio.setChecked(True)
        self.manual_radio.setToolTip("선택한 파일 하나를 직접 분할합니다.")
        self.auto_radio.setToolTip("목록의 파일들을 자동 분할합니다.")
        self.auto_radio.toggled.connect(self.update_preview)
        self.manual_radio.toggled.connect(self.update_preview)
        modes.addWidget(self.auto_radio)
        modes.addWidget(self.manual_radio)
        modes.addStretch()
        layout.addLayout(modes)
        self.tag_table = QTableWidget(0, 6)
        self.tag_table.setObjectName("splitTagTable")
        self.tag_table.setHorizontalHeaderLabels(["이 태그 뒤 분할", "순번", "태그 이름", "시작", "종료", "프레임 수"])
        self.tag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tag_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 3, 4, 5):
            self.tag_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tag_table.itemChanged.connect(self.update_preview)
        layout.addWidget(self.tag_table, 1)
        self.tabs = QTabWidget()
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.tabs.addTab(self.preview_text, "분할 계획")
        self.tabs.addTab(self.result_text, "실행 결과")
        layout.addWidget(self.tabs, 1)
        self.progress_label = QLabel("읽기 전용 검사 대기")
        layout.addWidget(self.progress_label)
        buttons = QHBoxLayout()
        self.execute_button = QPushButton("분할 실행")
        self.execute_button.clicked.connect(self.execute)
        self.folder_button = QPushButton("결과 폴더 열기")
        self.folder_button.clicked.connect(self.open_folder)
        self.close_button = QPushButton("닫기")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.folder_button)
        buttons.addStretch()
        buttons.addWidget(self.execute_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.folder_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        if self.files:
            self.file_table.selectRow(0)
        if auto_inspect and self.files:
            self.start_inspection()

    def start_inspection(self):
        jobs = [(i, path, None) for i, path in enumerate(self.files)]
        self._start_worker(SplitWorker(jobs, self.aseprite_path, inspect=True, parent=self))

    def _start_worker(self, worker):
        if self.busy:
            return
        self.worker = worker
        self.busy = True
        self._enable_controls()
        worker.progress.connect(self._progress)
        worker.result_ready.connect(self._receive_result)
        worker.finished.connect(self._finished)
        worker.start()

    def _progress(self, index, text):
        self.file_table.item(index, 2).setText(text)
        self.progress_label.setText(f"{index + 1}/{len(self.files)}: {Path(self.files[index]).name} - {text}")

    def _receive_result(self, index, result):
        if self.worker.inspect_only:
            self.records[index] = result
            for col, key in ((1, "bytes"), (3, "tag_count"), (4, "frame_count")):
                value = result.details.get(key)
                self.file_table.item(index, col).setText(f"{value:,}" if value is not None else "-")
        else:
            self.results[index] = result
            self.log_signal.emit(f"{Path(result.source).name}: {STATUS_LABELS.get(result.status, result.status)}")
        self.file_table.item(index, 2).setText(STATUS_LABELS.get(result.status, result.status))
        self._render_results()
        if index == self.current_index():
            self._select_file()

    def _finished(self):
        self.busy = False
        self.progress_label.setText("읽기 전용 검사 완료" if self.worker.inspect_only else "파일별 처리 완료")
        self._enable_controls()
        self.update_preview()
        if not self.worker.inspect_only:
            self.tabs.setCurrentWidget(self.result_text)

    def current_index(self):
        return self.file_table.currentRow()

    def boundaries(self):
        return [row + 1 for row in range(self.tag_table.rowCount() - 1)
                if self.tag_table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _select_file(self, *args):
        index = self.current_index()
        record = self.records.get(index)
        if self._tag_file_index is not None:
            self._file_boundaries[self._tag_file_index] = self.boundaries()
        if record is None:
            self._populating = True
            self.tag_table.setRowCount(0)
            self._tag_file_index = None
            self._populating = False
            self.preview_text.clear()
            self._enable_controls()
            return
        self._populating = True
        old_boundaries = self._file_boundaries.get(index, [])
        self._tag_file_index = index
        tags = record.details.get("tags", [])
        self.tag_table.setRowCount(len(tags))
        for row, tag in enumerate(tags):
            check = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            if row < len(tags) - 1:
                flags |= Qt.ItemFlag.ItemIsEnabled
            check.setFlags(flags)
            check.setCheckState(Qt.CheckState.Checked if row + 1 in old_boundaries else Qt.CheckState.Unchecked)
            self.tag_table.setItem(row, 0, check)
            for col, value in enumerate([row + 1, tag["name"], tag["first"], tag["last"],
                                         tag["last"] - tag["first"] + 1], 1):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.tag_table.setItem(row, col, item)
        d = record.details
        self.info_label.setText(f"{Path(record.source).name} | 실제 용량: {d.get('bytes', 0):,} bytes | "
                                f"제한: {engine.MAX_FILE_SIZE:,} bytes | "
                                f"태그: {d.get('tag_count', '-')} | 프레임: {d.get('frame_count', '-')}")
        self._populating = False
        self.update_preview()

    def update_preview(self, *args):
        if self._populating:
            return
        record = self.records.get(self.current_index())
        self.tag_table.setEnabled(not self.busy and self.manual_radio.isChecked())
        if record is None:
            return
        if record.status != "ready":
            self.preview_text.setPlainText(format_result(record))
        else:
            try:
                plan = engine.preview_plan(record.source, record.details["tags"], record.details["frame_count"],
                                           mode="manual" if self.manual_radio.isChecked() else "auto",
                                           boundaries=self.boundaries())
                lines = ["자동 분할: 초기 경계 계획 (최종 개수는 저장 후 결정)" if plan["provisional"]
                         else f"직접 분할: {len(plan['files'])}개 구간"]
                for output in plan["files"]:
                    a, b = output["tag_range"]
                    first, last = output["frame_range"]
                    lines.extend([f"\n{output['name']} | 태그 {a}~{b} | 프레임 {first}~{last}",
                                  "용량: 저장 후 확인", ", ".join(output["tag_names"])])
                self.preview_text.setPlainText("\n".join(lines))
            except engine.SplitFailure as exc:
                self.preview_text.setPlainText("분할 경계를 선택해 주세요." if exc.status == "invalid_boundary" else str(exc))
        self._enable_controls()

    def _enable_controls(self):
        self.auto_radio.setEnabled(not self.busy)
        selected = self.records.get(self.current_index())
        self.manual_radio.setEnabled(not self.busy and selected is not None and selected.status == "ready")
        self.close_button.setEnabled(not self.busy)
        self.tag_table.setEnabled(not self.busy and self.manual_radio.isChecked())
        can_execute = any(r.status == "ready" for r in self.records.values())
        if self.manual_radio.isChecked():
            can_execute = selected is not None and selected.status == "ready" and bool(self.boundaries())
        self.execute_button.setEnabled(not self.busy and can_execute)
        result = self.results.get(self.current_index())
        self.folder_button.setEnabled(not self.busy and result is not None and bool(result.outputs)
                                      and result.status in ("auto_success", "manual_success"))

    def execute(self):
        if self.busy or not self.execute_button.isEnabled():
            return
        manual = self.manual_radio.isChecked()
        jobs = [(i, r.source, r.details.get("sha256_before")) for i, r in sorted(self.records.items())
                if r.status in ("ready", "not_needed") and (not manual or i == self.current_index())]
        message = "\n".join(f"{Path(path).name}: {self.records[i].details['bytes']:,} bytes" for i, path, _ in jobs)
        message += (f"\n\n분할 방식: {'직접 분할' if manual else '자동 분할'}\n"
                    f"파일당 제한: {engine.MAX_FILE_SIZE:,} bytes\n"
                    "원본 파일은 변경되지 않습니다.\n결과는 각 원본의 별도 _split 폴더에 저장됩니다.")
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("분할 실행 확인")
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setText(message)
        run_button = confirmation.addButton("실행", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = confirmation.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        confirmation.setDefaultButton(cancel_button)
        confirmation.exec()
        if confirmation.clickedButton() != run_button:
            return
        self.results.clear()
        self._render_results()
        for index, _, _ in jobs:
            self.file_table.item(index, 2).setText("처리 대기")
        self._start_worker(SplitWorker(jobs, self.aseprite_path, mode="manual" if manual else "auto",
                                      boundaries=self.boundaries(), parent=self))

    def _render_results(self):
        lines = []
        for index, record in sorted(self.records.items()):
            result = self.results.get(index, record)
            if result.status == "ready":
                continue
            lines.append(f"원본 용량: {record.details.get('bytes', 0):,} bytes\n{format_result(result)}")
        self.result_text.setPlainText("\n\n".join(lines))

    def open_folder(self):
        result = self.results.get(self.current_index())
        if result and result.outputs and result.status in ("auto_success", "manual_success"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(result.outputs[0]["path"]).parent)))

    def reject(self):
        if self.busy:
            self.notify_busy_close()
        else:
            super().reject()

    def accept(self):
        if self.busy:
            self.notify_busy_close()
        else:
            super().accept()

    def done(self, result):
        if self.busy:
            self.notify_busy_close()
        else:
            super().done(result)

    def notify_busy_close(self):
        self.progress_label.setText("작업 중에는 종료할 수 없습니다. 작업 완료 후 닫아 주세요.")

    def closeEvent(self, event):
        if self.busy:
            self.notify_busy_close()
            event.ignore()
        else:
            super().closeEvent(event)
