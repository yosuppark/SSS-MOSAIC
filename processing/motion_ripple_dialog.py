# -*- coding: utf-8 -*-
"""motion_ripple_dialog.py — 토우피쉬 롤링/피칭 리플 해석 및 모션 보정 대화상자 (PySide6)

파도에 의해 발생하는 예인형 사이드스캔 소나(Towed SSS)의 롤링(Rolling, 좌우 교차 반전) 및
피칭(Pitching, 전후 동위상) 리플 패턴을 마우스 ROI 지정 영역으로부터 자동 분석하고,
사용자 인터랙티브 튜닝을 거쳐 소나 영상의 줄무늬 왜곡을 개선하는 통합 다이얼로그입니다.
"""
from __future__ import annotations

import os
import math
import numpy as np

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QImage, QPixmap,
    QPaintEvent, QMouseEvent
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QSlider, QSpinBox, QDoubleSpinBox,
    QComboBox, QRadioButton, QButtonGroup, QSplitter, QWidget,
    QFrame, QMessageBox, QTabWidget, QSizePolicy, QCheckBox
)

from processing.motion_ripple import (
    extract_channel_profiles, analyze_motion_ripple, correct_motion_ripple, smooth_1d
)


# ==============================================================================
# 1. 시계열 강도 프로파일 그래프 뷰 (RippleProfileGraphView)
# ==============================================================================

class RippleProfileGraphView(QWidget):
    """핑 진행에 따른 좌/우현 강도 시계열 파형 (교차 롤링 vs 동위상 피칭 시각화)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(140)
        self.setStyleSheet("background-color: #16161a; border-radius: 4px;")

        self.r0 = 0
        self.r1 = 0
        self.p_osc = np.zeros(0)
        self.s_osc = np.zeros(0)
        self.corr_prof = None
        self.suppression_pct = 0.0
        self.hover_idx = -1

    def set_data(
        self, r0: int, r1: int,
        p_osc: np.ndarray, s_osc: np.ndarray,
        corr_prof: np.ndarray | None = None,
        suppression_pct: float = 0.0
    ):
        self.r0 = r0
        self.r1 = r1
        self.p_osc = np.asarray(p_osc, dtype=np.float64)
        self.s_osc = np.asarray(s_osc, dtype=np.float64)
        self.corr_prof = np.asarray(corr_prof, dtype=np.float64) if corr_prof is not None else None
        self.suppression_pct = float(suppression_pct)
        self.update()

    def paintEvent(self, ev: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#16161a"))

        n = max(len(self.p_osc), len(self.s_osc))
        if n < 2:
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(self.rect(), Qt.AlignCenter, "ROI 영역을 지정하면 시계열 강도 파형이 표시됩니다.")
            return

        w = self.width()
        h = self.height()
        ml, mr, mt, mb = 45, 15, 12, 22
        pw = w - ml - mr
        ph = h - mt - mb

        # Y축 범위 (대칭 ±max_val)
        max_val = max(1.0, float(np.max(np.abs(self.p_osc))), float(np.max(np.abs(self.s_osc)))) * 1.15

        # 배경 0선 및 격자
        painter.setPen(QPen(QColor(60, 60, 70), 1, Qt.DashLine))
        y0 = mt + ph * 0.5
        painter.drawLine(ml, int(y0), ml + pw, int(y0))

        painter.setFont(QFont("Malgun Gothic", 8))
        painter.setPen(QColor(140, 140, 150))
        painter.drawText(5, int(y0) + 4, " 0.0")
        painter.drawText(5, mt + 10, f"+{max_val:.1f}")
        painter.drawText(5, mt + ph, f"-{max_val:.1f}")

        # X축 핑 레이블
        painter.drawText(ml, h - 5, f"Ping #{self.r0}")
        painter.drawText(ml + pw - 60, h - 5, f"Ping #{self.r1}")

        def to_screen(i, val):
            sx = ml + (float(i) / max(n - 1, 1)) * pw
            sy = mt + (0.5 - float(val) / (2.0 * max_val)) * ph
            return sx, sy

        # 1. Port 파형 (청록/Cyan: #00E5FF)
        pen_p = QPen(QColor(0, 229, 255, 220), 1.6)
        painter.setPen(pen_p)
        step = max(1, n // 600)
        for i in range(0, n - step, step):
            sx0, sy0 = to_screen(i, self.p_osc[i])
            sx1, sy1 = to_screen(i + step, self.p_osc[i + step])
            painter.drawLine(QPointF(sx0, sy0), QPointF(sx1, sy1))

        # 2. Starboard 파형 (코랄/Magenta: #FF5252)
        pen_s = QPen(QColor(255, 82, 82, 220), 1.6)
        painter.setPen(pen_s)
        for i in range(0, n - step, step):
            sx0, sy0 = to_screen(i, self.s_osc[i])
            sx1, sy1 = to_screen(i + step, self.s_osc[i + step])
            painter.drawLine(QPointF(sx0, sy0), QPointF(sx1, sy1))

        # 3. 보정 후 잔여 파형 (녹색 실선: #00E676, 있을 경우)
        if self.corr_prof is not None and len(self.corr_prof) == n:
            pen_c = QPen(QColor(0, 230, 118, 240), 2.0)
            painter.setPen(pen_c)
            for i in range(0, n - step, step):
                sx0, sy0 = to_screen(i, self.corr_prof[i])
                sx1, sy1 = to_screen(i + step, self.corr_prof[i + step])
                painter.drawLine(QPointF(sx0, sy0), QPointF(sx1, sy1))

        # 4. 상단 뱃지: 리플 저감율 표시
        if self.corr_prof is not None:
            badge_txt = f"★ 리플 억제율: {self.suppression_pct:.1f}% 감소"
            painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            painter.setPen(QColor(0, 230, 118))
            painter.drawText(ml + 10, mt + 14, badge_txt)

        # 5. 범례
        painter.setFont(QFont("Malgun Gothic", 8))
        lx = ml + pw - 250
        ly = mt + 4

        # Port 범례
        painter.setPen(QColor(0, 229, 255))
        painter.drawLine(lx, ly + 5, lx + 15, ly + 5)
        painter.drawText(lx + 20, ly + 9, "좌현(Port)")

        # Stbd 범례
        painter.setPen(QColor(255, 82, 82))
        painter.drawLine(lx + 80, ly + 5, lx + 95, ly + 5)
        painter.drawText(lx + 100, ly + 9, "우현(Stbd)")

        # 보정 후 잔여파 범례
        if self.corr_prof is not None:
            painter.setPen(QColor(0, 230, 118))
            painter.drawLine(lx + 160, ly + 5, lx + 175, ly + 5)
            painter.drawText(lx + 180, ly + 9, "보정 잔여파")


# ==============================================================================
# 2. 주파수 스펙트럼 뷰 (RippleSpectrumGraphView)
# ==============================================================================

class RippleSpectrumGraphView(QWidget):
    """FFT 전력 스펙트럼 밀도(PSD) 및 지배 파랑 피크 표시 뷰."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)
        self.setStyleSheet("background-color: #16161a; border-radius: 4px;")

        self.freqs = np.zeros(0)
        self.psd_s = np.zeros(0)
        self.psd_p = np.zeros(0)
        self.dom_period = 0.0
        self.dom_freq = 0.0

    def set_spectrum(self, freqs: np.ndarray, psd_s: np.ndarray, psd_p: np.ndarray, dom_period: float, dom_freq: float):
        self.freqs = np.asarray(freqs, dtype=np.float64)
        self.psd_s = np.asarray(psd_s, dtype=np.float64)
        self.psd_p = np.asarray(psd_p, dtype=np.float64)
        self.dom_period = dom_period
        self.dom_freq = dom_freq
        self.update()

    def paintEvent(self, ev: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#16161a"))

        if len(self.freqs) < 4:
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(self.rect(), Qt.AlignCenter, "스펙트럼 분석 대기 중...")
            return

        w = self.width()
        h = self.height()
        ml, mr, mt, mb = 45, 15, 12, 22
        pw = w - ml - mr
        ph = h - mt - mb

        # 0 ~ 0.25 cycles/ping 주파수 영역 집중 표시
        max_f = 0.25
        mask = (self.freqs >= 0.005) & (self.freqs <= max_f)
        sub_f = self.freqs[mask]
        sub_p = (self.psd_s[mask] + self.psd_p[mask])
        if len(sub_p) == 0:
            return

        max_pow = max(1.0, float(np.max(sub_p))) * 1.1

        # 배경 축
        painter.setPen(QPen(QColor(60, 60, 70), 1))
        painter.drawLine(ml, mt + ph, ml + pw, mt + ph)
        painter.drawLine(ml, mt, ml, mt + ph)

        painter.setFont(QFont("Malgun Gothic", 8))
        painter.setPen(QColor(140, 140, 150))
        painter.drawText(ml, h - 5, "0.0")
        painter.drawText(ml + pw // 2 - 20, h - 5, f"{max_f/2.0:.3f}")
        painter.drawText(ml + pw - 45, h - 5, f"{max_f:.2f} c/p")

        # 스펙트럼 곡선 렌더링
        poly = QPainterPath()
        poly.moveTo(ml, mt + ph)
        for i in range(len(sub_f)):
            sx = ml + (sub_f[i] / max_f) * pw
            sy = mt + ph - (sub_p[i] / max_pow) * ph
            poly.lineTo(sx, sy)
        poly.lineTo(ml + pw, mt + ph)
        poly.closeSubpath()

        # 채우기 & 외곽선
        painter.fillPath(poly, QBrush(QColor(255, 193, 7, 50)))
        painter.setPen(QPen(QColor(255, 193, 7, 220), 1.8))
        painter.drawPath(poly)

        # 지배 피크 주파수 수직 마커 표시
        if self.dom_freq > 0 and self.dom_freq <= max_f:
            peak_x = ml + (self.dom_freq / max_f) * pw
            painter.setPen(QPen(QColor(0, 229, 255), 1.5, Qt.DashLine))
            painter.drawLine(int(peak_x), mt, int(peak_x), mt + ph)

            # 피크 뱃지 레이블
            txt = f"★ 지배 피크: 주기 {self.dom_period:.1f} 핑 ({self.dom_freq:.3f} c/p)"
            painter.setFont(QFont("Malgun Gothic", 8, QFont.Bold))
            painter.setPen(QColor(0, 229, 255))
            painter.drawText(int(max(ml + 10, min(peak_x + 5, w - 180))), mt + 14, txt)


# ==============================================================================
# 3. 소나 영상 패치 비교 뷰 (RippleImageCompareView)
# ==============================================================================

class RippleImageCompareView(QWidget):
    """원본 영상 vs 보정 후 영상의 ROI 패치 비교 뷰어."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setStyleSheet("background-color: #121214; border-radius: 4px;")

        self.orig_pix = QPixmap()
        self.corr_pix = QPixmap()
        self.diff_pix = QPixmap()
        self.view_mode = "split"  # "split", "orig", "corr", "diff", "toggle"
        self._toggle_state = False

    def set_images(self, orig_sub: np.ndarray, corr_sub: np.ndarray, diff_sub: np.ndarray | None = None):
        h, w = orig_sub.shape[:2]

        def to_pixmap(arr):
            arr = np.ascontiguousarray(arr, dtype=np.uint8)
            qimg = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
            return QPixmap.fromImage(qimg)

        self.orig_pix = to_pixmap(orig_sub)
        self.corr_pix = to_pixmap(corr_sub)
        if diff_sub is not None:
            self.diff_pix = to_pixmap(diff_sub)
        self.update()

    def set_view_mode(self, mode: str):
        self.view_mode = mode
        self.update()

    def toggle_ab(self):
        """A(원본)와 B(보정 후)를 번갈아 깜빡이며 비교."""
        self.view_mode = "toggle"
        self._toggle_state = not self._toggle_state
        self.update()

    def mousePressEvent(self, ev: QMouseEvent):
        if self.view_mode == "toggle":
            self._toggle_state = not self._toggle_state
            self.update()
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self.toggle_ab()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def paintEvent(self, ev: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#121214"))

        if self.orig_pix.isNull():
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(self.rect(), Qt.AlignCenter, "영상 패치 미리보기 대기 중...")
            return

        w = self.width()
        h = self.height()

        if self.view_mode == "split":
            # 좌: 원본 / 우: 보정 후 (나란히 비교)
            half_w = (w - 6) // 2
            r_left = QRectF(0, 0, half_w, h)
            r_right = QRectF(half_w + 6, 0, half_w, h)

            if not self.orig_pix.isNull():
                painter.drawPixmap(r_left.toRect(), self.orig_pix)
            if not self.corr_pix.isNull():
                painter.drawPixmap(r_right.toRect(), self.corr_pix)

            # 중앙 분할선
            painter.setPen(QPen(QColor(0, 229, 255), 2))
            painter.drawLine(half_w + 3, 0, half_w + 3, h)

            # 상단 태그 뱃지
            painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            painter.setPen(Qt.white)
            painter.setBrush(QBrush(QColor(30, 30, 40, 200)))
            painter.drawRoundedRect(8, 8, 120, 22, 4, 4)
            painter.drawText(16, 23, "◀ 원본 (Original)")

            painter.drawRoundedRect(half_w + 14, 8, 130, 22, 4, 4)
            painter.setPen(QColor(0, 230, 118))
            painter.drawText(half_w + 22, 23, "보정 후 (Corrected) ▶")

        elif self.view_mode == "toggle":
            # A/B 깜빡임 비교 모드
            pix = self.corr_pix if self._toggle_state else self.orig_pix
            if not pix.isNull():
                painter.drawPixmap(self.rect(), pix)

            painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            painter.setBrush(QBrush(QColor(20, 20, 25, 210)))
            if self._toggle_state:
                painter.setPen(QColor(0, 230, 118))
                painter.drawRoundedRect(10, 10, 210, 24, 4, 4)
                painter.drawText(18, 26, "⚡ [B] 보정 후 영상 (Corrected)")
            else:
                painter.setPen(QColor(255, 82, 82))
                painter.drawRoundedRect(10, 10, 200, 24, 4, 4)
                painter.drawText(18, 26, "⚡ [A] 원본 영상 (Original)")

            painter.setFont(QFont("Malgun Gothic", 8))
            painter.setPen(QColor(200, 200, 210))
            painter.drawText(15, h - 12, "💡 클릭 또는 [A/B 깜빡임] 버튼을 누르면 원본과 보정본이 즉시 전환됩니다.")

        else:
            pix = self.corr_pix if self.view_mode == "corr" else (self.diff_pix if self.view_mode == "diff" else self.orig_pix)
            if not pix.isNull():
                painter.drawPixmap(self.rect(), pix)

            painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            painter.setBrush(QBrush(QColor(30, 30, 40, 200)))
            if self.view_mode == "corr":
                painter.drawRoundedRect(10, 10, 140, 22, 4, 4)
                painter.setPen(QColor(0, 230, 118))
                painter.drawText(18, 25, "보정 후 (Corrected)")
            elif self.view_mode == "diff":
                painter.drawRoundedRect(10, 10, 170, 22, 4, 4)
                painter.setPen(QColor(255, 215, 64))
                painter.drawText(18, 25, "추출된 리플 노이즈 (Noise)")
            else:
                painter.drawRoundedRect(10, 10, 120, 22, 4, 4)
                painter.setPen(Qt.white)
                painter.drawText(18, 25, "원본 (Original)")


# ==============================================================================
# 4. 토우피쉬 모션 리플 대화상자 메인 (TowfishMotionRippleDialog)
# ==============================================================================

class TowfishMotionRippleDialog(QDialog):
    """토우피쉬 롤링/피칭 리플 해석 및 모션 보정 메인 대화상자."""

    # (보정된 2D uint8 이미지, 전체 핑 적용 여부)
    correctionApplied = Signal(object, bool)
    # 메인 윈도우 실시간 연동 시그널 (preview_img, 전체 적용 여부)
    livePreviewRequested = Signal(object, bool)

    def __init__(self, parent=None, img=None, meta=None, filename=""):
        super().__init__(parent)
        self.setWindowTitle("토우피쉬 모션 리플(Rolling/Pitching Ripple) 해석 및 보정")
        self.resize(1060, 680)

        self.img = img
        self.meta = meta or []
        self.filename = filename

        # 현재 선택된 ROI 범위 (기본: 중앙부 500핑)
        n_pings = len(img) if img is not None else 1000
        self.r0 = max(0, min(250, n_pings // 4))
        self.r1 = min(n_pings, self.r0 + min(500, n_pings))
        self.col_range = None

        self.analysis_res = {}
        self.corr_sub = None
        self.rip_sub = None

        self._build_ui()
        self.run_analysis()

    def set_roi(self, r0: int, r1: int, c0: int | None = None, c1: int | None = None):
        """외부(WaterfallView)에서 마우스로 드래그한 ROI 갱신."""
        if self.img is None:
            return
        n_pings = len(self.img)
        self.r0 = max(0, min(int(r0), n_pings - 10))
        self.r1 = max(self.r0 + 10, min(int(r1), n_pings))
        if c0 is not None and c1 is not None and c1 > c0:
            self.col_range = (int(c0), int(c1))
        else:
            self.col_range = None

        self.run_analysis()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---------------- 좌측 제어 및 분석 패널 ----------------
        left_widget = QWidget()
        left_widget.setFixedWidth(430)
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        # 1. ROI 정보 그룹
        grp_roi = QGroupBox("선택된 관심 영역 (ROI)")
        roi_lay = QGridLayout(grp_roi)
        roi_lay.setSpacing(6)

        self.lbl_file = QLabel(os.path.basename(self.filename) if self.filename else "-")
        self.lbl_file.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.lbl_ping_range = QLabel(f"Ping #{self.r0} ~ #{self.r1}")
        self.lbl_ping_count = QLabel(f"{self.r1 - self.r0} 핑")

        roi_lay.addWidget(QLabel("파일명:"), 0, 0)
        roi_lay.addWidget(self.lbl_file, 0, 1)
        roi_lay.addWidget(QLabel("선택 핑:"), 1, 0)
        roi_lay.addWidget(self.lbl_ping_range, 1, 1)
        roi_lay.addWidget(QLabel("구간 크기:"), 2, 0)
        roi_lay.addWidget(self.lbl_ping_count, 2, 1)

        left_lay.addWidget(grp_roi)

        # 2. 패턴 해석 및 모션 진단 그룹
        grp_diag = QGroupBox("패턴 해석 및 모션 진단")
        diag_lay = QGridLayout(grp_diag)
        diag_lay.setSpacing(6)

        self.lbl_period = QLabel("-")
        self.lbl_period.setStyleSheet("font-weight: bold; font-size: 13px; color: #00E5FF;")
        self.lbl_freq = QLabel("-")
        self.lbl_mode = QLabel("-")
        self.lbl_mode.setStyleSheet("font-weight: bold; font-size: 12px; color: #FFD54F;")
        self.lbl_corr = QLabel("-")
        self.lbl_depth = QLabel("-")

        diag_lay.addWidget(QLabel("지배 파랑 주기:"), 0, 0)
        diag_lay.addWidget(self.lbl_period, 0, 1)
        diag_lay.addWidget(QLabel("지배 주파수:"), 1, 0)
        diag_lay.addWidget(self.lbl_freq, 1, 1)
        diag_lay.addWidget(QLabel("운동 모드 판별:"), 2, 0)
        diag_lay.addWidget(self.lbl_mode, 2, 1)
        diag_lay.addWidget(QLabel("좌/우현 상관계수:"), 3, 0)
        diag_lay.addWidget(self.lbl_corr, 3, 1)
        diag_lay.addWidget(QLabel("리플 변조 진폭:"), 4, 0)
        diag_lay.addWidget(self.lbl_depth, 4, 1)

        lbl_desc = QLabel(
            "💡 파도에 의한 롤링은 좌우현 빔의 입사각 변화로 교차 명암 줄무늬를 발생시키며(역위상), "
            "피칭은 양현 빔의 전후 기울임으로 동시 명암 변동(동위상)을 일으킵니다."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #9E9E9E; font-size: 10px; margin-top: 4px;")
        diag_lay.addWidget(lbl_desc, 5, 0, 1, 2)

        left_lay.addWidget(grp_diag)

        # 3. 보정 파라미터 그룹
        grp_param = QGroupBox("리플 보정 파라미터 설정")
        param_lay = QGridLayout(grp_param)
        param_lay.setSpacing(8)

        # 알고리즘 선택
        self.cmb_method = QComboBox()
        self.cmb_method.addItem("적응형 디스트라이핑 (Adaptive Destripe) - 추천", "adaptive_destripe")
        self.cmb_method.addItem("롤/피치 성분 분리 보정 (Decoupled Roll/Pitch)", "decoupled_roll_pitch")
        self.cmb_method.addItem("주파수 대역차단 노치 필터 (FFT Band-Stop)", "fft_notch")
        self.cmb_method.currentIndexChanged.connect(self._on_param_changed)

        param_lay.addWidget(QLabel("보정 알고리즘:"), 0, 0)
        param_lay.addWidget(self.cmb_method, 0, 1, 1, 2)

        # 보정 강도 슬라이더
        self.sl_strength = QSlider(Qt.Horizontal)
        self.sl_strength.setRange(0, 100)
        self.sl_strength.setValue(85)
        self.lbl_strength = QLabel("85%")
        self.sl_strength.valueChanged.connect(self._on_strength_slider)

        param_lay.addWidget(QLabel("보정 강도:"), 1, 0)
        param_lay.addWidget(self.sl_strength, 1, 1)
        param_lay.addWidget(self.lbl_strength, 1, 2)

        # 필터 윈도우 크기
        self.sp_window = QSpinBox()
        self.sp_window.setRange(5, 151)
        self.sp_window.setSingleStep(2)
        self.sp_window.setValue(27)
        self.sp_window.setSuffix(" 핑")
        self.sp_window.setToolTip("파랑 주기의 약 1.5 ~ 2배 윈도우 크기가 권장됩니다.")
        self.sp_window.valueChanged.connect(self._on_param_changed)

        param_lay.addWidget(QLabel("필터 윈도우:"), 2, 0)
        param_lay.addWidget(self.sp_window, 2, 1, 1, 2)

        # 적용 범위 선택 (ROI만 vs 전체)
        self.rb_full = QRadioButton("전체 핑에 일괄 적용 (Full Line, 권장)")
        self.rb_full.setChecked(True)
        self.rb_roi = QRadioButton("선택된 ROI 구간만 적용 (Local ROI)")
        self.bg_scope = QButtonGroup(self)
        self.bg_scope.addButton(self.rb_full, 0)
        self.bg_scope.addButton(self.rb_roi, 1)

        param_lay.addWidget(self.rb_full, 3, 0, 1, 3)
        param_lay.addWidget(self.rb_roi, 4, 0, 1, 3)

        # 메인 윈도우 실시간 연동 체크박스
        self.chk_live_sync = QCheckBox("☑ 메인 Waterfall 실시간 미리보기 연동 (Live Sync)")
        self.chk_live_sync.setStyleSheet("color: #00E5FF; font-weight: bold;")
        self.chk_live_sync.setChecked(True)
        self.chk_live_sync.toggled.connect(self._on_live_sync_toggled)
        param_lay.addWidget(self.chk_live_sync, 5, 0, 1, 3)

        left_lay.addWidget(grp_param)

        # 하단 조작 버튼
        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)

        btn_preview = QPushButton("🔄 미리보기 갱신")
        btn_preview.clicked.connect(self.update_preview)
        btn_box.addWidget(btn_preview)

        btn_apply = QPushButton("💾 화면 및 메모리에 적용")
        btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
        """)
        btn_apply.clicked.connect(self._on_apply_clicked)
        btn_box.addWidget(btn_apply)

        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.reject)
        btn_box.addWidget(btn_close)

        left_lay.addLayout(btn_box)
        left_lay.addStretch(1)

        root.addWidget(left_widget)

        # ---------------- 우측 그래프 및 영상 비교 뷰 ----------------
        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        # 1. 시계열 프로파일 그래프
        lbl_g1 = QLabel("<b>[ Along-track 강도 시계열 파형 (좌/우현 상호 위상) ]</b>")
        lbl_g1.setStyleSheet("color: #cfd8dc; font-size: 11px;")
        right_lay.addWidget(lbl_g1)
        self.graph_profile = RippleProfileGraphView(self)
        right_lay.addWidget(self.graph_profile)

        # 2. 주파수 스펙트럼 그래프
        lbl_g2 = QLabel("<b>[ FFT 주파수 스펙트럼 및 파랑 지배 피크 (Dominant Swell Peak) ]</b>")
        lbl_g2.setStyleSheet("color: #cfd8dc; font-size: 11px;")
        right_lay.addWidget(lbl_g2)
        self.graph_spectrum = RippleSpectrumGraphView(self)
        right_lay.addWidget(self.graph_spectrum)

        # 3. 소나 영상 패치 비교 뷰
        lbl_g3 = QLabel("<b>[ 소나 영상 패치 보정 전/후 비교 ]</b>")
        lbl_g3.setStyleSheet("color: #cfd8dc; font-size: 11px;")

        self.view_image = RippleImageCompareView(self)

        mode_box = QHBoxLayout()
        mode_box.addWidget(lbl_g3)
        mode_box.addStretch(1)

        self.btn_vm_split = QPushButton("좌우 분할 비교")
        self.btn_vm_split.setCheckable(True)
        self.btn_vm_split.setChecked(True)
        self.btn_vm_orig = QPushButton("원본만")
        self.btn_vm_orig.setCheckable(True)
        self.btn_vm_corr = QPushButton("보정 후만")
        self.btn_vm_corr.setCheckable(True)
        self.btn_vm_diff = QPushButton("리플 노이즈")
        self.btn_vm_diff.setCheckable(True)
        self.btn_toggle_ab = QPushButton("⚡ A/B 깜빡임 비교")
        self.btn_toggle_ab.setToolTip("클릭할 때마다 원본과 보정본이 즉시 전환되어 잔여 리플을 정밀하게 대조합니다.")

        self.bg_vm = QButtonGroup(self)
        self.bg_vm.addButton(self.btn_vm_split)
        self.bg_vm.addButton(self.btn_vm_orig)
        self.bg_vm.addButton(self.btn_vm_corr)
        self.bg_vm.addButton(self.btn_vm_diff)

        self.btn_vm_split.clicked.connect(lambda: self.view_image.set_view_mode("split"))
        self.btn_vm_orig.clicked.connect(lambda: self.view_image.set_view_mode("orig"))
        self.btn_vm_corr.clicked.connect(lambda: self.view_image.set_view_mode("corr"))
        self.btn_vm_diff.clicked.connect(lambda: self.view_image.set_view_mode("diff"))
        self.btn_toggle_ab.clicked.connect(self.view_image.toggle_ab)

        mode_box.addWidget(self.btn_vm_split)
        mode_box.addWidget(self.btn_vm_orig)
        mode_box.addWidget(self.btn_vm_corr)
        mode_box.addWidget(self.btn_vm_diff)
        mode_box.addWidget(self.btn_toggle_ab)

        right_lay.addLayout(mode_box)
        right_lay.addWidget(self.view_image, 1)

        root.addWidget(right_widget, 1)

    def _on_strength_slider(self, val):
        self.lbl_strength.setText(f"{val}%")
        self.update_preview()

    def _on_param_changed(self):
        self.update_preview()

    def run_analysis(self):
        """선택된 ROI 구간의 파형 및 스펙트럼 분석 실행."""
        if self.img is None or len(self.img) == 0:
            return

        # 핑 레이블 갱신
        self.lbl_ping_range.setText(f"Ping #{self.r0} ~ #{self.r1}")
        self.lbl_ping_count.setText(f"{self.r1 - self.r0} 핑")

        # 핑 전송률 추정
        ping_rate = 10.0
        if self.meta and len(self.meta) >= 2:
            try:
                # meta time 기반 계산 시도
                pass
            except Exception:
                pass

        # 1. 프로파일 추출
        p_prof, s_prof, sym, roll = extract_channel_profiles(
            self.img, self.meta, self.r0, self.r1, self.col_range
        )

        # 2. 모션 분석
        res = analyze_motion_ripple(p_prof, s_prof, ping_rate_hz=ping_rate)
        self.analysis_res = res

        # 3. 진단 카드 업데이트
        dom_p = res.get("dominant_period_pings", 0.0)
        dom_s = res.get("dominant_period_sec", 0.0)
        dom_f = res.get("dominant_freq_hz", 0.0)
        dom_cpp = res.get("dominant_freq_cpp", 0.0)

        if dom_p > 0:
            self.lbl_period.setText(f"{dom_p:.1f} 핑 ({dom_s:.2f} 초)")
            self.lbl_freq.setText(f"{dom_f:.2f} Hz ({dom_cpp:.4f} c/p)")
            # 필터 윈도우 자동 권장값 설정
            rec_w = max(9, int(round(dom_p * 1.5)))
            if rec_w % 2 == 0:
                rec_w += 1
            self.sp_window.blockSignals(True)
            self.sp_window.setValue(rec_w)
            self.sp_window.blockSignals(False)
        else:
            self.lbl_period.setText("주기 불분명")
            self.lbl_freq.setText("-")

        self.lbl_mode.setText(res.get("motion_mode", "-"))
        self.lbl_corr.setText(f"r = {res.get('correlation', 0.0):.2f}")
        self.lbl_depth.setText(f"{res.get('modulation_depth_pct', 0.0):.1f}%")

        # 4. 그래프 뷰 업데이트
        self.graph_profile.set_data(
            self.r0, self.r1,
            res.get("p_osc", np.zeros(0)),
            res.get("s_osc", np.zeros(0))
        )
        self.graph_spectrum.set_spectrum(
            res.get("freqs", np.zeros(0)),
            res.get("psd_stbd", np.zeros(0)),
            res.get("psd_port", np.zeros(0)),
            dom_p, dom_cpp
        )

        # 5. 미리보기 계산
        self.update_preview()

    def _on_live_sync_toggled(self, checked: bool):
        """메인 Waterfall 실시간 미리보기 동기화 토글."""
        if not checked:
            if self.img is not None:
                self.livePreviewRequested.emit(self.img, False)
        else:
            self.update_preview()

    def update_preview(self):
        """현재 파라미터로 보정 적용 및 영상 패치/잔여 파형/메인화면 갱신."""
        if self.img is None or self.r1 <= self.r0:
            return

        method = self.cmb_method.currentData()
        strength = self.sl_strength.value() / 100.0
        w_filt = self.sp_window.value()
        f0 = self.analysis_res.get("dominant_freq_cpp", 0.045)
        apply_full = self.rb_full.isChecked()

        sub_orig = self.img[self.r0:self.r1]

        # ROI 또는 전체 적용 여부에 따른 보정 미리보기 계산
        r0_target = 0 if apply_full else self.r0
        r1_target = len(self.img) if apply_full else self.r1

        corr_full, rip_full = correct_motion_ripple(
            self.img, method=method, strength=strength, window=w_filt,
            f0=f0, r0=r0_target, r1=r1_target, meta=self.meta
        )

        self.corr_sub = corr_full[self.r0:self.r1]
        self.rip_sub = rip_full[self.r0:self.r1]

        # 보정 후 프로파일 및 리플 억제율 계산
        p_c, s_c, _, _ = extract_channel_profiles(
            corr_full, self.meta, self.r0, self.r1, self.col_range
        )
        c_prof = s_c - smooth_1d(s_c, max(15, min(len(s_c) // 4 * 2 + 1, 61)))

        var_orig = float(np.var(self.analysis_res.get("s_osc", np.zeros(1))))
        var_corr = float(np.var(c_prof))
        suppression_pct = max(0.0, min(100.0, (1.0 - var_corr / max(var_orig, 1e-4)) * 100.0))

        self.graph_profile.set_data(
            self.r0, self.r1,
            self.analysis_res.get("p_osc", np.zeros(0)),
            self.analysis_res.get("s_osc", np.zeros(0)),
            corr_prof=c_prof,
            suppression_pct=suppression_pct
        )

        # 영상 패치 갱신
        self.view_image.set_images(sub_orig, self.corr_sub, self.rip_sub)

        # 메인 Waterfall 화면 실시간 연동 시그널 발행
        if getattr(self, "chk_live_sync", None) is not None and self.chk_live_sync.isChecked():
            self.livePreviewRequested.emit(corr_full, apply_full)

    def _on_apply_clicked(self):
        """보정 결과를 확정하고 상위 메인 윈도우로 시그널 발행."""
        if self.img is None:
            return

        method = self.cmb_method.currentData()
        strength = self.sl_strength.value() / 100.0
        w_filt = self.sp_window.value()
        f0 = self.analysis_res.get("dominant_freq_cpp", 0.045)
        apply_full = self.rb_full.isChecked()

        if apply_full:
            r0_apply, r1_apply = 0, len(self.img)
        else:
            r0_apply, r1_apply = self.r0, self.r1

        corr_full, _ = correct_motion_ripple(
            self.img, method=method, strength=strength, window=w_filt,
            f0=f0, r0=r0_apply, r1=r1_apply, meta=self.meta
        )

        self.correctionApplied.emit(corr_full, apply_full)
        self.accept()
