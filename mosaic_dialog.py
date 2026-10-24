

# -*- coding: utf-8 -*-
"""mosaic_dialog.py - 모자이크 생성 파라미터 다이얼로그 (v3)

v3 변경점
- 탭(QTabWidget) 3분할 + 각 탭 QScrollArea → 다이얼로그 높이 대폭 축소
- OK/Cancel 버튼을 탭 바깥 하단에 고정 배치하여 항상 보이도록 수정
- 화면 available geometry 기준으로 최대 크기 자동 제한
- Qt5/Qt6 enum 차이(StandardButton, DialogCode) 호환 처리

기존 API 유지: MosaicDialog, ensure_app, get_params, ask,
              files/set_files/add_files/params/_collect
"""
from __future__ import annotations

import os
import sys

_ORDER = ("PySide6", "PyQt5", "PyQt6", "PySide2")


def _load_qt():
    """이미 import 된 바인딩을 최우선으로 선택."""
    order = [n for n in _ORDER if n in sys.modules]
    order += [n for n in _ORDER if n not in order]
    last = None
    for n in order:
        try:
            core = __import__(n + ".QtCore", fromlist=["QtCore"])
            widg = __import__(n + ".QtWidgets", fromlist=["QtWidgets"])
            return n, core, widg
        except Exception as e:  # noqa: BLE001
            last = e
    raise ImportError("Qt 바인딩을 찾을 수 없습니다: %s" % last)


BINDING, QtCore, QtWidgets = _load_qt()
Qt = QtCore.Qt

QDialog = QtWidgets.QDialog
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QLineEdit = QtWidgets.QLineEdit
QListWidget = QtWidgets.QListWidget
QCheckBox = QtWidgets.QCheckBox
QComboBox = QtWidgets.QComboBox
QSpinBox = QtWidgets.QSpinBox
QDoubleSpinBox = QtWidgets.QDoubleSpinBox
QFileDialog = QtWidgets.QFileDialog
QDialogButtonBox = QtWidgets.QDialogButtonBox
QApplication = QtWidgets.QApplication
QTabWidget = QtWidgets.QTabWidget
QScrollArea = QtWidgets.QScrollArea
QSizePolicy = QtWidgets.QSizePolicy

# Qt5 / Qt6 enum 호환
_SB = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
_DC = getattr(QDialog, "DialogCode", QDialog)

FILE_KEYS = ("files", "inputs", "paths", "xtf_files", "xtf", "xtf_paths",
             "xtfs", "file_list", "input_files", "src_files", "sources")
OUTDIR_KEYS = ("outdir", "out_dir", "output_dir", "out_root", "dest_dir")
NAME_KEYS = ("name", "mission", "mission_name", "out_name", "basename")
RES_KEYS = ("res", "resolution", "cell", "cell_size", "pixel_size")
EPSG_KEYS = ("epsg", "out_epsg", "target_epsg", "crs_epsg", "dst_epsg")


def ensure_app():
    """QApplication 이 없으면 생성해서 반환."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if sys.argv else ["mosaic_dialog"])
    return app


def _scroll(inner):
    """위젯을 스크롤 영역으로 감싸 반환."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


class MosaicDialog(QDialog):
    """모자이크 생성 파라미터 입력 다이얼로그."""

    def __init__(self, parent=None, files=None):
        ensure_app()
        super().__init__(parent)
        self.setWindowTitle("모자이크 생성 파라미터")
        self._build()
        self._fit_screen()
        if files:
            self.set_files(files)

    # ------------------------------------------------ 크기 조정
    def _fit_screen(self):
        w, h = 620, 520
        try:
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry()
            w = min(w, max(420, g.width() - 80))
            h = min(h, max(360, g.height() - 120))
        except Exception:  # noqa: BLE001
            pass
        self.resize(w, h)
        self.setMinimumSize(420, 360)

    # ------------------------------------------------ UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.addTab(_scroll(self._tab_io()), "입력 / 출력")
        self.tabs.addTab(_scroll(self._tab_proc()), "신호 처리")
        self.tabs.addTab(_scroll(self._tab_raster()), "래스터화")
        root.addWidget(self.tabs, 1)

        # --- 하단 고정 버튼 ---
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.lbl_status = QLabel("파일 0개")
        bar.addWidget(self.lbl_status)
        bar.addStretch(1)

        btns = QDialogButtonBox(_SB.Ok | _SB.Cancel)
        ok = btns.button(_SB.Ok)
        ok.setText("모자이크 생성")
        ok.setDefault(True)
        ok.setMinimumWidth(130)
        btns.button(_SB.Cancel).setText("취소")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        bar.addWidget(btns)

        w_bar = QWidget()
        w_bar.setLayout(bar)
        w_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(w_bar, 0)

    # ---- 탭 1 : 입력 / 출력 ----
    def _tab_io(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        g_in = QGroupBox("입력 XTF")
        v_in = QVBoxLayout(g_in)
        v_in.setContentsMargins(8, 6, 8, 8)
        v_in.setSpacing(4)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(90)
        self.lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        v_in.addWidget(self.lst, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        b_add = QPushButton("추가...")
        b_del = QPushButton("선택 제거")
        b_clr = QPushButton("모두 지우기")
        b_add.clicked.connect(self._add_files)
        b_del.clicked.connect(self._del_sel)
        b_clr.clicked.connect(self._clear_files)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addWidget(b_clr)
        row.addStretch(1)
        v_in.addLayout(row)
        v.addWidget(g_in, 1)

        g_out = QGroupBox("출력")
        f = QFormLayout(g_out)
        f.setContentsMargins(8, 6, 8, 8)
        f.setSpacing(5)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.ed_dir = QLineEdit(os.path.join(os.getcwd(), "exported_mosaics"))
        b_dir = QPushButton("...")
        b_dir.setFixedWidth(32)
        b_dir.clicked.connect(self._pick_dir)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        h.addWidget(self.ed_dir, 1)
        h.addWidget(b_dir)
        w_dir = QWidget()
        w_dir.setLayout(h)
        f.addRow("출력 폴더", w_dir)

        self.ed_name = QLineEdit("mosaic")
        f.addRow("이름", self.ed_name)

        self.sp_epsg = QSpinBox()
        self.sp_epsg.setRange(1000, 999999)
        self.sp_epsg.setValue(32652)
        self.sp_res = QDoubleSpinBox()
        self.sp_res.setRange(0.01, 50.0)
        self.sp_res.setDecimals(3)
        self.sp_res.setSingleStep(0.05)
        self.sp_res.setValue(0.2)
        self.sp_res.setSuffix(" m")

        hr = QHBoxLayout()
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(6)
        hr.addWidget(QLabel("EPSG"))
        hr.addWidget(self.sp_epsg)
        hr.addSpacing(12)
        hr.addWidget(QLabel("해상도"))
        hr.addWidget(self.sp_res)
        hr.addStretch(1)
        w_r = QWidget()
        w_r.setLayout(hr)
        f.addRow("좌표계 / 격자", w_r)

        v.addWidget(g_out, 0)
        v.addStretch(1)
        return page

    # ---- 탭 2 : 신호 처리 ----
    def _tab_proc(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        g_ch = QGroupBox("채널 및 보정")
        f = QFormLayout(g_ch)
        f.setContentsMargins(8, 6, 8, 8)
        f.setSpacing(4)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.cb_port = QCheckBox("Port")
        self.cb_port.setChecked(True)
        self.cb_stbd = QCheckBox("Starboard")
        self.cb_stbd.setChecked(True)
        hc = QHBoxLayout()
        hc.setContentsMargins(0, 0, 0, 0)
        hc.addWidget(self.cb_port)
        hc.addWidget(self.cb_stbd)
        hc.addStretch(1)
        w_ch = QWidget()
        w_ch.setLayout(hc)
        f.addRow("채널", w_ch)

        self.cb_swap_ch = QCheckBox("좌우 채널 교환 (Port ↔ Starboard Swap)")
        self.cb_swap_ch.setChecked(False)
        self.cb_flip = QCheckBox("Port 좌우 반전 (data[0][::-1])")
        self.cb_flip.setChecked(True)
        self.cb_slant = QCheckBox("Slant range 보정")
        self.cb_slant.setChecked(True)
        self.cb_wc = QCheckBox("Water column 제거")
        self.cb_wc.setChecked(True)
        self.cb_avc = QCheckBox("AVC / AVG 보정")
        self.cb_avc.setChecked(True)
        for cb in (self.cb_swap_ch, self.cb_flip, self.cb_slant, self.cb_wc, self.cb_avc):
            f.addRow("", cb)

        self.sp_avc_win = QSpinBox()
        self.sp_avc_win.setRange(3, 999)
        self.sp_avc_win.setSingleStep(2)
        self.sp_avc_win.setValue(51)
        f.addRow("AVC 윈도우(핑)", self.sp_avc_win)
        v.addWidget(g_ch)

        g_bt = QGroupBox("바탐 (Bottom / Altitude)")
        fb = QFormLayout(g_bt)
        fb.setContentsMargins(8, 6, 8, 8)
        fb.setSpacing(4)
        fb.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.cb_sidecar = QCheckBox("bottom sidecar CSV 사용")
        self.cb_sidecar.setChecked(True)
        fb.addRow("", self.cb_sidecar)
        self.cb_track = QCheckBox("없으면 자동 bottom tracking")
        self.cb_track.setChecked(True)
        fb.addRow("", self.cb_track)
        self.sp_smooth = QSpinBox()
        self.sp_smooth.setRange(1, 501)
        self.sp_smooth.setSingleStep(2)
        self.sp_smooth.setValue(11)
        fb.addRow("평활 윈도우", self.sp_smooth)
        v.addWidget(g_bt)

        v.addStretch(1)
        return page

    # ---- 탭 3 : 래스터화 ----
    def _tab_raster(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        g = QGroupBox("합성 및 후처리")
        f = QFormLayout(g)
        f.setContentsMargins(8, 6, 8, 8)
        f.setSpacing(5)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.cmb_blend = QComboBox()
        self.cmb_blend.addItems(["weighted_mean", "mean", "last", "max"])
        f.addRow("합성 방식", self.cmb_blend)

        self.sp_lo = QDoubleSpinBox()
        self.sp_lo.setRange(0.0, 50.0)
        self.sp_lo.setDecimals(1)
        self.sp_lo.setValue(2.0)
        self.sp_lo.setSuffix(" %")
        self.sp_hi = QDoubleSpinBox()
        self.sp_hi.setRange(50.0, 100.0)
        self.sp_hi.setDecimals(1)
        self.sp_hi.setValue(98.0)
        self.sp_hi.setSuffix(" %")
        hcl = QHBoxLayout()
        hcl.setContentsMargins(0, 0, 0, 0)
        hcl.setSpacing(6)
        hcl.addWidget(QLabel("하위"))
        hcl.addWidget(self.sp_lo)
        hcl.addSpacing(12)
        hcl.addWidget(QLabel("상위"))
        hcl.addWidget(self.sp_hi)
        hcl.addStretch(1)
        w_cl = QWidget()
        w_cl.setLayout(hcl)
        f.addRow("명암 클립", w_cl)

        self.sp_fill = QSpinBox()
        self.sp_fill.setRange(0, 25)
        self.sp_fill.setValue(2)
        f.addRow("구멍 메움(px)", self.sp_fill)

        self.cb_alpha = QCheckBox("알파 채널 포함")
        self.cb_alpha.setChecked(True)
        f.addRow("", self.cb_alpha)

        self.cmb_alpha = QComboBox()
        self.cmb_alpha.addItems(["gray", "rgba", "mask"])
        f.addRow("알파 모드", self.cmb_alpha)

        v.addWidget(g)
        v.addStretch(1)
        return page

    # ------------------------------------------------ 파일 조작
    def _add_files(self):
        sel, _ = QFileDialog.getOpenFileNames(
            self, "XTF 파일 선택", "", "XTF Files (*.xtf);;All Files (*)")
        if sel:
            self.add_files(sel)

    def _del_sel(self):
        for it in self.lst.selectedItems():
            self.lst.takeItem(self.lst.row(it))
        self._update_status()

    def _clear_files(self):
        self.lst.clear()
        self._update_status()

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "출력 폴더 선택", self.ed_dir.text())
        if d:
            self.ed_dir.setText(d)

    def add_files(self, paths):
        cur = set(self.files())
        for p in paths:
            if isinstance(p, str) and p.strip() and p not in cur:
                self.lst.addItem(p)
                cur.add(p)
        self._auto_name()
        self._update_status()

    def set_files(self, paths):
        self.lst.clear()
        self.add_files(sorted(paths))

    setFiles = set_files
    set_inputs = set_files

    def files(self):
        return [self.lst.item(i).text() for i in range(self.lst.count())]

    def _auto_name(self):
        f = self.files()
        if f and self.ed_name.text() in ("", "mosaic"):
            self.ed_name.setText(os.path.splitext(os.path.basename(f[0]))[0])

    def _update_status(self):
        n = self.lst.count()
        self.lbl_status.setText("파일 %d개" % n)

    # ------------------------------------------------ 확인
    def _on_ok(self):
        if not self.files():
            QtWidgets.QMessageBox.warning(
                self, "입력 없음", "XTF 파일을 1개 이상 추가하세요.")
            self.tabs.setCurrentIndex(0)
            return
        if not (self.cb_port.isChecked() or self.cb_stbd.isChecked()):
            QtWidgets.QMessageBox.warning(
                self, "채널 없음", "Port 또는 Starboard 중 하나는 선택해야 합니다.")
            self.tabs.setCurrentIndex(1)
            return
        d = self.ed_dir.text().strip()
        if d:
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(
                    self, "폴더 오류", "출력 폴더를 만들 수 없습니다:\n%s" % e)
                self.tabs.setCurrentIndex(0)
                return
        self.accept()

    # ------------------------------------------------ 결과
    def _collect(self):
        files = self.files()
        p = {
            "epsg": int(self.sp_epsg.value()),
            "res": float(self.sp_res.value()),
            "outdir": self.ed_dir.text().strip(),
            "name": self.ed_name.text().strip() or "mosaic",
            "use_port": self.cb_port.isChecked(),
            "use_stbd": self.cb_stbd.isChecked(),
            "swap_channels": self.cb_swap_ch.isChecked(),
            "flip_port": self.cb_flip.isChecked(),
            "slant_correct": self.cb_slant.isChecked(),
            "remove_water_column": self.cb_wc.isChecked(),
            "avc": self.cb_avc.isChecked(),
            "avc_window": int(self.sp_avc_win.value()),
            "use_sidecar": self.cb_sidecar.isChecked(),
            "auto_track": self.cb_track.isChecked(),
            "bottom_smooth": int(self.sp_smooth.value()),
            "blend": self.cmb_blend.currentText(),
            "lo": float(self.sp_lo.value()),
            "hi": float(self.sp_hi.value()),
            "fill_radius": int(self.sp_fill.value()),
            "alpha": self.cb_alpha.isChecked(),
            "alpha_mode": self.cmb_alpha.currentText(),
        }
        for k in FILE_KEYS:
            p[k] = list(files)
        for k in OUTDIR_KEYS:
            p[k] = p["outdir"]
        for k in NAME_KEYS:
            p[k] = p["name"]
        for k in RES_KEYS:
            p[k] = p["res"]
        for k in EPSG_KEYS:
            p[k] = p["epsg"]
        return p

    collect = _collect

    def params(self):
        return self._collect()

    get_params = params
    values = params
    result_params = params


def get_params(parent=None, files=None):
    """다이얼로그를 띄우고 파라미터 dict 반환 (취소 시 None)."""
    ensure_app()
    dlg = MosaicDialog(parent, files)
    if dlg.exec() in (1, _DC.Accepted):
        return dlg.params()
    return None


ask = get_params


if __name__ == "__main__":
    app = ensure_app()
    print("Qt binding:", BINDING)
    r = get_params()
    if r is None:
        print("취소됨")
    else:
        for k in ("files", "epsg", "res", "outdir", "name", "blend"):
            print(" ", k, "=", r.get(k))

