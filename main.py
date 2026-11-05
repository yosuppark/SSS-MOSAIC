
"""main.py — XTF Processor V1.0 (다중 파일 + 모자이크 오버레이)
- 복수 XTF 동시 열기 (Ctrl+O)
- 파일별 색상 항적/coverage 지도 표시, 활성 파일 강조
- Waterfall 하단 파일 순번 표시 + 좌우 전환 (◀/▶, PgUp/PgDn)
- hover → 지도 핑 위치 마커 / contact → 저장 + 지도/Waterfall 마커
- 모자이크 생성(알파 채널 포함) 및 지도 위 오버레이 (Ctrl+M)
"""
import proj_env
import bounds_utils
import inspect
try:
    import rasterio.serde
    import rasterio._shim
    import rasterio.control
    import rasterio.sample
except ImportError:
    pass
# main.py 최상단에 추가할 코드
import os
import sys

# QtWebEngine 안정화 플래그 (Chromium 샌드박스 충돌 방지 및 단독 실행 보장)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

# PyInstaller 단일 파일 실행 시 임시 폴더 경로를 시스템 참조 경로에 강제 추가
if getattr(sys, 'frozen', False):
    ext_dir = sys._MEIPASS
    if ext_dir not in sys.path:
        sys.path.insert(0, ext_dir)

import math
import inspect
import traceback

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QSplitter,
    QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSlider, QMessageBox,
    QDoubleSpinBox, QButtonGroup, QFrame, QProgressDialog, QMenu,
)

from xtf_loader import load_xtf
from waterfall_view import WaterfallView
from map_view import MapView
from processing.contacts import save_contact
from processing.bottom_io import sync_bottom, save_bottom
from processing.nav_io import sync_nav, save_nav
from processing.nav_interpolator import process_navigation, apply_interpolated_nav_to_meta
from processing.nav_dialog import NavProcessDialog
from processing.fishbone_dialog import NavHeadingTunerDialog
from processing.motion_ripple_dialog import TowfishMotionRippleDialog
from processing.sonar_converter import convert_sonar_to_xtf
from processing.converter_dialog import SonarConverterDialog
from processing.agc import apply_agc
from bounds_utils import bounds_from_raster


# 시스템 환경 변수에 등록된 기존 PROJ 경로를 먼저 완전히 제거 (오염 차단)
os.environ.pop('PROJ_DATA', None)
os.environ.pop('PROJ_LIB', None)

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    
    # 2. 실행 파일 내부의 올바른 최신 proj 데이터 폴더 위치 지정
    proj_data_path = os.path.join(base_path, 'pyproj', 'proj_dir', 'share', 'proj')
    if not os.path.exists(proj_data_path):
        proj_data_path = os.path.join(base_path, 'rasterio', 'proj_data')
        
    # 3. 시스템 환경 변수에 강제 고정
    os.environ['PROJ_DATA'] = proj_data_path
    os.environ['PROJ_LIB'] = proj_data_path
    
    # 4. ★핵심★ pyproj 라이브러리 내부의 경로 설정 함수를 강제로 덮어쓰기
    try:
        import pyproj.datadir
        pyproj.datadir.set_data_dir(proj_data_path)
    except Exception:
        pass

    
# 파일별 항적 색상 팔레트
COLORS = ["#e6194b", "#3cb44e", "#4363d8", "#f58231", "#911eb4",
          "#46f0f0", "#f032e6", "#bcf60c", "#800000", "#008080"]
SWATH_STEP = 25          # coverage 폴리곤 핑 샘플 간격
AGC_WINDOW = 301
MOSAIC_MAX_DIM = 4096    # 지도 표시용 최대 픽셀 크기

# 빌더/다이얼로그가 쓸 수 있는 파라미터 키 별칭
FILE_KEYS = ("files", "inputs", "paths", "xtf_files", "xtf", "xtf_paths",
             "xtfs", "file_list", "input_files", "src_files", "sources")
OUTDIR_KEYS = ("outdir", "out_dir", "output_dir", "out_root", "dest_dir")
NAME_KEYS = ("name", "mission", "mission_name", "out_name", "basename")
RES_KEYS = ("res", "resolution", "cell", "cell_size", "pixel_size")
EPSG_KEYS = ("epsg", "out_epsg", "target_epsg", "crs_epsg", "dst_epsg")


# ==================================================== 모자이크 읽기 유틸
def read_mosaic_rgba(path, max_dim=MOSAIC_MAX_DIM):
    """GeoTIFF → (RGBA uint8 HxWx4, (lon_min, lat_min, lon_max, lat_max))"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds

    with rasterio.open(path) as src:
        sc = max(1.0, max(src.width, src.height) / float(max_dim))
        w = max(1, int(round(src.width / sc)))
        h = max(1, int(round(src.height / sc)))
        arr = src.read(out_shape=(src.count, h, w),
                       resampling=Resampling.average)
        cnt = int(src.count)
        nod = src.nodata
        bnd = src.bounds
        crs = src.crs

    def _u8(a):
        a = np.asarray(a)
        if a.dtype == np.uint8:
            return a
        f = a.astype(np.float64)
        v = f[np.isfinite(f)]
        if v.size == 0:
            return np.zeros(a.shape, np.uint8)
        p0, p1 = np.percentile(v, 2.0), np.percentile(v, 98.0)
        if p1 <= p0:
            p0, p1 = float(v.min()), float(v.max()) + 1e-9
        g = np.clip((f - p0) / (p1 - p0), 0.0, 1.0) * 255.0
        return np.nan_to_num(g).astype(np.uint8)

    if cnt >= 4:
        rgb = np.stack([_u8(arr[0]), _u8(arr[1]), _u8(arr[2])], axis=-1)
        alpha = _u8(arr[3])
    elif cnt == 3:
        rgb = np.stack([_u8(arr[0]), _u8(arr[1]), _u8(arr[2])], axis=-1)
        alpha = np.where(np.any(arr[:3] != 0, axis=0), 255, 0).astype(np.uint8)
    elif cnt == 2:
        g = _u8(arr[0])
        rgb = np.stack([g, g, g], axis=-1)
        alpha = _u8(arr[1])
    else:
        g = _u8(arr[0])
        rgb = np.stack([g, g, g], axis=-1)
        base = arr[0].astype(np.float64)
        m = np.isfinite(base)
        m &= (base != (nod if nod is not None else 0.0))
        alpha = np.where(m, 255, 0).astype(np.uint8)

    rgba = np.dstack([rgb, alpha])

    if crs is not None:
        lo0, la0, lo1, la1 = transform_bounds(
            crs, "EPSG:4326", bnd.left, bnd.bottom, bnd.right, bnd.top,
            densify_pts=21)
    else:
        lo0, la0, lo1, la1 = bnd.left, bnd.bottom, bnd.right, bnd.top
    return rgba, (float(lo0), float(la0), float(lo1), float(la1))


def _as_path_list(v):
    """값이 경로 리스트/문자열이면 존재하는 경로 리스트로 변환."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    try:
        for x in v:
            if isinstance(x, str) and x.strip():
                out.append(x)
            elif isinstance(x, dict):
                p = x.get("path") or x.get("file")
                if isinstance(p, str) and p.strip():
                    out.append(p)
    except TypeError:
        return []
    return out


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XTF Processor V1.0")
        self.resize(1500, 900)

        # ---- 상태 ----
        self.files = []
        self.cur_idx = -1
        self.agc_on = False
        self.brightness = 0        # -100 ~ +100
        self.contrast = 1.0        # 0.2 ~ 3.0
        self.contact_no = 0

        # ---- 모자이크 상태 ----
        self.mosaic_path = None
        self.mosaic_item = None      # pyqtgraph ImageItem 또는 mpl AxesImage
        self.mosaic_kind = None      # "custom" / "pg" / "mpl"
        self.mosaic_opacity = 0.75

        self._build_ui()
        self.wf.bottomEditFinished.connect(self._on_bottom_edited)

    # ================================================ UI
    def _build_ui(self):
        # ---- 메뉴바 영구 보존 및 부모 설정 (Shiboken C++ 소멸 방지) ----
        mb = self.menuBar()

        # ---- 메뉴: 파일 ----
        self.m_file = QMenu("파일(&F)", mb)
        mb.addMenu(self.m_file)

        act_open = QAction("열기(복수 선택)...", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(lambda checked=False: self.open_files())
        self.m_file.addAction(act_open)
        self.act_open = act_open

        act_convert = QAction("소나 파일 포맷 변환 (Humminbird / Lowrance ➔ XTF)...", self)
        act_convert.setShortcut(QKeySequence("Ctrl+T"))
        act_convert.setStatusTip("Humminbird 및 Lowrance 소나 기록 파일을 표준 2채널 XTF로 변환")
        act_convert.triggered.connect(self._on_open_converter_dialog)
        self.m_file.addAction(act_convert)
        self.act_convert = act_convert
        
        self.m_file.addSeparator()

        act_exit = QAction("종료(&X)", self)
        act_exit.setShortcut(QKeySequence.Quit)      # Windows: Ctrl+Q
        act_exit.setShortcuts([QKeySequence.Quit,
                               QKeySequence("Alt+F4")])
        act_exit.setStatusTip("프로그램을 종료합니다")
        act_exit.triggered.connect(self.close)
        self.m_file.addAction(act_exit)
        self.act_exit = act_exit

        # ---- 메뉴: 처리 ----
        self.m_view = QMenu("처리(&P)", mb)
        mb.addMenu(self.m_view)

        self.act_agc = QAction("방사보정 AGC 적용 (Angle Varied Gain)", self, checkable=True)
        self.act_agc.setStatusTip("해저면 First Bottom 기준 각도별 이득 보정(AVG/TVG)을 적용하여 직하방 과포화 억제 및 원거리 감쇄를 보상합니다.")
        self.act_agc.toggled.connect(self._on_agc_toggled)
        self.m_view.addAction(self.act_agc)

        self.act_swap_ch = QAction("좌우 채널 반전 (Port ↔ Starboard Swap)", self, checkable=True)
        self.act_swap_ch.setShortcut(QKeySequence("Ctrl+Shift+X"))
        self.act_swap_ch.setStatusTip("소나 Port와 Starboard 채널 순서를 상호 반대로 교환")
        self.act_swap_ch.toggled.connect(self._on_swap_channels_toggled)
        self.m_view.addAction(self.act_swap_ch)

        act_fishbone = QAction("생선 가시 항법/헤딩 튜너 (Fishbone Inspection)...", self)
        act_fishbone.setShortcut(QKeySequence("Ctrl+H"))
        act_fishbone.setStatusTip("생선 가시 형태 항적/헤딩 시각화 및 부드러운 변위 보간 실시간 튜닝")
        act_fishbone.triggered.connect(self._on_open_fishbone_tuner)
        self.m_view.addAction(act_fishbone)
        self.act_fishbone = act_fishbone

        act_nav = QAction("항법 일괄 보정 및 핑 보간 (Navigation Processing)...", self)
        act_nav.setShortcut(QKeySequence("Ctrl+N"))
        act_nav.setStatusTip("GPS 스파이크 제거, 기준 위치 스무딩 및 매 핑별 위치/헤딩 보간")
        act_nav.triggered.connect(self._on_process_navigation)
        self.m_view.addAction(act_nav)
        self.act_nav = act_nav

        self.m_view.addSeparator()
        self.act_bt_edit = QAction("해저면 추적 및 바탐 편집 (Bottom Tracking / Edit)...", self, checkable=True)
        self.act_bt_edit.setShortcut(QKeySequence("Ctrl+B"))
        self.act_bt_edit.setStatusTip("시드 기반 First Bottom 자동 추적 및 구간 수동 보정")
        self.act_bt_edit.toggled.connect(self._toggle_bt_edit)
        self.m_view.addAction(self.act_bt_edit)

        act_ripple = QAction("토우피쉬 모션 리플 보정 (Motion Ripple Correction)...", self)
        act_ripple.setShortcut(QKeySequence("Ctrl+R"))
        act_ripple.setStatusTip("파도에 의한 롤링/피칭 리플 해석 및 줄무늬 아티팩트 보정")
        act_ripple.triggered.connect(self._on_open_motion_ripple)
        self.m_view.addAction(act_ripple)
        self.act_ripple = act_ripple

        # ---- 메뉴: 모자이크 ----
        self.m_mos = QMenu("모자이크(&M)", mb)
        mb.addMenu(self.m_mos)

        act_build = QAction("모자이크 생성 (알파 포함)...", self)
        act_build.setShortcut(QKeySequence("Ctrl+M"))
        act_build.triggered.connect(self.build_mosaic)
        self.m_mos.addAction(act_build)

        act_load = QAction("모자이크 GeoTIFF 열기...", self)
        act_load.triggered.connect(self.load_mosaic_file)
        self.m_mos.addAction(act_load)

        act_alpha = QAction("기존 GeoTIFF에 알파 추가...", self)
        act_alpha.triggered.connect(self.add_alpha_file)
        self.m_mos.addAction(act_alpha)

        self.m_mos.addSeparator()
        self.act_mos_show = QAction("지도에 모자이크 표시", self, checkable=True)
        self.act_mos_show.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.act_mos_show.setChecked(True)
        self.act_mos_show.toggled.connect(self._on_mosaic_visible)
        self.m_mos.addAction(self.act_mos_show)

        act_mos_del = QAction("모자이크 제거", self)
        act_mos_del.triggered.connect(self.remove_mosaic)
        self.m_mos.addAction(act_mos_del)

        # ---- 메뉴: 도구 ----
        self.m_tool = QMenu("도구(&T)", mb)
        mb.addMenu(self.m_tool)

        act_tool_convert = QAction("소나 파일 포맷 변환기 (Humminbird / Lowrance ➔ XTF)...", self)
        act_tool_convert.setStatusTip("Humminbird 및 Lowrance 소나 기록 파일을 표준 2채널 XTF로 일괄 변환")
        act_tool_convert.triggered.connect(self._on_open_converter_dialog)
        self.m_tool.addAction(act_tool_convert)
        self.act_tool_convert = act_tool_convert

        # ---- 상단 메인 툴바 (빠른 실행 도구) ----
        tb = self.addToolBar("메인 도구")
        tb.setMovable(False)
        tb.setStyleSheet("""
            QToolBar {
                spacing: 8px;
                padding: 4px 10px;
                background-color: #1f242d;
                border-bottom: 1px solid #30363d;
            }
            QPushButton {
                padding: 6px 14px;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
            }
        """)

        btn_open = QPushButton("📂 소나 파일 열기 (Ctrl+O)")
        btn_open.setStyleSheet("background-color: #238636; color: white;")
        btn_open.setToolTip("XTF, Lowrance(SL2/SL3/SLG), Humminbird(DAT/SON) 소나 파일을 열고 모자이킹 준비")
        btn_open.clicked.connect(lambda checked=False: self.open_files())
        tb.addWidget(btn_open)

        btn_conv = QPushButton("🔄 소나 포맷 변환기 (Ctrl+T)")
        btn_conv.setStyleSheet("background-color: #1f6feb; color: white;")
        btn_conv.setToolTip("Humminbird 및 Lowrance 소나 기록 파일을 표준 2채널 XTF로 일괄 변환")
        btn_conv.clicked.connect(self._on_open_converter_dialog)
        tb.addWidget(btn_conv)

        tb.addSeparator()

        btn_mos = QPushButton("🗺️ 모자이크 생성 (Ctrl+M)")
        btn_mos.setStyleSheet("background-color: #30363d; color: #f0f6fc;")
        btn_mos.setToolTip("선택된 소나 라인들로부터 지리참조 GeoTIFF 모자익을 생성합니다.")
        btn_mos.clicked.connect(self.build_mosaic)
        tb.addWidget(btn_mos)

        btn_rip = QPushButton("🌊 모션 리플 보정 (Ctrl+R)")
        btn_rip.setStyleSheet("background-color: #30363d; color: #f0f6fc;")
        btn_rip.setToolTip("토우피쉬 롤링/피칭 리플 해석 및 보정 다이얼로그를 엽니다.")
        btn_rip.clicked.connect(self._on_open_motion_ripple)
        tb.addWidget(btn_rip)

        btn_fish = QPushButton("🐟 Fishbone 튜너 (Ctrl+H)")
        btn_fish.setStyleSheet("background-color: #30363d; color: #f0f6fc;")
        btn_fish.setToolTip("생선 가시 항적/헤딩 보간 실시간 튜너를 엽니다.")
        btn_fish.clicked.connect(self._on_open_fishbone_tuner)
        tb.addWidget(btn_fish)

        # ---- 좌: 지도 / 우: waterfall + 하단바 ----
        self.map = MapView()
        self.wf = WaterfallView()
        self.wf.pingHovered.connect(self._on_hover)
        self.wf.contactAdded.connect(self._on_contact)
        self.wf.bottomProgress.connect(self._on_bottom_progress)
        self.wf.roiSelected.connect(self._on_roi_selected)
        self.ripple_dialog = None

        # 하단 네비게이션 바
        self.btn_prev = QPushButton("\u25c0")
        self.btn_next = QPushButton("\u25b6")
        self.btn_prev.setFixedWidth(40)
        self.btn_next.setFixedWidth(40)
        self.btn_prev.setShortcut(QKeySequence(Qt.Key_PageUp))
        self.btn_next.setShortcut(QKeySequence(Qt.Key_PageDown))
        self.btn_prev.clicked.connect(lambda: self._switch_file(-1))
        self.btn_next.clicked.connect(lambda: self._switch_file(+1))
        self.lbl_file = QPushButton("📂 소나 파일 열기 (현재: 파일 없음 - 클릭하여 파일 선택)")
        self.lbl_file.setToolTip("클릭하여 XTF, Lowrance(SL2/SL3), Humminbird(DAT/SON) 소나 파일을 엽니다 (단축키: Ctrl+O)")
        self.lbl_file.setStyleSheet("""
            QPushButton {
                background-color: #21262d;
                color: #58a6ff;
                border: 1px solid #388bfd;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1f6feb;
                color: #ffffff;
            }
        """)
        self.lbl_file.clicked.connect(lambda checked=False: self.open_files())

        # 좌우 채널 반전 버튼
        self.btn_swap_ch = QPushButton("좌우 반전 (P↔S)")
        self.btn_swap_ch.setCheckable(True)
        self.btn_swap_ch.setToolTip("소나 Port와 Starboard 채널 순서를 상호 교환합니다 (Ctrl+Shift+X)")
        self.btn_swap_ch.toggled.connect(self._on_swap_channels_toggled)

        # 바탐 편집 버튼
        self.btn_bt_edit = QPushButton("바탐 편집 시작")
        self.btn_bt_edit.setCheckable(True)
        self.btn_bt_edit.toggled.connect(self._toggle_bt_edit)

        # 모션 리플 보정 버튼
        self.btn_ripple = QPushButton("모션 리플 보정 (Ctrl+R)")
        self.btn_ripple.setToolTip("파도에 의한 롤링/피칭 리플 효과를 마우스 영역 지정 후 해석 및 보정합니다.")
        self.btn_ripple.clicked.connect(self._on_open_motion_ripple)

        nav = QHBoxLayout()
        nav.setContentsMargins(4, 2, 4, 2)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.lbl_file, 1)
        nav.addWidget(self.btn_swap_ch)
        nav.addWidget(self.btn_bt_edit)
        nav.addWidget(self.btn_ripple)
        nav.addWidget(self.btn_next)

        # ---- 중앙 배치 ----
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)

        # 바탐 편집 전용 컨트롤 패널
        self.bt_panel = self._create_bottom_edit_panel()
        self.bt_panel.setVisible(False)
        rlay.addWidget(self.bt_panel)

        rlay.addWidget(self.wf, 1)
        nav_w = QWidget()
        nav_w.setLayout(nav)
        rlay.addWidget(nav_w)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.map)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([550, 950])
        self.setCentralWidget(splitter)

        # ---- 밝기/대비/투명도 슬라이더 (상태바) ----
        self.sl_bright = QSlider(Qt.Horizontal)
        self.sl_bright.setRange(-100, 100)
        self.sl_bright.setValue(0)
        self.sl_bright.setFixedWidth(120)
        self.sl_bright.valueChanged.connect(self._on_display_changed)

        self.sl_contrast = QSlider(Qt.Horizontal)
        self.sl_contrast.setRange(20, 300)          # /100 => 0.2 ~ 3.0
        self.sl_contrast.setValue(100)
        self.sl_contrast.setFixedWidth(120)
        self.sl_contrast.valueChanged.connect(self._on_display_changed)

        self.sl_mos = QSlider(Qt.Horizontal)
        self.sl_mos.setRange(0, 100)
        self.sl_mos.setValue(int(self.mosaic_opacity * 100))
        self.sl_mos.setFixedWidth(110)
        self.sl_mos.valueChanged.connect(self._on_mosaic_opacity)

        sb = self.statusBar()
        sb.addPermanentWidget(QLabel("밝기"))
        sb.addPermanentWidget(self.sl_bright)
        sb.addPermanentWidget(QLabel("대비"))
        sb.addPermanentWidget(self.sl_contrast)
        sb.addPermanentWidget(QLabel("모자이크"))
        sb.addPermanentWidget(self.sl_mos)
        self._update_nav()

    # ================================================ 파일 열기 (복수)
    def open_files(self, paths=None):
        # QAction.triggered 신호에서 bool(False)이 전달되거나 인자가 없는 경우 파일 선택 대화상자 활성화
        if paths is None or isinstance(paths, bool) or not paths:
            try:
                # 1차 시도: Qt 표준 내장 다이얼로그 (Chromium / QtWebEngine 환경에서 네이티브 COM 행/숨김 방지)
                paths, _ = QFileDialog.getOpenFileNames(
                    self, "소나 파일 선택 (XTF, Lowrance, Humminbird)", "",
                    "모든 지원 소나 파일 (*.xtf *.sl2 *.sl3 *.slg *.dat *.son);;XTF 소나 (*.xtf);;Lowrance 로그 (*.sl2 *.sl3 *.slg);;Humminbird 파일 (*.dat *.son);;모든 파일 (*)",
                    options=QFileDialog.Option.DontUseNativeDialog
                )
            except Exception:
                # 2차 시도: 기본 다이얼로그 폴백
                paths, _ = QFileDialog.getOpenFileNames(
                    self, "소나 파일 선택 (XTF, Lowrance, Humminbird)", "",
                    "모든 지원 소나 파일 (*.xtf *.sl2 *.sl3 *.slg *.dat *.son);;XTF 소나 (*.xtf);;Lowrance 로그 (*.sl2 *.sl3 *.slg);;Humminbird 파일 (*.dat *.son);;모든 파일 (*)"
                )
        if not paths or isinstance(paths, bool):
            return

        self.files = []
        self.cur_idx = -1
        self.map.clear_all()
        self.mosaic_item = None
        self.mosaic_kind = None

        errors = []
        xtf_paths = []

        # 1. 지원 포맷 분류: Lowrance / Humminbird 소나 파일 자동 변환
        non_xtf = []
        for path in sorted(paths):
            ext = os.path.splitext(path)[1].lower()
            if ext in (".sl2", ".sl3", ".slg", ".dat", ".son"):
                non_xtf.append(path)
            else:
                xtf_paths.append(path)

        if non_xtf:
            prog = QProgressDialog("소나 파일 포맷 변환 중 (XTF 생성)...", "취소", 0, len(non_xtf) * 100, self)
            prog.setWindowTitle("소나 파일 변환")
            prog.setWindowModality(Qt.WindowModal)
            prog.setMinimumDuration(0)
            prog.setValue(0)
            prog.show()
            QApplication.processEvents()

            for i, src in enumerate(non_xtf):
                if prog.wasCanceled():
                    break
                fname = os.path.basename(src)
                dst = f"{os.path.splitext(src)[0]}.xtf"

                prog.setLabelText(f"변환 중 ({i+1}/{len(non_xtf)}): {fname} ➔ {os.path.basename(dst)}")
                QApplication.processEvents()

                try:
                    def _conv_cb(pct, msg, base=i * 100):
                        prog.setValue(base + int(pct))
                        prog.setLabelText(f"{fname}: {msg}")
                        QApplication.processEvents()

                    out_path = convert_sonar_to_xtf(src, dst, progress_callback=_conv_cb)
                    xtf_paths.append(out_path)
                except Exception as e:
                    errors.append(f"{fname} 변환 오류: {e}")

            prog.close()

        # 2. XTF 파일 로드
        for path in sorted(xtf_paths):
            try:
                img, meta = load_xtf(path, agc=self.agc_on,
                                     agc_window=AGC_WINDOW)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                continue

            color = COLORS[len(self.files) % len(COLORS)]
            sync_bottom(path, meta)
            sync_nav(path, meta)
            self.files.append({
                "path": path, "img": img, "raw_img": img.copy(), "meta": meta,
                "color": color, "agc": self.agc_on,
                "swap_channels": False,
                "contacts": [],
            })

        if errors:
            QMessageBox.warning(
                self, "로드 실패",
                "다음 파일을 열 수 없습니다:\n" + "\n".join(errors))
        if not self.files:
            self._update_nav()
            return

        for idx, f in enumerate(self.files):
            track = self._track_points(f["meta"])
            self.map.add_track(idx, track, f["color"])
            poly = self._swath_polygon(f["meta"])
            if poly:
                self.map.add_coverage(idx, poly, f["color"])
        self.map.fit_all()

        self._show_file(0)

    # ================================================ 파일 전환
    def _switch_file(self, delta):
        if not self.files:
            return
        idx = (self.cur_idx + delta) % len(self.files)
        self._show_file(idx)

    def _show_file(self, idx):
        self.cur_idx = idx
        f = self.files[idx]

        if f.get("agc") != self.agc_on:
            if "raw_img" not in f:
                f["raw_img"] = f["img"].copy()
            try:
                if self.agc_on:
                    f["img"] = apply_agc(f["raw_img"], meta=f["meta"], window=AGC_WINDOW, strength=1.0)
                    f["agc"] = True
                else:
                    f["img"] = f["raw_img"].copy()
                    f["agc"] = False
            except Exception as e:
                QMessageBox.warning(self, "AGC 적용 실패", str(e))

        # 좌우 채널 반전 버튼/메뉴 상태 동기화
        swap_on = bool(f.get("swap_channels", False))
        if hasattr(self, 'act_swap_ch'):
            self.act_swap_ch.blockSignals(True)
            self.act_swap_ch.setChecked(swap_on)
            self.act_swap_ch.blockSignals(False)
        if hasattr(self, 'btn_swap_ch'):
            self.btn_swap_ch.blockSignals(True)
            self.btn_swap_ch.setChecked(swap_on)
            self.btn_swap_ch.blockSignals(False)

        self._apply_display()
        self.map.set_active_track(idx)
        self._update_nav()

    def _update_nav(self):
        n = len(self.files)
        has = n > 0
        self.btn_prev.setEnabled(n > 1)
        self.btn_next.setEnabled(n > 1)
        if has:
            name = os.path.basename(self.files[self.cur_idx]["path"])
            self.lbl_file.setText(f"📂 [{self.cur_idx + 1}/{n}]  {name}  (클릭 시 새 파일 열기)")
            self.setWindowTitle(f"XTF Processor V1.0 — {name}")
        else:
            self.lbl_file.setText("📂 소나 파일 열기 (현재: 파일 없음 - 클릭하여 파일 선택)")
            self.setWindowTitle("XTF Processor V1.0")

    # ================================================ 표시 (밝기/대비)
    def _on_display_changed(self):
        self.brightness = self.sl_bright.value()
        self.contrast = self.sl_contrast.value() / 100.0
        self._apply_display()

    def _apply_display(self):
        if not self.files:
            return
        f = self.files[self.cur_idx]
        img = f["img"]

        if self.brightness != 0 or self.contrast != 1.0:
            adj = img.astype(np.float32) * self.contrast + self.brightness
            img = np.clip(adj, 0, 255).astype(np.uint8)

        self.wf.set_image(img, f["meta"])

        self.wf.clear_contact_markers()
        for row, col, label in f.get("contacts", []):
            self.wf.add_contact_marker(row, col, label)



    # ================================================ 생선 가시 항법/헤딩 튜너
    def _on_open_fishbone_tuner(self):
        """생선 가시(Fishbone) 대화상자를 열어 항적 및 헤딩 보간을 시각적으로 확인하고 메모리에 즉시 적용."""
        if not self.files:
            QMessageBox.information(
                self, "항법 및 헤딩 튜너",
                "열려있는 XTF 파일이 없습니다.\n먼저 XTF 파일을 열어주세요.")
            return

        dlg = NavHeadingTunerDialog(self, files=self.files, cur_idx=self.cur_idx)
        if dlg.exec() == 1:
            # 메모리(f["meta"])가 대화상자에서 이미 갱신되었으므로 지도 및 화면 즉시 갱신
            f = self.files[self.cur_idx]
            track = self._track_points(f["meta"])
            self.map.add_track(self.cur_idx, track, f["color"])
            poly = self._swath_polygon(f["meta"])
            if poly:
                self.map.add_coverage(self.cur_idx, poly, f["color"])
            self.map.set_active_track(self.cur_idx)
            self._apply_display()

            self.statusBar().showMessage(
                f"항법 및 헤딩 보정 완료: {os.path.basename(f['path'])} 메모리 및 지도 갱신됨", 6000
            )

    # ================================================ 항법 보정 및 핑 보간
    def _on_process_navigation(self):
        """선택한 XTF 파일(들)의 항법 스파이크 제거, 스무딩 및 핑별 위치/헤딩 보간 수행."""
        if not self.files:
            QMessageBox.information(
                self, "항법 보정",
                "열려있는 XTF 파일이 없습니다.\n먼저 XTF 파일을 열어주세요.")
            return

        dlg = NavProcessDialog(self, files=self.files, cur_idx=self.cur_idx)
        if dlg.exec() != 1:
            return

        p = dlg.get_params()
        targets = self.files if p.get("apply_all") else [self.files[self.cur_idx]]

        total_spikes = 0
        processed_files = []

        self.statusBar().showMessage("항법 보정 및 매 핑별 위치 보간 중... 잠시 기다려 주세요.")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for f in targets:
                path = f["path"]
                meta = f["meta"]
                results, stats = process_navigation(
                    meta,
                    spike_filter=p.get("spike_filter", True),
                    max_speed_mps=p.get("max_speed_mps", 12.0),
                    smooth_window_sec=p.get("smooth_window_sec", 3.0),
                    interp_method=p.get("interp_method", "hermite"),
                    use_cog_heading=p.get("use_cog_heading", False),
                )
                apply_interpolated_nav_to_meta(meta, results)

                if p.get("save_sidecar", True):
                    save_nav(path, meta)

                total_spikes += stats.get("spikes_detected", 0)
                processed_files.append((f, stats))

            # 지도 및 화면 갱신
            for idx, f in enumerate(self.files):
                if f in targets:
                    track = self._track_points(f["meta"])
                    self.map.add_track(idx, track, f["color"])
                    poly = self._swath_polygon(f["meta"])
                    if poly:
                        self.map.add_coverage(idx, poly, f["color"])

            if 0 <= self.cur_idx < len(self.files):
                self.map.set_active_track(self.cur_idx)
                self._apply_display()

        except Exception as e:
            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "항법 보정 실패", f"오류 발생: {e}")
            self.statusBar().showMessage("항법 보정 실패", 5000)
            return
        finally:
            QApplication.restoreOverrideCursor()

        # 결과 요약 안내 메시지
        cur_stats = processed_files[0][1] if processed_files else {}
        summary_msg = (
            f"항법 보정 및 핑 보간이 성공적으로 완료되었습니다.\n\n"
            f"• 대상 파일: {len(processed_files)}개\n"
            f"• 핑 업데이트 레이트(추정): {cur_stats.get('avg_ping_rate', 0.0):.1f} Hz\n"
            f"• GPS 갱신 빈도: {cur_stats.get('gps_update_rate', 0.0):.2f} Hz "
            f"(1회당 약 {cur_stats.get('pings_per_update', 0.0):.1f}핑 보간)\n"
            f"• 검출된 위치 스파이크/결측: {total_spikes}건 복원\n"
            f"• 평균 선속: {cur_stats.get('avg_speed_mps', 0.0):.2f} m/s "
            f"({cur_stats.get('avg_speed_knots', 0.0):.1f} kts)\n\n"
            f"메모리의 항법 정보가 갱신되어 지도 항적 및 커버리지가 업데이트되었습니다."
        )
        if p.get("save_sidecar", True):
            summary_msg += "\n(사이드카 nav/*.nav.csv 파일 저장 완료 - 모자이크 생성 시 자동 반영)"

        self.statusBar().showMessage(
            f"항법 보정 완료: {cur_stats.get('avg_ping_rate', 0.0):.1f} Hz 매 핑 보간, 스파이크 {total_spikes}건 복원",
            7000,
        )
        QMessageBox.information(self, "항법 보정 완료", summary_msg)


    # ================================================ 토우피쉬 모션 리플 보정
    def _on_open_motion_ripple(self):
        """토우피쉬 롤링/피칭 리플 해석 및 보정 대화상자를 열고 ROI 선택 모드 활성화."""
        if not self.files or self.cur_idx < 0:
            QMessageBox.information(
                self, "모션 리플 보정",
                "열려있는 XTF 파일이 없습니다.\n먼저 XTF 파일을 열어주세요."
            )
            return

        f = self.files[self.cur_idx]
        self.wf.set_roi_mode(True)

        if self.ripple_dialog is None:
            self.ripple_dialog = TowfishMotionRippleDialog(
                self, img=f["img"], meta=f["meta"], filename=f["path"]
            )
            self.ripple_dialog.correctionApplied.connect(self._on_ripple_correction_applied)
            self.ripple_dialog.livePreviewRequested.connect(self._on_ripple_live_preview)
            self.ripple_dialog.finished.connect(self._on_ripple_dialog_closed)
        else:
            self.ripple_dialog.img = f["img"]
            self.ripple_dialog.meta = f["meta"]
            self.ripple_dialog.filename = f["path"]
            self.ripple_dialog.run_analysis()

        self.ripple_dialog.show()
        self.ripple_dialog.raise_()
        self.ripple_dialog.activateWindow()
        self.statusBar().showMessage(
            "💡 Waterfall 화면에서 파도 리플 패턴이 나타난 구간을 마우스 좌클릭으로 드래그하여 지정하세요.", 8000
        )

    # ================================================ 소나 포맷 변환기
    def _on_open_converter_dialog(self):
        """Humminbird / Lowrance 소나 파일 변환 대화상자 표시."""
        dlg = SonarConverterDialog(self)
        dlg.converted_files_ready.connect(self.open_files)
        dlg.exec()

    def _on_roi_selected(self, r0: int, r1: int, c0: int, c1: int):
        """Waterfall에서 사용자가 마우스로 드래그한 ROI 영역 수신."""
        if not self.files or self.cur_idx < 0:
            return
        f = self.files[self.cur_idx]
        if self.ripple_dialog is None or not self.ripple_dialog.isVisible():
            self._on_open_motion_ripple()
        if self.ripple_dialog is not None:
            self.ripple_dialog.set_roi(r0, r1, c0, c1)
            self.ripple_dialog.raise_()

    def _on_ripple_live_preview(self, preview_img: np.ndarray, apply_full: bool):
        """리플 대화상자에서 슬라이더/파라미터 조절 시 메인 Waterfall 화면 실시간 동기화."""
        if not self.files or self.cur_idx < 0:
            return
        f = self.files[self.cur_idx]
        img = preview_img
        if self.brightness != 0 or self.contrast != 1.0:
            adj = img.astype(np.float32) * self.contrast + self.brightness
            img = np.clip(adj, 0, 255).astype(np.uint8)
        self.wf.set_image(img, f["meta"])

    def _on_ripple_correction_applied(self, new_img: np.ndarray, apply_full: bool):
        """대화상자에서 보정 확정 시 메모리 및 화면 갱신."""
        if not self.files or self.cur_idx < 0:
            return
        f = self.files[self.cur_idx]
        f["img"] = new_img
        f["raw_img"] = new_img.copy()
        self._apply_display()
        scope_str = "전체 핑 일괄 적용" if apply_full else "선택된 ROI 구간 적용"
        self.statusBar().showMessage(f"토우피쉬 모션 리플 보정 적용 완료 ({scope_str})", 6000)
        self.wf.clear_roi()
        self.wf.set_roi_mode(False)

    def _on_ripple_dialog_closed(self, result: int):
        """리플 대화상자가 닫힐 때(적용하지 않고 닫은 경우 포함) 원본 복원 및 정리."""
        self._apply_display()
        self.wf.clear_roi()
        self.wf.set_roi_mode(False)


    # ================================================ 좌우 채널 반전 (Port ↔ Starboard)
    def _on_swap_channels_toggled(self, checked):
        """현재 파일의 Port와 Starboard 채널 순서를 상호 반대로 교환하여 재로드 및 화면 갱신."""
        if self.cur_idx < 0 or not self.files:
            return
        f = self.files[self.cur_idx]
        if f.get("swap_channels") == checked:
            return
        f["swap_channels"] = checked

        # UI 위젯 동기화
        if hasattr(self, 'act_swap_ch') and self.act_swap_ch.isChecked() != checked:
            self.act_swap_ch.blockSignals(True)
            self.act_swap_ch.setChecked(checked)
            self.act_swap_ch.blockSignals(False)
        if hasattr(self, 'btn_swap_ch') and self.btn_swap_ch.isChecked() != checked:
            self.btn_swap_ch.blockSignals(True)
            self.btn_swap_ch.setChecked(checked)
            self.btn_swap_ch.blockSignals(False)

        try:
            img, meta = load_xtf(f["path"], flip_port=False, swap_channels=checked,
                                 agc=self.agc_on, agc_window=AGC_WINDOW)
            sync_bottom(f["path"], meta)
            sync_nav(f["path"], meta)
            f["img"], f["meta"] = img, meta
        except Exception as e:
            QMessageBox.warning(self, "채널 반전 로드 실패", str(e))
            return

        self._apply_display()

        # 지도 커버리지 폴리곤 즉시 갱신
        poly = self._swath_polygon(f["meta"])
        if poly:
            self.map.add_coverage(self.cur_idx, poly, f["color"])

        status_text = "좌우 채널 반전 적용됨 (Port ↔ Starboard 교환)" if checked else "좌우 채널 기본(정상) 적용됨"
        self.statusBar().showMessage(status_text, 5000)

    # ================================================ AGC
    def _on_agc_toggled(self, checked):
        self.agc_on = checked
        if not self.files or self.cur_idx < 0:
            return
        f = self.files[self.cur_idx]
        if "raw_img" not in f:
            f["raw_img"] = f["img"].copy()
        try:
            if self.agc_on:
                f["img"] = apply_agc(f["raw_img"], meta=f["meta"], window=AGC_WINDOW, strength=1.0)
                f["agc"] = True
            else:
                f["img"] = f["raw_img"].copy()
                f["agc"] = False
        except Exception as e:
            QMessageBox.warning(self, "AGC 적용 실패", str(e))
            return
        self._apply_display()
        self.statusBar().showMessage(
            f"방사보정 AGC(Angle Varied Gain) {'적용' if self.agc_on else '해제'}: {os.path.basename(f['path'])}", 4000
        )

    # ================================================ hover → 지도 마커
    def _on_hover(self, ping_idx, sample_idx):
        if self.cur_idx < 0:
            return
        meta = self.files[self.cur_idx]["meta"]
        if not (0 <= ping_idx < len(meta)):
            return
        m = meta[ping_idx]
        ping_no = m.get("ping_no", m.get("ping", ping_idx))
        self.map.highlight_ping(ping_idx)
        self.statusBar().showMessage(
            f"ping {ping_no}  time {m['time']}  "
            f"lat {m['y']:.6f}  lon {m['x']:.6f}  "
            f"alt {m['altitude']:.1f} m  range {m['slant_range']:.1f} m")

    # ================================================ contact
    def _on_contact(self, ping_idx, sample_idx):
        if self.cur_idx < 0:
            return
        f = self.files[self.cur_idx]
        self.contact_no += 1
        try:
            info = save_contact(f["img"], f["meta"],
                                ping_idx, sample_idx,
                                self.contact_no, f["path"])
            lat = float(info["lat"])
            lon = float(info["lon"])
        except Exception as e:
            QMessageBox.warning(self, "Contact 저장 실패", str(e))
            self.contact_no -= 1
            return

        label = f"C{self.contact_no:03d}"
        self.map.add_contact(lon, lat, label)
        f.setdefault("contacts", []).append((ping_idx, sample_idx, label))
        self.wf.add_contact_marker(ping_idx, sample_idx, label)
        self.statusBar().showMessage(
            f"Contact {label} 저장  lat {lat:.6f}  lon {lon:.6f}")

    # ================================================ meta → 지도 좌표
    @staticmethod
    def _track_points(meta):
        """핑 인덱스와 1:1 유지되는 (lat, lon) 리스트."""
        pts = []
        last = (0.0, 0.0)
        for m in meta:
            la, lo = m["y"], m["x"]
            if la == 0.0 and lo == 0.0:
                la, lo = last
            else:
                last = (la, lo)
            pts.append((la, lo))
        return pts

    @staticmethod
    def _swath_polygon(meta, step=SWATH_STEP):
        """slant_range/heading 기반 좌우 외곽선 폴리곤 [(lat, lon), ...]
        Port와 Starboard의 range를 각각 반영하여 채널 반전 및 비대칭 폭을 정확히 투영."""
        left, right = [], []
        for m in meta[::step]:
            la, lo = m["y"], m["x"]
            if la == 0.0 and lo == 0.0:
                continue
            rng_p = float(m.get("range_port") or m.get("slant_range") or 0.0)
            rng_s = float(m.get("range_stbd") or m.get("slant_range") or 0.0)
            if rng_p <= 0.0 and rng_s <= 0.0:
                continue
            rng_p = rng_p if rng_p > 0.0 else rng_s
            rng_s = rng_s if rng_s > 0.0 else rng_p

            hdg = math.radians(m.get("heading", 0.0) or 0.0)
            m_per_deg_lat = 111320.0
            m_per_deg_lon = 111320.0 * max(math.cos(math.radians(la)), 1e-6)

            # 우현 (Starboard, +90도): cos(hdg), -sin(hdg)
            de_s = math.cos(hdg) * rng_s
            dn_s = -math.sin(hdg) * rng_s
            right.append((la + dn_s / m_per_deg_lat,
                          lo + de_s / m_per_deg_lon))

            # 좌현 (Port, -90도): -cos(hdg), sin(hdg)
            de_p = math.cos(hdg) * rng_p
            dn_p = -math.sin(hdg) * rng_p
            left.append((la - dn_p / m_per_deg_lat,
                         lo - de_p / m_per_deg_lon))
        if len(left) < 2:
            return []
        return left + right[::-1]

    # ================================================ 바탐 편집 패널 및 로직
    def _create_bottom_edit_panel(self):
        panel = QFrame()
        panel.setObjectName("BottomEditPanel")
        panel.setStyleSheet("""
            QFrame#BottomEditPanel {
                background-color: #1e1f22;
                border-bottom: 2px solid #3c4043;
                padding: 4px;
            }
            QLabel {
                color: #e8eaed;
                font-size: 11px;
            }
            QPushButton {
                background-color: #2b2d30;
                color: #e8eaed;
                border: 1px solid #4e5157;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #35383d;
            }
            QPushButton:checked {
                background-color: #1a73e8;
                color: #ffffff;
                border-color: #1a73e8;
                font-weight: bold;
            }
            QDoubleSpinBox {
                background-color: #2b2d30;
                color: #e8eaed;
                border: 1px solid #4e5157;
                border-radius: 4px;
                padding: 2px 4px;
                font-size: 11px;
            }
        """)

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(4)

        # 1행: 제어 컨트롤 버튼들
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        lbl_title = QLabel("<b>[ 해저면 추적 / 바탐 편집 ]</b>")
        row1.addWidget(lbl_title)

        # 모드 선택 그룹
        self.btn_bt_auto = QPushButton("🎯 시드 자동 추적")
        self.btn_bt_auto.setCheckable(True)
        self.btn_bt_auto.setChecked(True)
        self.btn_bt_auto.setToolTip("클릭한 지점을 기점으로 전 핑의 First Bottom을 자동 추적합니다.")

        self.btn_bt_manual = QPushButton("✏️ 구간 수동 보정")
        self.btn_bt_manual.setCheckable(True)
        self.btn_bt_manual.setToolTip("불량 구간의 시작과 끝점을 클릭하여 선형 보정합니다.")

        self.bt_mode_group = QButtonGroup(self)
        self.bt_mode_group.addButton(self.btn_bt_auto, 0)
        self.bt_mode_group.addButton(self.btn_bt_manual, 1)
        self.btn_bt_auto.clicked.connect(lambda: self.wf.set_edit_mode("auto"))
        self.btn_bt_manual.clicked.connect(lambda: self.wf.set_edit_mode("manual"))

        row1.addWidget(self.btn_bt_auto)
        row1.addWidget(self.btn_bt_manual)

        # 버퍼 스핀박스
        lbl_buf = QLabel("버퍼:")
        self.sp_bt_buffer = QDoubleSpinBox()
        self.sp_bt_buffer.setRange(0.5, 30.0)
        self.sp_bt_buffer.setSingleStep(0.5)
        self.sp_bt_buffer.setValue(3.5)
        self.sp_bt_buffer.setSuffix(" m")
        self.sp_bt_buffer.setToolTip("이전 핑 고도 대비 탐색 허용 범위 (±m)")
        self.sp_bt_buffer.valueChanged.connect(self.wf.set_buffer_m)

        row1.addWidget(lbl_buf)
        row1.addWidget(self.sp_bt_buffer)

        # 평활화 및 초기화
        btn_smooth = QPushButton("🌊 부드럽게 (Smooth)")
        btn_smooth.setToolTip("중앙값 필터를 적용하여 미세 지터와 노이즈를 완화합니다.")
        btn_smooth.clicked.connect(lambda: self.wf.smooth_current_bottom(5))
        row1.addWidget(btn_smooth)

        btn_reset = QPushButton("↺ 초기화")
        btn_reset.setToolTip("현재 편집/추적 내역을 초기화합니다.")
        btn_reset.clicked.connect(self.wf.reset_bottom)
        row1.addWidget(btn_reset)

        row1.addStretch(1)

        # 완료 및 취소
        btn_save = QPushButton("💾 완료 및 저장 (Enter)")
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #1b5e20;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
        """)
        btn_save.setToolTip("추적된 바탐 고도를 사이드카(.bottom.csv)에 저장하고 모자이크에 반영합니다.")
        btn_save.clicked.connect(self.wf.finish_bottom_edit)

        btn_cancel = QPushButton("✕ 취소 (Esc)")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #424242;
                color: #ffffff;
                border: 1px solid #616161;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        btn_cancel.clicked.connect(self.wf.cancel_bottom_edit)

        row1.addWidget(btn_save)
        row1.addWidget(btn_cancel)
        vbox.addLayout(row1)

        # 2행: 도움말 및 진행 상태
        self.lbl_bt_hint = QLabel("💡 영상에서 해저면 경계(First Bottom) 지점을 마우스 좌클릭하세요. 전 핑이 자동 추적됩니다.")
        self.lbl_bt_hint.setStyleSheet("color: #ffe082; font-size: 11px; padding-left: 2px;")
        vbox.addWidget(self.lbl_bt_hint)

        return panel

    def _on_bottom_progress(self, msg: str):
        if hasattr(self, "lbl_bt_hint"):
            self.lbl_bt_hint.setText(f"💡 {msg}")
        self.statusBar().showMessage(msg, 5000)

    def _toggle_bt_edit(self, on):
        if on:
            if not self.files or self.cur_idx < 0:
                QMessageBox.information(self, "안내", "먼저 XTF 파일을 열어주세요.")
                self.btn_bt_edit.blockSignals(True)
                self.btn_bt_edit.setChecked(False)
                self.btn_bt_edit.blockSignals(False)
                if hasattr(self, "act_bt_edit"):
                    self.act_bt_edit.blockSignals(True)
                    self.act_bt_edit.setChecked(False)
                    self.act_bt_edit.blockSignals(False)
                return

            if not self.wf.start_bottom_edit():
                self.btn_bt_edit.blockSignals(True)
                self.btn_bt_edit.setChecked(False)
                self.btn_bt_edit.blockSignals(False)
                if hasattr(self, "act_bt_edit"):
                    self.act_bt_edit.blockSignals(True)
                    self.act_bt_edit.setChecked(False)
                    self.act_bt_edit.blockSignals(False)
                return

            self.btn_bt_edit.setText("바탐 편집 완료 (Enter)")
            if hasattr(self, "act_bt_edit"):
                self.act_bt_edit.blockSignals(True)
                self.act_bt_edit.setChecked(True)
                self.act_bt_edit.blockSignals(False)
            self.bt_panel.setVisible(True)
            self.btn_bt_auto.setChecked(True)
        else:
            self.wf.finish_bottom_edit()
            self.btn_bt_edit.setText("바탐 편집 시작")
            if hasattr(self, "act_bt_edit"):
                self.act_bt_edit.blockSignals(True)
                self.act_bt_edit.setChecked(False)
                self.act_bt_edit.blockSignals(False)
            self.bt_panel.setVisible(False)

    def _on_bottom_edited(self, new_alt):
        self.bt_panel.setVisible(False)
        self.btn_bt_edit.blockSignals(True)
        self.btn_bt_edit.setChecked(False)
        self.btn_bt_edit.setText("바탐 편집 시작")
        self.btn_bt_edit.blockSignals(False)
        if hasattr(self, "act_bt_edit"):
            self.act_bt_edit.blockSignals(True)
            self.act_bt_edit.setChecked(False)
            self.act_bt_edit.blockSignals(False)

        if new_alt is None:
            self.statusBar().showMessage("바탐 편집이 취소되었습니다.", 3000)
            return

        if not self.files or self.cur_idx < 0:
            return

        f = self.files[self.cur_idx]
        for row, m in enumerate(f["meta"]):
            if row < len(new_alt):
                m["altitude"] = float(new_alt[row])
        path = save_bottom(f["path"], f["meta"])
        if f.get("agc"):
            if "raw_img" not in f:
                f["raw_img"] = f["img"].copy()
            f["img"] = apply_agc(f["raw_img"], meta=f["meta"], window=AGC_WINDOW, strength=1.0)
            self._apply_display()
        self.statusBar().showMessage(f"해저면(바탐) 고도 사이드카 저장 완료: {path}", 5000)

    # ================================================ 모자이크 파라미터
    def _xtf_paths(self):
        """현재 열린 XTF 경로. 없으면 파일 대화상자로 요청."""
        paths = [f["path"] for f in self.files]
        if paths:
            return paths
        sel, _ = QFileDialog.getOpenFileNames(
            self, "모자이크에 사용할 소나 파일 선택", "",
            "모든 지원 소나 파일 (*.xtf *.sl2 *.sl3 *.slg *.dat *.son);;XTF 소나 (*.xtf);;Lowrance 로그 (*.sl2 *.sl3 *.slg);;Humminbird 파일 (*.dat *.son);;모든 파일 (*)")
        if not sel:
            return []
        final_list = []
        for s in sel:
            ext = os.path.splitext(s)[1].lower()
            if ext in (".sl2", ".sl3", ".slg", ".dat", ".son"):
                dst = f"{os.path.splitext(s)[0]}.xtf"
                try:
                    converted = convert_sonar_to_xtf(s, dst)
                    final_list.append(converted)
                except Exception as e:
                    print(f"변환 실패 ({s}): {e}")
            else:
                final_list.append(s)
        return sorted(final_list)

    def _default_params(self, paths):
        name = "mosaic"
        if paths:
            name = os.path.splitext(os.path.basename(paths[0]))[0]
        cur_swap = False
        if self.files and 0 <= self.cur_idx < len(self.files):
            cur_swap = bool(self.files[self.cur_idx].get("swap_channels", False))
        return {
            "epsg": 32652, "res": 0.2,
            "outdir": os.path.join(os.getcwd(), "exported_mosaics"),
            "name": name, "lo": 2.0, "hi": 98.0,
            "use_port": True, "use_stbd": True,
            "swap_channels": cur_swap,
            "stride": 1, "ping_stride": 1,
            "flip_port": True, "slant_correct": True,
            "remove_water_column": True, "avc": True,
            "use_sidecar": True, "auto_track": True,
            "alpha": True, "alpha_mode": "gray",
        }

    def _normalize_params(self, p, paths):
        """키 별칭을 모두 채우고, 비어 있는 파일 목록을 보정한다."""
        p = dict(p or {})

        # --- 파일 목록 ---
        found = []
        for k in FILE_KEYS:
            found = _as_path_list(p.get(k))
            if found:
                break
        if not found:
            found = list(paths)
        found = [q for q in found if isinstance(q, str) and q.strip()]
        if not found:
            raise ValueError("XTF 파일이 지정되지 않았습니다. "
                             "먼저 파일을 열거나 선택해 주세요.")
        for k in FILE_KEYS:
            p[k] = list(found)

        d = self._default_params(found)

        def _fill(keys, default):
            val = None
            for k in keys:
                v = p.get(k)
                if v not in (None, "", []):
                    val = v
                    break
            if val is None:
                val = default
            for k in keys:
                p[k] = val
            return val

        _fill(OUTDIR_KEYS, d["outdir"])
        _fill(NAME_KEYS, d["name"])
        _fill(RES_KEYS, d["res"])
        _fill(EPSG_KEYS, d["epsg"])

        for k, v in d.items():
            p.setdefault(k, v)
        return p

    def _mosaic_params(self):
        """mosaic_dialog 가 있으면 사용, 없으면 기본값 dict 반환."""
        paths = self._xtf_paths()
        if not paths:
            return None
        default = self._normalize_params({}, paths)

        try:
            import mosaic_dialog as MD
        except Exception:
            return default

        cls = None
        for nm in ("MosaicDialog", "MosaicParamsDialog", "ParamsDialog",
                   "Dialog"):
            cls = getattr(MD, nm, None)
            if cls is not None:
                break

        if cls is None:
            fn = getattr(MD, "get_params", None) or getattr(MD, "ask", None)
            if callable(fn):
                try:
                    p = fn(self, paths)
                except TypeError:
                    try:
                        p = fn(paths)
                    except TypeError:
                        p = fn()
                if not p:
                    return None
                return self._normalize_params(p, paths)
            return default

        try:
            dlg = None
            for args in ((self, paths), (self,), (paths,), ()):
                try:
                    dlg = cls(*args)
                    break
                except TypeError:
                    continue
            if dlg is None:
                return default

            for nm in ("set_files", "setFiles", "set_inputs", "add_files"):
                fn = getattr(dlg, nm, None)
                if callable(fn):
                    try:
                        fn(paths)
                        break
                    except Exception:
                        pass

            if dlg.exec() != 1:
                return None

            got = None
            for nm in ("params", "values", "result_params", "get_params",
                       "_collect", "collect"):
                fn = getattr(dlg, nm, None)
                if callable(fn):
                    try:
                        r = fn()
                    except Exception:
                        continue
                    if isinstance(r, dict):
                        got = r
                        break
                elif isinstance(fn, dict):
                    got = fn
                    break
            return self._normalize_params(got or {}, paths)
        except Exception:
            traceback.print_exc()
            return default

    # ================================================ 모자이크 생성
    def build_mosaic(self):
        try:
            p = self._mosaic_params()
        except ValueError as e:
            QMessageBox.warning(self, "모자이크", str(e))
            return
        if p is None:
            return

        print("[mosaic] params:")
        for k in ("files", "epsg", "res", "outdir", "name"):
            print(f"   {k} = {p.get(k)}")

        self.statusBar().showMessage("모자이크 생성 중... 잠시 기다려 주세요.")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out = self._run_builder(p)
        except Exception as e:
            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "모자이크 실패",
                                 f"{type(e).__name__}: {e}")
            self.statusBar().showMessage("모자이크 실패", 5000)
            return
        QApplication.restoreOverrideCursor()

        if not out or not os.path.isfile(out):
            QMessageBox.warning(self, "모자이크",
                                "결과 파일을 찾을 수 없습니다.\n"
                                f"반환값: {out!r}")
            return

        if p.get("alpha", True):
            out = self._ensure_alpha(out, p.get("alpha_mode", "gray"))

        self.mosaic_path = out
        self.statusBar().showMessage(f"모자이크 완료: {out}", 6000)
        self.show_mosaic(out)

    @staticmethod
    def _extract_path(r):
        if isinstance(r, str):
            return r
        if isinstance(r, dict):
            for k in ("path", "out", "output", "tif", "tiff", "file"):
                v = r.get(k)
                if isinstance(v, str):
                    return v
            return None
        if isinstance(r, (list, tuple)):
            for v in r:
                s = MainWindow._extract_path(v)
                if s and s.lower().endswith((".tif", ".tiff")):
                    return s
            for v in r:
                if isinstance(v, str):
                    return v
        return None

    @staticmethod
    def _invoke(fn, p):
        """함수 시그니처에 맞춰 호출."""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return fn(p)
        params = sig.parameters
        names = [n for n in params
                 if params[n].kind in (inspect.Parameter.POSITIONAL_ONLY,
                                       inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        has_var_kw = any(x.kind == inspect.Parameter.VAR_KEYWORD
                         for x in params.values())

        # 단일 인자 → dict 통째로
        if len(params) == 1 and names and not has_var_kw:
            return fn(p)
        if has_var_kw:
            return fn(**p)

        kw = {k: v for k, v in p.items() if k in params}
        missing = [n for n in names
                   if params[n].default is inspect.Parameter.empty
                   and n not in kw]
        if missing:
            raise TypeError(f"필수 인자 누락: {missing}")
        return fn(**kw)

    def _run_builder(self, p):
        """mosaic_builder → mosaic_min 순으로 실행 가능한 함수를 찾는다."""
        tried = []
        for mod_name in ("mosaic_builder", "mosaic_min"):
            try:
                mod = __import__(mod_name)
            except Exception as e:
                tried.append(f"{mod_name}: import 실패 ({e})")
                continue
            for fn_name in ("build_mosaic_from_params", "build_from_params",
                            "build_mosaic", "build", "run", "make_mosaic",
                            "main"):
                fn = getattr(mod, fn_name, None)
                if not callable(fn):
                    continue
                try:
                    r = self._invoke(fn, p)
                except TypeError as e:
                    tried.append(f"{mod_name}.{fn_name}: {e}")
                    continue
                out = self._extract_path(r)
                print(f"[mosaic] {mod_name}.{fn_name}() → {out!r}")
                if out:
                    return out
                tried.append(f"{mod_name}.{fn_name}: 경로 반환 없음")
        raise RuntimeError(
            "실행 가능한 모자이크 빌드 함수를 찾지 못했습니다.\n" +
            "\n".join(tried[:8]))

    def _ensure_alpha(self, path, mode="gray"):
        """알파 밴드가 없으면 mosaic_alpha 로 추가한 새 파일 경로 반환."""
        try:
            import rasterio
            with rasterio.open(path) as src:
                if src.count in (2, 4):
                    return path
        except Exception:
            return path
        try:
            from mosaic_alpha import add_alpha_to_file
            return add_alpha_to_file(path, mode=mode)
        except Exception as e:
            print("[알파 추가 실패]", e)
            return path

    # ================================================ 모자이크 열기 / 알파
    def load_mosaic_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "모자이크 GeoTIFF 선택", "",
            "GeoTIFF (*.tif *.tiff);;All Files (*)")
        if not path:
            return
        self.mosaic_path = path
        self.show_mosaic(path)

    def add_alpha_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "알파를 추가할 GeoTIFF 선택", "",
            "GeoTIFF (*.tif *.tiff);;All Files (*)")
        if not path:
            return
        try:
            from mosaic_alpha import add_alpha_to_file
            out = add_alpha_to_file(path, mode="gray")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "알파 추가 실패", str(e))
            return
        self.mosaic_path = out
        QMessageBox.information(self, "알파 추가 완료", out)
        self.show_mosaic(out)

    # ================================================ 지도 오버레이
    def _resolve_map_widget(self):
       """오버레이 메서드를 실제로 가진 MapView 위젯을 찾아 반환."""
       names = ("map_view", "map", "mapview", "mapWidget", "web_map", "webmap")
       cands = []
       for nm in names:
           w = getattr(self, nm, None)
           if w is not None:
               cands.append((nm, w))

       # 1순위: add_image_overlay 를 가진 위젯
       for nm, w in cands:
           if callable(getattr(w, "add_image_overlay", None)):
               print("[오버레이] map widget =", nm, type(w).__name__)
               return w
       # 2순위: 다른 오버레이 계열 메서드를 가진 위젯
       for nm, w in cands:
           for a in ("add_mosaic_overlay", "add_raster_overlay", "set_mosaic"):
               if callable(getattr(w, a, None)):
                   print("[오버레이] map widget =", nm, type(w).__name__)
                   return w
       # 3순위: 아무거나
       if cands:
           print("[오버레이] map widget =", cands[0][0], type(cands[0][1]).__name__)
           return cands[0][1]
       return None

    def _resolve_map_widget(self):
        """오버레이 메서드를 실제로 가진 MapView 위젯을 찾아 반환."""
        names = ("map_view", "map", "mapview", "mapWidget", "web_map", "webmap")
        seen, cands = set(), []
        for nm in names:
            w = getattr(self, nm, None)
            if w is not None and id(w) not in seen:
                seen.add(id(w))
                cands.append((nm, w))

        for nm, w in cands:
            if callable(getattr(w, "add_image_overlay", None)):
                print("[오버레이] map widget =", nm, type(w).__name__)
                return w
        for nm, w in cands:
            for a in ("add_mosaic_overlay", "add_raster_overlay", "set_mosaic"):
                if callable(getattr(w, a, None)):
                    print("[오버레이] map widget =", nm, type(w).__name__)
                    return w
        if cands:
            print("[오버레이] map widget =", cands[0][0], type(cands[0][1]).__name__)
            return cands[0][1]
        return None

    def _try_overlay_call(self, fn, name, arg_sets):
        """인자 조합을 순차 시도. 성공하면 True.

        inspect.signature().bind() 로 사전 검증하므로, 호출 중 발생한
        TypeError 는 시그니처 문제가 아니라 함수 내부 버그로 판정한다.
        """
        try:
            sig = inspect.signature(fn)
            print("[오버레이] %s%s" % (name, sig))
        except (TypeError, ValueError):
            sig = None

        for args in arg_sets:
            desc = ", ".join(
                (("ndarray%s" % (a.shape,)) if hasattr(a, "shape") else
                 repr(a)[:48]) for a in args)
            if sig is not None:
                try:
                    sig.bind(*args)
                except TypeError as exc:
                    print("[오버레이]   시그니처 불일치 (%s) : %s" % (desc, exc))
                    continue
            try:
                fn(*args)
            except Exception:
                print("[오버레이]   호출 중 예외 (%s) ↓" % desc)
                traceback.print_exc()
                continue
            print("[오버레이] %s 호출 성공 : %s" % (name, desc))
            return True
        return False

    def show_mosaic(self, path):
        mv = self._resolve_map_widget()
        if mv is None:
            QMessageBox.warning(self, "모자이크 표시",
                                "MapView 위젯을 찾지 못했습니다.\n\n" + str(path))
            return

        try:
            rgba, bounds = read_mosaic_rgba(path)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "모자이크 표시 실패", str(e))
            return

        self.remove_mosaic(silent=True)

        try:
            lo0, la0, lo1, la1 = bounds_utils.normalize_bounds(bounds, latlon=True)
        except Exception as exc:
            print("[오버레이] bounds 정규화 실패:", repr(bounds), exc)
            QMessageBox.critical(self, "모자이크 표시 실패",
                                 "bounds 정규화 실패: %r" % (bounds,))
            return

        leaflet = [[la0, lo0], [la1, lo1]]          # [[s, w], [n, e]]
        flat = (lo0, la0, lo1, la1)
        print("[오버레이] lon %.8f~%.8f  lat %.8f~%.8f" % (lo0, lo1, la0, la1))
        try:
            print("[오버레이] rgba", rgba.shape, rgba.dtype,
                  "alpha max =", int(rgba[..., 3].max())
                  if rgba.ndim == 3 and rgba.shape[2] == 4 else "-")
        except Exception:
            pass

        # ---- 1) MapView 전용 오버레이 메서드 --------------------------
        arg_sets = ((rgba, leaflet), (rgba, flat), (rgba, bounds),
                    (str(path), leaflet), (str(path),), (rgba,))
        for nm in ("add_image_overlay", "add_mosaic_overlay",
                   "add_raster_overlay", "set_mosaic"):
            fn = getattr(mv, nm, None)
            if not callable(fn):
                continue
            if not self._try_overlay_call(fn, nm, arg_sets):
                continue
            self.mosaic_kind = "custom"
            self.mosaic_item = None
            self._apply_mosaic_opacity()
            for fb in ("fit_bounds", "zoom_to_bounds", "set_bounds"):
                g = getattr(mv, fb, None)
                if callable(g):
                    try:
                        g(leaflet)
                    except Exception:
                        pass
                    break
            self.act_mos_show.setChecked(True)
            self.statusBar().showMessage("모자이크 오버레이 표시", 4000)
            return

        # ---- 2) pyqtgraph 기반 MapView --------------------------------
        if hasattr(mv, "addItem") or hasattr(mv, "plot_item"):
            try:
                import pyqtgraph as pg
                from PySide6.QtCore import QRectF
                item = pg.ImageItem(rgba[::-1].copy())
                item.setOpts(axisOrder="row-major")
                item.setRect(QRectF(lo0, la0, lo1 - lo0, la1 - la0))
                item.setZValue(-100)
                item.setOpacity(self.mosaic_opacity)
                tgt = mv if hasattr(mv, "addItem") else getattr(mv, "plot_item")
                tgt.addItem(item)
                self.mosaic_item = item
                self.mosaic_kind = "pg"
                self.act_mos_show.setChecked(True)
                self.statusBar().showMessage("모자이크 오버레이 표시", 4000)
                return
            except Exception:
                traceback.print_exc()

        # ---- 3) matplotlib 기반 MapView -------------------------------
        ax = getattr(mv, "ax", None) or getattr(mv, "axes", None)
        if ax is not None:
            try:
                im = ax.imshow(rgba, extent=[lo0, lo1, la0, la1],
                               origin="upper", zorder=1,
                               alpha=self.mosaic_opacity,
                               interpolation="bilinear")
                self.mosaic_item = im
                self.mosaic_kind = "mpl"
                canvas = getattr(mv, "canvas", None) or ax.figure.canvas
                canvas.draw_idle()
                self.act_mos_show.setChecked(True)
                self.statusBar().showMessage("모자이크 오버레이 표시", 4000)
                return
            except Exception:
                traceback.print_exc()

        QMessageBox.information(
            self, "모자이크 표시",
            "MapView(%s) 오버레이 호출이 모두 실패했습니다.\n"
            "콘솔의 [오버레이] 로그를 확인하세요.\n\n생성 파일:\n%s"
            % (type(mv).__name__, path))

    def remove_mosaic(self, silent=False):
        try:
            if self.mosaic_kind == "custom":
                for nm in ("remove_mosaic_overlay", "clear_image_overlay",
                           "clear_mosaic"):
                    fn = getattr(self.map, nm, None)
                    if callable(fn):
                        fn()
                        break
            elif self.mosaic_kind == "pg" and self.mosaic_item is not None:
                tgt = self.map if hasattr(self.map, "removeItem") \
                    else getattr(self.map, "plot_item", None)
                if tgt is not None:
                    tgt.removeItem(self.mosaic_item)
            elif self.mosaic_kind == "mpl" and self.mosaic_item is not None:
                self.mosaic_item.remove()
                ax = getattr(self.map, "ax", None) or \
                    getattr(self.map, "axes", None)
                if ax is not None:
                    ax.figure.canvas.draw_idle()
        except Exception:
            traceback.print_exc()
        self.mosaic_item = None
        self.mosaic_kind = None
        if not silent:
            self.statusBar().showMessage("모자이크 오버레이 제거", 3000)

    def _on_mosaic_visible(self, on):
        if self.mosaic_kind == "pg" and self.mosaic_item is not None:
            self.mosaic_item.setVisible(bool(on))
        elif self.mosaic_kind == "mpl" and self.mosaic_item is not None:
            self.mosaic_item.set_visible(bool(on))
            ax = getattr(self.map, "ax", None) or getattr(self.map, "axes", None)
            if ax is not None:
                ax.figure.canvas.draw_idle()
        elif self.mosaic_kind is None and on and self.mosaic_path:
            self.show_mosaic(self.mosaic_path)

    def _on_mosaic_opacity(self, val):
        self.mosaic_opacity = max(0.0, min(1.0, val / 100.0))
        self._apply_mosaic_opacity()

    def _apply_mosaic_opacity(self):
        if self.mosaic_kind == "pg" and self.mosaic_item is not None:
            self.mosaic_item.setOpacity(self.mosaic_opacity)
        elif self.mosaic_kind == "mpl" and self.mosaic_item is not None:
            self.mosaic_item.set_alpha(self.mosaic_opacity)
            ax = getattr(self.map, "ax", None) or getattr(self.map, "axes", None)
            if ax is not None:
                ax.figure.canvas.draw_idle()
        elif self.mosaic_kind == "custom":
            fn = getattr(self.map, "set_overlay_opacity", None)
            if callable(fn):
                try:
                    fn(self.mosaic_opacity)
                except Exception:
                    pass
    
    def closeEvent(self, event):
        """창 닫기 / 종료 메뉴 공통 처리."""
        ret = QMessageBox.question(
            self, "종료 확인", "프로그램을 종료하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            event.ignore()
            return
        try:
            self.map_view.clear_all()       # 웹엔진 리소스 정리
        except Exception:
            pass
        event.accept()

# ==================================================== 엔트리 포인트
def main():
    from PySide6.QtCore import QLoggingCategory
    from PySide6.QtGui import QFont

    # Windows DirectWrite 비트맵 폰트(MS Sans Serif) 폴백 관련 불필요한 시스템 경고 억제
    QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false")

    app = QApplication(sys.argv)
    app_font = QFont("Malgun Gothic", 9)
    app_font.setStyleHint(QFont.SansSerif)
    app_font.setFamilies(["Malgun Gothic", "Segoe UI", "Arial", "sans-serif"])
    app.setFont(app_font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
