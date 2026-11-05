# -*- coding: utf-8 -*-
"""processing/converter_dialog.py — Humminbird 및 Lowrance 소나 파일 변환 대화상자 (PySide6)

Humminbird (.DAT, .SON) 및 Lowrance (.SL2, .SL3, .SLG) 소나 기록 파일을
표준 2채널(Port/Starboard) XTF 포맷으로 변환하고,
변환 즉시 메인 워크스페이스에 로드하여 워터폴 확인 및 GeoTIFF 모자이킹을 수행할 수 있도록 지원합니다.

참조:
 - CameronBodine/PINGMapper (https://github.com/CameronBodine/PINGMapper)
"""
from __future__ import annotations

import os
import sys
import time
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QTextEdit, QFileDialog, QMessageBox, QCheckBox,
    QLineEdit, QRadioButton, QButtonGroup, QFrame, QAbstractItemView
)

from processing.sonar_converter import convert_sonar_to_xtf, inspect_sonar_file


# ==============================================================================
# 1. 백그라운드 변환 작업 스레드 (Worker)
# ==============================================================================
class SonarConvertWorker(QThread):
    """소나 파일 변환을 비동기 수행하는 QThread 작업자."""
    file_started = Signal(int, str)       # (row, filepath)
    file_progress = Signal(int, int, str) # (row, percent, status_msg)
    file_finished = Signal(int, str, bool, str) # (row, xtf_path, success, msg)
    all_finished = Signal(list)           # [successful_xtf_paths]
    log_message = Signal(str)             # log text

    def __init__(self, file_tasks: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.file_tasks = file_tasks
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        successful_xtf = []
        total = len(self.file_tasks)
        self.log_message.emit(f"🚀 총 {total}개 소나 파일 변환 작업을 시작합니다...")

        for idx, task in enumerate(self.file_tasks):
            if self._is_cancelled:
                self.log_message.emit("⚠️ 사용자에 의해 변환 작업이 중단되었습니다.")
                break

            row = task['row']
            src_path = task['src_path']
            dst_path = task['dst_path']
            fname = os.path.basename(src_path)

            self.file_started.emit(row, src_path)
            self.log_message.emit(f"[{idx + 1}/{total}] 변환 시작: {fname}")

            def progress_cb(pct: int, msg: str):
                self.file_progress.emit(row, pct, msg)

            try:
                out_path = convert_sonar_to_xtf(
                    input_path=src_path,
                    output_xtf_path=dst_path,
                    progress_callback=progress_cb
                )
                successful_xtf.append(out_path)
                self.file_finished.emit(row, out_path, True, "변환 완료")
                self.log_message.emit(f"✅ [{idx + 1}/{total}] 완료: {os.path.basename(out_path)}")
            except Exception as e:
                err_msg = str(e)
                self.file_finished.emit(row, "", False, f"오류: {err_msg}")
                self.log_message.emit(f"❌ [{idx + 1}/{total}] 실패: {fname} -> {err_msg}")

        self.all_finished.emit(successful_xtf)


# ==============================================================================
# 2. 소나 파일 포맷 변환기 대화상자 (SonarConverterDialog)
# ==============================================================================
class SonarConverterDialog(QDialog):
    """Humminbird / Lowrance -> XTF 소나 파일 일괄 변환 대화상자."""

    converted_files_ready = Signal(list)  # [xtf_filepaths] 변환 후 즉시 열기 시 메인 윈도우로 전달

    def __init__(self, parent=None, initial_files: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("소나 파일 포맷 변환기 (Humminbird / Lowrance ➔ XTF)")
        self.resize(920, 680)

        self.worker: Optional[SonarConvertWorker] = None
        self._file_records: List[Dict[str, Any]] = []

        self._build_ui()

        if initial_files:
            self.add_files(initial_files)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # 상단 안내 배너
        info_banner = QFrame()
        info_banner.setStyleSheet("""
            QFrame {
                background-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        ib_layout = QHBoxLayout(info_banner)
        ib_layout.setContentsMargins(8, 4, 8, 4)
        info_lbl = QLabel(
            "<b>지원 포맷:</b> Lowrance (<code>.SL2, .SL3, .SLG</code>), "
            "Humminbird (<code>.DAT, .SON, .IDX</code>) ➔ <b>표준 XTF (2-채널 사이드스캔)</b><br>"
            "<small style='color: #8b949e;'>PINGMapper 및 PINGVerter 사양을 기반으로 정밀 GPS 좌표 및 사이드스캔 빔 데이터를 XTF로 손실 없이 인코딩합니다.</small>"
        )
        info_lbl.setWordWrap(True)
        ib_layout.addWidget(info_lbl)
        main_layout.addWidget(info_banner)

        # 파일 목록 테이블 그룹박스
        grp_table = QGroupBox("변환 대상 소나 파일 목록")
        tbl_layout = QVBoxLayout(grp_table)

        # 상단 버튼 툴바
        btn_bar = QHBoxLayout()
        self.btn_add_files = QPushButton("➕ 파일 추가...")
        self.btn_add_files.clicked.connect(self._on_add_files_clicked)
        btn_bar.addWidget(self.btn_add_files)

        self.btn_add_dir = QPushButton("📁 폴더 탐색 추가...")
        self.btn_add_dir.clicked.connect(self._on_add_dir_clicked)
        btn_bar.addWidget(self.btn_add_dir)

        self.btn_remove_sel = QPushButton("➖ 선택 삭제")
        self.btn_remove_sel.clicked.connect(self._on_remove_selected)
        btn_bar.addWidget(self.btn_remove_sel)

        self.btn_clear = QPushButton("🗑️ 전체 비우기")
        self.btn_clear.clicked.connect(self._on_clear_all)
        btn_bar.addWidget(self.btn_clear)

        btn_bar.addStretch()
        tbl_layout.addLayout(btn_bar)

        # 테이블 위젯
        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels([
            "선택", "파일명", "형식", "파일 크기", "출력 XTF 파일 경로", "변환 상태"
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setStyleSheet("""
            QTableWidget {
                background-color: #161b22;
                alternate-background-color: #0d1117;
                color: #c9d1d9;
                gridline-color: #30363d;
                border: 1px solid #30363d;
            }
            QHeaderView::section {
                background-color: #21262d;
                color: #f0f6fc;
                border: 1px solid #30363d;
                padding: 4px;
                font-weight: bold;
            }
        """)
        tbl_layout.addWidget(self.tbl)
        main_layout.addWidget(grp_table, stretch=3)

        # 출력 및 변환 옵션 그룹
        grp_opts = QGroupBox("변환 및 출력 설정")
        opt_layout = QGridLayout(grp_opts)

        self.rad_same_dir = QRadioButton("원본 파일과 동일한 폴더에 XTF 생성 (권장)")
        self.rad_same_dir.setChecked(True)
        self.rad_custom_dir = QRadioButton("지정된 폴더에 일괄 저장:")
        bg = QButtonGroup(self)
        bg.addButton(self.rad_same_dir)
        bg.addButton(self.rad_custom_dir)
        self.rad_same_dir.toggled.connect(self._on_dir_mode_changed)

        opt_layout.addWidget(self.rad_same_dir, 0, 0, 1, 3)

        h_custom = QHBoxLayout()
        h_custom.addWidget(self.rad_custom_dir)
        self.txt_out_dir = QLineEdit()
        self.txt_out_dir.setEnabled(False)
        self.txt_out_dir.setPlaceholderText("출력 대상 폴더 경로를 선택하세요...")
        h_custom.addWidget(self.txt_out_dir)
        self.btn_browse_dir = QPushButton("찾아보기...")
        self.btn_browse_dir.setEnabled(False)
        self.btn_browse_dir.clicked.connect(self._on_browse_output_dir)
        h_custom.addWidget(self.btn_browse_dir)
        opt_layout.addLayout(h_custom, 1, 0, 1, 3)

        self.chk_auto_load = QCheckBox("변환 완료 후 메인 작업창에 자동 로드 (워터폴/항적 표시 및 모자이킹 준비)")
        self.chk_auto_load.setChecked(True)
        self.chk_auto_load.setStyleSheet("color: #58a6ff; font-weight: bold;")
        opt_layout.addWidget(self.chk_auto_load, 2, 0, 1, 3)

        main_layout.addWidget(grp_opts)

        # 진행률 및 로그 그룹
        grp_prog = QGroupBox("진행 상황 및 로그")
        prog_layout = QVBoxLayout(grp_prog)

        h_prog = QHBoxLayout()
        self.lbl_progress = QLabel("대기 중...")
        h_prog.addWidget(self.lbl_progress)
        self.pbar = QProgressBar()
        self.pbar.setValue(0)
        self.pbar.setTextVisible(True)
        h_prog.addWidget(self.pbar)
        prog_layout.addLayout(h_prog)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(120)
        self.txt_log.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #58a6ff;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
                border: 1px solid #30363d;
            }
        """)
        prog_layout.addWidget(self.txt_log)
        main_layout.addWidget(grp_prog, stretch=2)

        # 하단 액션 버튼
        h_action = QHBoxLayout()
        self.btn_convert = QPushButton("🚀 변환 시작")
        self.btn_convert.setStyleSheet("""
            QPushButton {
                background-color: #238636;
                color: white;
                font-weight: bold;
                font-size: 13px;
                padding: 8px 18px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #2ea043;
            }
            QPushButton:disabled {
                background-color: #30363d;
                color: #8b949e;
            }
        """)
        self.btn_convert.clicked.connect(self.start_conversion)
        h_action.addWidget(self.btn_convert)

        self.btn_cancel = QPushButton("⏹ 작업 중단")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_conversion)
        h_action.addWidget(self.btn_cancel)

        h_action.addStretch()

        self.btn_close = QPushButton("닫기")
        self.btn_close.clicked.connect(self.close)
        h_action.addWidget(self.btn_close)

        main_layout.addLayout(h_action)

    # ==========================================================================
    # 파일 목록 관리
    # ==========================================================================
    def _on_add_files_clicked(self, checked=False):
        files, _ = QFileDialog.getOpenFileNames(
            self, "변환할 소나 파일 선택 (복수 선택 가능)", "",
            "지원 소나 파일 (*.sl2 *.sl3 *.slg *.dat *.son);;Lowrance 로그 (*.sl2 *.sl3 *.slg);;Humminbird 파일 (*.dat *.son);;모든 파일 (*)"
        )
        if files:
            self.add_files(files)

    def _on_add_dir_clicked(self, checked=False):
        folder = QFileDialog.getExistingDirectory(self, "소나 파일이 포함된 폴더 선택")
        if not folder:
            return

        supported_exts = {'.sl2', '.sl3', '.slg', '.dat', '.son'}
        found_files = []
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_exts:
                    # Humminbird B001/B002 중복 방지 (DAT가 있으면 DAT 우선)
                    found_files.append(os.path.join(root, f))

        # Humminbird 폴더의 경우 DAT가 있으면 하위 SON 파일들은 개별 등록 생략
        filtered = []
        dats = {os.path.splitext(f)[0].lower() for f in found_files if f.lower().endswith('.dat')}
        for f in found_files:
            ext = os.path.splitext(f)[1].lower()
            if ext == '.son':
                # 같은 디렉터리나 상위에 .dat가 있으면 스킵
                parent_dir = os.path.dirname(f)
                parent_name = os.path.basename(parent_dir).lower()
                if parent_name in dats:
                    continue
            filtered.append(f)

        if filtered:
            self.add_files(filtered)
            self._log(f"📁 폴더 '{os.path.basename(folder)}'에서 {len(filtered)}개 소나 파일 탐색 완료.")
        else:
            QMessageBox.information(self, "파일 없음", "선택한 폴더에서 지원 소나 파일을 찾지 못했습니다.")

    def add_files(self, paths: List[str]):
        """파일들을 테이블에 등록 및 정보 프로브."""
        for p in paths:
            if not os.path.isfile(p):
                continue
            # 이미 등록된 파일인지 확인
            if any(r['path'] == p for r in self._file_records):
                continue

            info = inspect_sonar_file(p)
            out_xtf = self._determine_output_path(p)

            rec = {
                'path': p,
                'format': info['format'],
                'size_mb': info['size_mb'],
                'out_xtf': out_xtf,
                'status': "준비",
            }
            self._file_records.append(rec)
            self._append_table_row(rec)

        self._log(f"➕ 총 {len(self._file_records)}개 파일이 변환 대기열에 있습니다.")

    def _determine_output_path(self, src_path: str) -> str:
        """출력 XTF 경로 계산."""
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        if self.rad_same_dir.isChecked():
            out_dir = os.path.dirname(src_path)
        else:
            out_dir = self.txt_out_dir.text().strip() or os.path.dirname(src_path)
        return os.path.join(out_dir, f"{base_name}.xtf")

    def _append_table_row(self, rec: Dict[str, Any]):
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        # Col 0: 체크박스
        chk_item = QTableWidgetItem()
        chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk_item.setCheckState(Qt.Checked)
        self.tbl.setItem(row, 0, chk_item)

        # Col 1: 파일명
        f_item = QTableWidgetItem(os.path.basename(rec['path']))
        f_item.setToolTip(rec['path'])
        self.tbl.setItem(row, 1, f_item)

        # Col 2: 포맷
        fmt_item = QTableWidgetItem(rec['format'])
        fmt_item.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(row, 2, fmt_item)

        # Col 3: 크기
        sz_item = QTableWidgetItem(f"{rec['size_mb']:.1f} MB")
        sz_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.tbl.setItem(row, 3, sz_item)

        # Col 4: 출력 XTF 경로
        out_item = QTableWidgetItem(rec['out_xtf'])
        self.tbl.setItem(row, 4, out_item)

        # Col 5: 상태
        st_item = QTableWidgetItem(rec['status'])
        st_item.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(row, 5, st_item)

    def _on_remove_selected(self):
        rows_to_remove = []
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 0).checkState() == Qt.Checked or self.tbl.isItemSelected(self.tbl.item(r, 1)):
                rows_to_remove.append(r)

        for r in reversed(rows_to_remove):
            self.tbl.removeRow(r)
            if r < len(self._file_records):
                del self._file_records[r]

    def _on_clear_all(self):
        self.tbl.setRowCount(0)
        self._file_records.clear()
        self._log("대기열을 모두 비웠습니다.")

    def _on_dir_mode_changed(self):
        is_custom = self.rad_custom_dir.isChecked()
        self.txt_out_dir.setEnabled(is_custom)
        self.btn_browse_dir.setEnabled(is_custom)
        self._refresh_output_paths()

    def _on_browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "출력 XTF 저장 폴더 선택")
        if d:
            self.txt_out_dir.setText(d)
            self._refresh_output_paths()

    def _refresh_output_paths(self):
        for r, rec in enumerate(self._file_records):
            new_out = self._determine_output_path(rec['path'])
            rec['out_xtf'] = new_out
            if r < self.tbl.rowCount():
                self.tbl.item(r, 4).setText(new_out)

    def _log(self, msg: str):
        t_str = time.strftime("[%H:%M:%S] ")
        self.txt_log.append(t_str + msg)

    # ==========================================================================
    # 변환 실행 제어
    # ==========================================================================
    def start_conversion(self):
        if self.worker and self.worker.isRunning():
            return

        tasks = []
        for r in range(self.tbl.rowCount()):
            chk = self.tbl.item(r, 0)
            if chk and chk.checkState() == Qt.Checked:
                src_path = self._file_records[r]['path']
                dst_path = self.tbl.item(r, 4).text()
                tasks.append({
                    'row': r,
                    'src_path': src_path,
                    'dst_path': dst_path
                })

        if not tasks:
            QMessageBox.information(self, "선택 파일 없음", "변환할 파일을 1개 이상 체크해 주세요.")
            return

        self.btn_convert.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_add_files.setEnabled(False)
        self.btn_add_dir.setEnabled(False)
        self.btn_remove_sel.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.pbar.setValue(0)

        self.worker = SonarConvertWorker(tasks, parent=self)
        self.worker.file_started.connect(self._on_worker_file_started)
        self.worker.file_progress.connect(self._on_worker_file_progress)
        self.worker.file_finished.connect(self._on_worker_file_finished)
        self.worker.all_finished.connect(self._on_worker_all_finished)
        self.worker.log_message.connect(self._log)
        self.worker.start()

    def cancel_conversion(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._log("⏹ 변환 중단 요청됨...")

    @Slot(int, str)
    def _on_worker_file_started(self, row: int, src_path: str):
        if row < self.tbl.rowCount():
            item = self.tbl.item(row, 5)
            item.setText("변환 중...")
            item.setForeground(QColor("#58a6ff"))

    @Slot(int, int, str)
    def _on_worker_file_progress(self, row: int, pct: int, msg: str):
        self.pbar.setValue(pct)
        self.lbl_progress.setText(msg)
        if row < self.tbl.rowCount():
            self.tbl.item(row, 5).setText(f"{pct}% ({msg[:12]}..)")

    @Slot(int, str, bool, str)
    def _on_worker_file_finished(self, row: int, xtf_path: str, success: bool, msg: str):
        if row < self.tbl.rowCount():
            item = self.tbl.item(row, 5)
            item.setText(msg)
            if success:
                item.setForeground(QColor("#3fb950"))
            else:
                item.setForeground(QColor("#f85149"))

    @Slot(list)
    def _on_worker_all_finished(self, successful_xtf: List[str]):
        self.btn_convert.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_add_files.setEnabled(True)
        self.btn_add_dir.setEnabled(True)
        self.btn_remove_sel.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.pbar.setValue(100)
        self.lbl_progress.setText(f"완료 (총 {len(successful_xtf)}개 파일 변환됨)")

        if successful_xtf:
            QMessageBox.information(
                self, "변환 완료",
                f"총 {len(successful_xtf)}개 소나 파일이 XTF로 성공적으로 변환되었습니다!"
            )
            if self.chk_auto_load.isChecked():
                self.converted_files_ready.emit(successful_xtf)
                self.accept()
        else:
            QMessageBox.warning(self, "변환 실패", "변환된 파일이 없습니다. 로그를 확인해 주세요.")
