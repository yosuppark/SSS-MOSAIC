# -*- coding: utf-8 -*-
"""fishbone_dialog.py — 항적 및 헤딩 생선 가시(Fishbone) 시각화 및 보정 튜너 대화상자

사이드스캔 소나의 각 핑 위치와 헤딩을 생선 가시(좌우 빔 발사 방향) 형태로 시각화하여,
보간 전(1초 간격 듬성듬성한 가시)과 보간 후(매 핑 촘촘하고 유려한 가시)를 눈으로 직접 확인하고
파라미터를 실시간으로 조절한 뒤 메모리에 즉시 적용하는 독립 대화상자입니다.
"""
from __future__ import annotations

import os
import math
import numpy as np

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QWheelEvent,
    QMouseEvent, QPaintEvent
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QSlider, QDoubleSpinBox, QSpinBox,
    QComboBox, QRadioButton, QButtonGroup, QSplitter, QWidget,
    QFrame, QMessageBox, QApplication, QCheckBox
)

from processing.nav_interpolator import (
    process_navigation, compute_fishbone_rays, apply_interpolated_nav_to_meta
)
from processing.nav_io import save_nav


# ==============================================================================
# 1. 생선 가시 2D 캔버스 (FishboneCanvas)
# ==============================================================================

class FishboneCanvas(QWidget):
    """항적(척추)과 핑별 좌우 빔(가시)을 고성능 QPainter로 렌더링하는 인터랙티브 캔버스."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.WheelFocus)
        self.setStyleSheet("background-color: #16161a; border-radius: 6px;")

        # 원본 및 보간 좌표 데이터
        self.raw_east = np.zeros(0)
        self.raw_north = np.zeros(0)
        self.raw_hdg = np.zeros(0)
        self.raw_nodes = np.zeros(0, dtype=int)

        self.interp_east = np.zeros(0)
        self.interp_north = np.zeros(0)
        self.interp_hdg = np.zeros(0)

        # 핑별 슬랜트 레인지 (파일 실제 기록값)
        self.range_port = np.zeros(0)
        self.range_stbd = np.zeros(0)
        self.default_ray_length = 80.0

        # 뷰포트 변환 (Pan & Zoom)
        self.scale = 1.0          # 픽셀 / 미터
        self.pan_x = 0.0          # 화면 중심 기준 오프셋 (미터)
        self.pan_y = 0.0
        self.is_dragging = False
        self.last_mouse_pos = QPointF()

        # 시각화 설정
        self.user_ray_length = 80.0  # 사용자 설정 가시 길이 (미터)
        self.stride = 5              # 핑 간격
        self.view_mode = "compare"   # "interp", "compare", "raw"
        self.anti_overlap = True     # 줌아웃 시 가시 겹침 방지 (화면 최소 픽셀 거리 필터)
        self.hover_ping_idx = -1

    def set_data(self, raw_e, raw_n, raw_hd, raw_nodes, int_e, int_n, int_hd, rp, rs, def_len):
        """데이터 설정 및 초기 뷰 맞춤."""
        self.raw_east = np.asarray(raw_e, dtype=np.float64)
        self.raw_north = np.asarray(raw_n, dtype=np.float64)
        self.raw_hdg = np.asarray(raw_hd, dtype=np.float64)
        self.raw_nodes = np.asarray(raw_nodes, dtype=int) if raw_nodes is not None else np.arange(len(raw_e))

        self.interp_east = np.asarray(int_e, dtype=np.float64)
        self.interp_north = np.asarray(int_n, dtype=np.float64)
        self.interp_hdg = np.asarray(int_hd, dtype=np.float64)

        self.range_port = np.asarray(rp, dtype=np.float64)
        self.range_stbd = np.asarray(rs, dtype=np.float64)
        self.default_ray_length = float(def_len)
        self.user_ray_length = float(def_len)

        self.fit_view()
        self.update()

    def update_interp_data(self, int_e, int_n, int_hd, raw_hd=None):
        """보간 결과 및 헤딩 실시간 갱신 (뷰포트 유지)."""
        self.interp_east = np.asarray(int_e, dtype=np.float64)
        self.interp_north = np.asarray(int_n, dtype=np.float64)
        self.interp_hdg = np.asarray(int_hd, dtype=np.float64)
        if raw_hd is not None:
            self.raw_hdg = np.asarray(raw_hd, dtype=np.float64)
        self.update()

    def fit_view(self):
        """전체 항적이 화면에 꼭 맞도록 축척 및 중심 자동 계산."""
        if len(self.interp_east) < 2 and len(self.raw_east) < 2:
            return
        e = self.interp_east if len(self.interp_east) > 1 else self.raw_east
        n = self.interp_north if len(self.interp_north) > 1 else self.raw_north

        min_e, max_e = float(np.min(e)), float(np.max(e))
        min_n, max_n = float(np.min(n)), float(np.max(n))

        span_e = max(max_e - min_e + self.user_ray_length * 2.5, 30.0)
        span_n = max(max_n - min_n + self.user_ray_length * 2.5, 30.0)

        w = max(self.width(), 100)
        h = max(self.height(), 100)

        scale_x = (w * 0.85) / span_e
        scale_y = (h * 0.85) / span_n
        self.scale = min(scale_x, scale_y)

        self.pan_x = (min_e + max_e) / 2.0
        self.pan_y = (min_n + max_n) / 2.0
        self.update()

    # ------------------------------------------------ 마우스 인터랙션
    def wheelEvent(self, event: QWheelEvent):
        """마우스 커서 중심 확대/축소."""
        num_degrees = event.angleDelta().y() / 8.0
        factor = 1.15 if num_degrees > 0 else 0.87

        mouse_pos = event.position()
        m_east, m_north = self._screen_to_meter(mouse_pos.x(), mouse_pos.y())

        self.scale *= factor
        self.scale = max(0.005, min(self.scale, 50.0))

        new_m_east, new_m_north = self._screen_to_meter(mouse_pos.x(), mouse_pos.y())
        self.pan_x += (m_east - new_m_east)
        self.pan_y += (m_north - new_m_north)

        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton):
            self.is_dragging = True
            self.last_mouse_pos = event.position()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_dragging:
            delta = event.position() - self.last_mouse_pos
            self.pan_x -= delta.x() / self.scale
            self.pan_y += delta.y() / self.scale
            self.last_mouse_pos = event.position()
            self.update()
        else:
            m_east, m_north = self._screen_to_meter(event.position().x(), event.position().y())
            if len(self.interp_east) > 0:
                dist_sq = (self.interp_east - m_east) ** 2 + (self.interp_north - m_north) ** 2
                closest = int(np.argmin(dist_sq))
                if dist_sq[closest] <= (60.0 / max(self.scale, 1e-4)) ** 2:
                    self.hover_ping_idx = closest
                else:
                    self.hover_ping_idx = -1
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.is_dragging = False

    # ------------------------------------------------ 좌표 변환
    def _meter_to_screen(self, east, north):
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        sx = cx + (east - self.pan_x) * self.scale
        sy = cy - (north - self.pan_y) * self.scale
        return sx, sy

    def _screen_to_meter(self, sx, sy):
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        east = self.pan_x + (sx - cx) / self.scale
        north = self.pan_y - (sy - cy) / self.scale
        return east, north

    # ------------------------------------------------ 렌더링
    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#16161a"))  # 다크 배경 확실히 채움
        painter.setBrush(Qt.NoBrush)  # ★절대 폴리곤 채우기 금지★

        w, h = self.width(), self.height()

        # 1. 배경 그리드 및 눈금
        self._draw_grid(painter, w, h)

        # 2. 보간 후 생선가시 (배경에 매 핑 촘촘하고 유려한 청록색 가시 렌더링)
        if self.view_mode in ("interp", "compare") and len(self.interp_east) > 1:
            self._draw_interp_fishbone(painter)

        # 3. 보간 전 원본 생선가시 (전면에 1초 간격 듬성듬성한 적색 가시와 GPS 점 오버레이)
        if self.view_mode in ("compare", "raw") and len(self.raw_east) > 1:
            self._draw_raw_fishbone(painter)

        # 4. 호버 정보 오버레이
        if self.hover_ping_idx >= 0 and self.hover_ping_idx < len(self.interp_east):
            self._draw_hover_info(painter)

        # 5. 범례 (Legend)
        self._draw_legend(painter)

    def _draw_grid(self, painter: QPainter, w: int, h: int):
        """배경 미터 그리드 라인."""
        painter.setBrush(Qt.NoBrush)
        pen = QPen(QColor(50, 50, 60, 100), 1, Qt.DotLine)
        painter.setPen(pen)

        rough_step = 80.0 / max(self.scale, 1e-4)
        exp = math.floor(math.log10(rough_step))
        base = 10.0 ** exp
        candidates = [base, base * 2.0, base * 5.0, base * 10.0]
        step = min(candidates, key=lambda x: abs(x - rough_step))

        e0, n0 = self._screen_to_meter(0, h)
        e1, n1 = self._screen_to_meter(w, 0)

        start_e = math.floor(min(e0, e1) / step) * step
        end_e = math.ceil(max(e0, e1) / step) * step
        start_n = math.floor(min(n0, n1) / step) * step
        end_n = math.ceil(max(n0, n1) / step) * step

        e = start_e
        while e <= end_e:
            sx, _ = self._meter_to_screen(e, 0)
            painter.drawLine(int(sx), 0, int(sx), h)
            e += step

        n = start_n
        while n <= end_n:
            _, sy = self._meter_to_screen(0, n)
            painter.drawLine(0, int(sy), w, int(sy))
            n += step

    def _draw_raw_fishbone(self, painter: QPainter):
        """보간 전 원본 생선가시: 1초마다의 고유 GPS 위치에 듬성듬성한 적색 가시와 점선 항적 렌더링."""
        painter.setBrush(Qt.NoBrush)  # 폴리곤 채우기 절대 방지

        node_idx = self.raw_nodes if len(self.raw_nodes) > 1 else np.arange(0, len(self.raw_east), 10)
        node_idx = node_idx[node_idx < len(self.raw_east)]
        if len(node_idx) < 2:
            return

        e_nodes = self.raw_east[node_idx]
        n_nodes = self.raw_north[node_idx]
        hd_nodes = self.raw_hdg[node_idx]

        scale_ratio = self.user_ray_length / max(self.default_ray_length, 1e-4)
        if len(self.range_port) == len(self.raw_east):
            rp_nodes = self.range_port[node_idx] * scale_ratio
            rs_nodes = self.range_stbd[node_idx] * scale_ratio
        else:
            rp_nodes = np.full(len(node_idx), self.user_ray_length)
            rs_nodes = np.full(len(node_idx), self.user_ray_length)

        # 1초 노드 가시 좌표 계산
        sub_e, sub_n, p_e, p_n, s_e, s_n = compute_fishbone_rays(
            e_nodes, n_nodes, hd_nodes, ray_length_m=self.user_ray_length, stride=1,
            range_port=rp_nodes, range_stbd=rs_nodes
        )

        rib_pen = QPen(QColor(235, 77, 75, 230), 1.8)  # 선명한 산호 적색
        painter.setPen(rib_pen)
        painter.setBrush(Qt.NoBrush)

        min_spacing_px = 18.0 if self.anti_overlap else 0.0
        last_sx, last_sy = -999999.0, -999999.0

        for i in range(len(sub_e)):
            sx_c, sy_c = self._meter_to_screen(sub_e[i], sub_n[i])
            if min_spacing_px > 0:
                dist = math.hypot(sx_c - last_sx, sy_c - last_sy)
                if dist < min_spacing_px:
                    continue
            last_sx, last_sy = sx_c, sy_c

            sx_p, sy_p = self._meter_to_screen(p_e[i], p_n[i])
            sx_s, sy_s = self._meter_to_screen(s_e[i], s_n[i])
            painter.drawLine(QPointF(sx_p, sy_p), QPointF(sx_s, sy_s))

            # 1초 단위 GPS 위치 강조 마커 (빨간 점)
            painter.setBrush(QBrush(QColor(235, 77, 75, 240)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(sx_c, sy_c), 3, 3)
            painter.setPen(rib_pen)
            painter.setBrush(Qt.NoBrush)

        # 2. 원본 1초 척추(항적선) 점선 그리기 (폴리곤 채우기 절대 금지!)
        spine_pen = QPen(QColor(235, 77, 75, 200), 2.0, Qt.DashLine)
        painter.setPen(spine_pen)
        painter.setBrush(Qt.NoBrush)

        for i in range(len(e_nodes) - 1):
            sx0, sy0 = self._meter_to_screen(e_nodes[i], n_nodes[i])
            sx1, sy1 = self._meter_to_screen(e_nodes[i + 1], n_nodes[i + 1])
            painter.drawLine(QPointF(sx0, sy0), QPointF(sx1, sy1))

    def _draw_interp_fishbone(self, painter: QPainter):
        """보간 후 생선가시: 매 핑마다 촘촘하게 가시가 채워지고 유려하게 회전하는 청록색 렌더링."""
        painter.setBrush(Qt.NoBrush)  # 폴리곤 채우기 절대 방지

        n = len(self.interp_east)
        if n < 2:
            return

        stride = max(self.stride, 1)

        scale_ratio = self.user_ray_length / max(self.default_ray_length, 1e-4)
        if len(self.range_port) == n:
            rp_arr = self.range_port * scale_ratio
            rs_arr = self.range_stbd * scale_ratio
        else:
            rp_arr = np.full(n, self.user_ray_length)
            rs_arr = np.full(n, self.user_ray_length)

        # 가시 좌표 산출
        sub_e, sub_n, p_e, p_n, s_e, s_n = compute_fishbone_rays(
            self.interp_east, self.interp_north, self.interp_hdg,
            ray_length_m=self.user_ray_length, stride=stride,
            range_port=rp_arr, range_stbd=rs_arr
        )

        rib_pen = QPen(QColor(26, 188, 156, 180), 1.2)  # 에메랄드 청록색
        painter.setPen(rib_pen)
        painter.setBrush(Qt.NoBrush)

        min_spacing_px = 5.0 if self.anti_overlap else 0.0
        last_sx, last_sy = -999999.0, -999999.0

        for i in range(len(sub_e)):
            sx_c, sy_c = self._meter_to_screen(sub_e[i], sub_n[i])
            if min_spacing_px > 0:
                dist = math.hypot(sx_c - last_sx, sy_c - last_sy)
                if dist < min_spacing_px:
                    continue
            last_sx, last_sy = sx_c, sy_c

            sx_p, sy_p = self._meter_to_screen(p_e[i], p_n[i])
            sx_s, sy_s = self._meter_to_screen(s_e[i], s_n[i])
            painter.drawLine(QPointF(sx_p, sy_p), QPointF(sx_s, sy_s))

        # 2. 보간된 중심 항적선(Spine) 실선 그리기
        spine_pen = QPen(QColor(52, 152, 219, 240), 2.5)  # 선명한 파란색
        painter.setPen(spine_pen)
        painter.setBrush(Qt.NoBrush)

        step_pts = max(1, n // 1500)
        for i in range(0, n - step_pts, step_pts):
            sx0, sy0 = self._meter_to_screen(self.interp_east[i], self.interp_north[i])
            sx1, sy1 = self._meter_to_screen(self.interp_east[i + step_pts], self.interp_north[i + step_pts])
            painter.drawLine(QPointF(sx0, sy0), QPointF(sx1, sy1))

        # 시작/끝 마커
        sx0, sy0 = self._meter_to_screen(self.interp_east[0], self.interp_north[0])
        sx1, sy1 = self._meter_to_screen(self.interp_east[-1], self.interp_north[-1])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(46, 204, 113)))
        painter.drawEllipse(QPointF(sx0, sy0), 4, 4)
        painter.setBrush(QBrush(QColor(231, 76, 60)))
        painter.drawEllipse(QPointF(sx1, sy1), 4, 4)
        painter.setBrush(Qt.NoBrush)

    def _draw_hover_info(self, painter: QPainter):
        """마우스 호버 위치 상세 표시."""
        painter.setBrush(Qt.NoBrush)
        idx = self.hover_ping_idx
        e = self.interp_east[idx]
        n = self.interp_north[idx]
        hd = self.interp_hdg[idx]

        sx, sy = self._meter_to_screen(e, n)

        # 하이라이트 핑 점
        painter.setBrush(QBrush(QColor(241, 196, 15)))
        painter.setPen(QPen(Qt.black, 1.5))
        painter.drawEllipse(QPointF(sx, sy), 5, 5)

        # 헤딩 방향 화살표
        rad = math.radians(hd)
        arr_len = 28.0
        ex = sx + arr_len * math.sin(rad)
        ey = sy - arr_len * math.cos(rad)
        painter.setPen(QPen(QColor(241, 196, 15), 2.5))
        painter.drawLine(QPointF(sx, sy), QPointF(ex, ey))

        # 툴팁 박스
        raw_h = self.raw_hdg[idx] if idx < len(self.raw_hdg) else 0.0
        rp = self.range_port[idx] if idx < len(self.range_port) else self.user_ray_length
        rs = self.range_stbd[idx] if idx < len(self.range_stbd) else self.user_ray_length
        txt = f"Ping #{idx} | 보간 헤딩: {hd:.1f}° (원본: {raw_h:.1f}°) | 폭: L {rp:.0f}m / R {rs:.0f}m"

        painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        painter.setPen(Qt.white)
        painter.setBrush(QBrush(QColor(30, 30, 40, 220)))
        box_w = 360
        painter.drawRoundedRect(int(sx + 10), int(sy - 32), box_w, 24, 4, 4)
        painter.drawText(int(sx + 16), int(sy - 16), txt)
        painter.setBrush(Qt.NoBrush)

    def _draw_legend(self, painter: QPainter):
        """우측 상단 범례 표시."""
        painter.setFont(QFont("Malgun Gothic", 9))
        rect = QRectF(self.width() - 245, 12, 233, 90)
        painter.setBrush(QBrush(QColor(25, 25, 30, 230)))
        painter.setPen(QPen(QColor(80, 80, 90), 1))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setBrush(Qt.NoBrush)

        # 원본 범례 (적색 1초 간격 듬성듬성)
        painter.setPen(QPen(QColor(235, 77, 75), 2, Qt.DashLine))
        painter.drawLine(int(rect.x() + 12), int(rect.y() + 20), int(rect.x() + 36), int(rect.y() + 20))
        painter.setBrush(QBrush(QColor(235, 77, 75)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(rect.x() + 24, rect.y() + 20), 3, 3)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(Qt.white)
        painter.drawText(int(rect.x() + 44), int(rect.y() + 24), "보간 전: 1초 간격 듬성듬성 (적색)")

        # 보간 범례 (청록색 매 핑 촘촘·유려함)
        painter.setPen(QPen(QColor(26, 188, 156), 2))
        painter.drawLine(int(rect.x() + 12), int(rect.y() + 46), int(rect.x() + 36), int(rect.y() + 46))
        painter.setPen(Qt.white)
        painter.drawText(int(rect.x() + 44), int(rect.y() + 50), "보간 후: 매 핑 촘촘·유려함 (청록)")

        # 조작 안내
        painter.setFont(QFont("Malgun Gothic", 8))
        painter.setPen(QColor(160, 160, 170))
        painter.drawText(int(rect.x() + 12), int(rect.y() + 75), "휠=줌 · 드래그=패닝 · 호버=상세")


# ==============================================================================
# 2. 헤딩 각도 시계열 그래프 (HeadingGraphView)
# ==============================================================================

class HeadingGraphView(QWidget):
    """핑 번호에 따른 원본 헤딩(계단식/점핑) vs 보간된 헤딩(매끄러운 곡선) 시계열 비교 그래프."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(130)
        self.setStyleSheet("background-color: #16161a; border-radius: 4px;")
        self.raw_hdg = np.zeros(0)
        self.interp_hdg = np.zeros(0)
        self.raw_nodes = np.zeros(0, dtype=int)

    def set_headings(self, raw_hdg, interp_hdg, raw_nodes=None):
        self.raw_hdg = np.asarray(raw_hdg, dtype=np.float64)
        self.interp_hdg = np.asarray(interp_hdg, dtype=np.float64)
        if raw_nodes is not None:
            self.raw_nodes = np.asarray(raw_nodes, dtype=int)
        else:
            self.raw_nodes = np.zeros(0, dtype=int)
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#16161a"))  # ★확실한 다크 배경 채우기★
        painter.setBrush(Qt.NoBrush)

        w = self.width()
        h = self.height()
        n = max(len(self.raw_hdg), len(self.interp_hdg))
        if n < 2:
            return

        margin_l, margin_r, margin_t, margin_b = 40, 15, 12, 22
        pw = w - margin_l - margin_r
        ph = h - margin_t - margin_b

        # 0°, 90°, 180°, 270°, 360° 눈금 및 가이드라인
        painter.setFont(QFont("Malgun Gothic", 8))
        for deg in (0, 90, 180, 270, 360):
            y = int((h - margin_b) - (deg / 360.0) * ph)
            painter.setPen(QPen(QColor(45, 45, 55), 1, Qt.DotLine))
            painter.drawLine(margin_l, y, w - margin_r, y)
            painter.setPen(QColor(140, 140, 150))
            painter.drawText(4, y + 4, f"{deg}°")

        # 축선
        painter.setPen(QPen(QColor(80, 80, 90), 1))
        painter.drawLine(margin_l, margin_t, margin_l, h - margin_b)
        painter.drawLine(margin_l, h - margin_b, w - margin_r, h - margin_b)

        # 1. 보간 전 원본 헤딩 (적색 계단선, 두께 2.2px + 1초 노드 마커)
        if len(self.raw_hdg) > 1:
            raw_pen = QPen(QColor(255, 71, 87, 240), 2.2)  # 선명한 산호 적색
            painter.setPen(raw_pen)
            step_x = pw / float(len(self.raw_hdg) - 1)

            step_stride = max(1, len(self.raw_hdg) // 1500)
            prev_x = margin_l
            prev_y = (h - margin_b) - (self.raw_hdg[0] % 360.0 / 360.0) * ph

            for i in range(step_stride, len(self.raw_hdg), step_stride):
                curr_x = margin_l + i * step_x
                curr_y = (h - margin_b) - (self.raw_hdg[i] % 360.0 / 360.0) * ph
                if abs(curr_y - prev_y) < ph * 0.7:
                    # 계단식: 수평선 후 수직 점프
                    painter.drawLine(QPointF(prev_x, prev_y), QPointF(curr_x, prev_y))
                    if abs(curr_y - prev_y) > 0.5:
                        painter.drawLine(QPointF(curr_x, prev_y), QPointF(curr_x, curr_y))
                prev_x, prev_y = curr_x, curr_y

            # 1초 갱신 노드 위치에 빨간 마커 점 표시
            if len(self.raw_nodes) > 1:
                painter.setBrush(QBrush(QColor(255, 71, 87, 230)))
                painter.setPen(Qt.NoPen)
                dot_step = max(1, len(self.raw_nodes) // 80)
                for n_idx in self.raw_nodes[::dot_step]:
                    if n_idx < len(self.raw_hdg):
                        nx = margin_l + n_idx * step_x
                        ny = (h - margin_b) - (self.raw_hdg[n_idx] % 360.0 / 360.0) * ph
                        painter.drawEllipse(QPointF(nx, ny), 2.5, 2.5)
                painter.setBrush(Qt.NoBrush)

        # 2. 보간 후 헤딩 (청록색 매끄러운 곡선, 두께 2.0px)
        if len(self.interp_hdg) > 1:
            int_pen = QPen(QColor(0, 210, 211, 255), 2.0)  # 눈에 띄는 선명한 청록색
            painter.setPen(int_pen)
            step_x = pw / float(len(self.interp_hdg) - 1)

            step_stride = max(1, len(self.interp_hdg) // 1500)
            prev_x = margin_l
            prev_y = (h - margin_b) - (self.interp_hdg[0] % 360.0 / 360.0) * ph

            for i in range(step_stride, len(self.interp_hdg), step_stride):
                curr_x = margin_l + i * step_x
                curr_y = (h - margin_b) - (self.interp_hdg[i] % 360.0 / 360.0) * ph
                if abs(curr_y - prev_y) < ph * 0.7:
                    painter.drawLine(QPointF(prev_x, prev_y), QPointF(curr_x, curr_y))
                prev_x, prev_y = curr_x, curr_y

        # 범례 표시 (시계열 좌상단 깔끔한 배경 박스)
        legend_rect = QRectF(margin_l + 8, margin_t + 4, 490, 22)
        painter.setBrush(QBrush(QColor(22, 22, 26, 210)))
        painter.setPen(QPen(QColor(60, 60, 70), 1))
        painter.drawRoundedRect(legend_rect, 4, 4)
        painter.setBrush(Qt.NoBrush)

        painter.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        painter.setPen(QColor(255, 71, 87))
        painter.drawText(int(legend_rect.x() + 10), int(legend_rect.y() + 15), "──● 보간 전: 1초 단위 계단식 점핑 (적색)")
        painter.setPen(QColor(0, 210, 211))
        painter.drawText(int(legend_rect.x() + 250), int(legend_rect.y() + 15), "──── 보간 후: 매 핑 유려한 스무딩 곡선 (청록색)")


# ==============================================================================
# 3. 메인 튜너 대화상자 (NavHeadingTunerDialog)
# ==============================================================================

class NavHeadingTunerDialog(QDialog):
    """생선 가시 시각화 및 항법/헤딩 실시간 파라미터 튜닝 대화상자."""

    def __init__(self, parent=None, files=None, cur_idx=0):
        super().__init__(parent)
        self.setWindowTitle("항법 및 헤딩 튜너 — 생선 가시(Fishbone) 시각화")
        self.resize(1180, 800)

        self.files = files or []
        self.cur_idx = max(0, min(cur_idx, len(self.files) - 1)) if self.files else -1
        self.current_results = None
        self.current_stats = None

        self._build_ui()
        self._load_and_compute_initial()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # 좌측 제어 패널
        left_panel = QWidget()
        left_panel.setFixedWidth(340)
        vbox = QVBoxLayout(left_panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        # 1. 파일 정보
        grp_file = QGroupBox("파일 정보")
        f_lay = QGridLayout(grp_file)
        self.lbl_fname = QLabel("-")
        self.lbl_fname.setStyleSheet("font-weight: bold; color: #3498db;")
        self.lbl_pings = QLabel("-")
        self.lbl_rate = QLabel("-")
        self.lbl_file_swath = QLabel("-")
        self.lbl_file_swath.setStyleSheet("font-weight: bold; color: #e67e22;")

        f_lay.addWidget(QLabel("대상 파일:"), 0, 0)
        f_lay.addWidget(self.lbl_fname, 0, 1)
        f_lay.addWidget(QLabel("총 소나 핑:"), 1, 0)
        f_lay.addWidget(self.lbl_pings, 1, 1)
        f_lay.addWidget(QLabel("Ping Rate:"), 2, 0)
        f_lay.addWidget(self.lbl_rate, 2, 1)
        f_lay.addWidget(QLabel("파일 Swath 폭:"), 3, 0)
        f_lay.addWidget(self.lbl_file_swath, 3, 1)
        vbox.addWidget(grp_file)

        # 2. 헤딩 및 스무딩 파라미터
        grp_params = QGroupBox("실시간 보정 파라미터")
        p_lay = QGridLayout(grp_params)
        p_lay.setSpacing(8)

        self.cmb_hdg_mode = QComboBox()
        self.cmb_hdg_mode.addItem("GPS Course (진행방향 스무딩, 추천)", "cog")
        self.cmb_hdg_mode.addItem("센서 헤딩(SensorHeading) 스무딩", "sensor")
        self.cmb_hdg_mode.currentIndexChanged.connect(self._recompute_and_render)
        p_lay.addWidget(QLabel("헤딩 모드:"), 0, 0, 1, 2)
        p_lay.addWidget(self.cmb_hdg_mode, 1, 0, 1, 2)

        p_lay.addWidget(QLabel("위치 스무딩:"), 2, 0)
        self.spn_pos_smooth = QDoubleSpinBox()
        self.spn_pos_smooth.setRange(0.5, 30.0)
        self.spn_pos_smooth.setValue(3.0)
        self.spn_pos_smooth.setSingleStep(0.5)
        self.spn_pos_smooth.setSuffix(" s")
        self.spn_pos_smooth.valueChanged.connect(self._recompute_and_render)
        p_lay.addWidget(self.spn_pos_smooth, 2, 1)

        p_lay.addWidget(QLabel("헤딩 스무딩:"), 3, 0)
        self.spn_hdg_smooth = QDoubleSpinBox()
        self.spn_hdg_smooth.setRange(0.5, 30.0)
        self.spn_hdg_smooth.setValue(5.0)
        self.spn_hdg_smooth.setSingleStep(0.5)
        self.spn_hdg_smooth.setSuffix(" s")
        self.spn_hdg_smooth.valueChanged.connect(self._recompute_and_render)
        p_lay.addWidget(self.spn_hdg_smooth, 3, 1)

        vbox.addWidget(grp_params)

        # 3. 생선 가시 뷰 제어 (파일 Slant Range 연동)
        grp_view = QGroupBox("생선 가시(Fishbone) 표시 설정")
        v_lay = QGridLayout(grp_view)
        v_lay.setSpacing(8)

        v_lay.addWidget(QLabel("가시 길이 (Swath):"), 0, 0)
        self.spn_ray_len = QSpinBox()
        self.spn_ray_len.setRange(10, 500)
        self.spn_ray_len.setValue(80)  # 파일 기본값으로 초기화됨
        self.spn_ray_len.setSuffix(" m")
        self.spn_ray_len.valueChanged.connect(self._on_view_param_changed)
        v_lay.addWidget(self.spn_ray_len, 0, 1)

        v_lay.addWidget(QLabel("가시 간격:"), 1, 0)
        self.cmb_stride = QComboBox()
        self.cmb_stride.addItem("매 1 핑 (초고밀도)", 1)
        self.cmb_stride.addItem("매 2 핑", 2)
        self.cmb_stride.addItem("매 5 핑 (표준)", 5)
        self.cmb_stride.addItem("매 10 핑", 10)
        self.cmb_stride.addItem("매 20 핑", 20)
        self.cmb_stride.setCurrentIndex(2)
        self.cmb_stride.currentIndexChanged.connect(self._on_view_param_changed)
        v_lay.addWidget(self.cmb_stride, 1, 1)

        self.cmb_view_mode = QComboBox()
        self.cmb_view_mode.addItem("비교 모드 (보간 전 적색 듬성듬성 + 보간 후 청록 촘촘)", "compare")
        self.cmb_view_mode.addItem("보간 결과만 보기 (촘촘하고 유려한 청록색 가시)", "interp")
        self.cmb_view_mode.addItem("보간 전 원본만 보기 (1초 간격 듬성듬성한 적색 가시)", "raw")
        self.cmb_view_mode.currentIndexChanged.connect(self._on_view_param_changed)
        v_lay.addWidget(QLabel("표시 모드:"), 2, 0, 1, 2)
        v_lay.addWidget(self.cmb_view_mode, 3, 0, 1, 2)

        self.chk_anti_overlap = QCheckBox("화면 배율 연동 겹침 방지 (권장)")
        self.chk_anti_overlap.setChecked(True)
        self.chk_anti_overlap.setToolTip("줌아웃 시 가시가 서로 겹쳐 붉은 면처럼 뭉치는 현상을 방지하고, 항상 분리된 가시로 표시합니다.")
        self.chk_anti_overlap.toggled.connect(self._on_view_param_changed)
        v_lay.addWidget(self.chk_anti_overlap, 4, 0, 1, 2)

        btn_fit = QPushButton("화면 맞춤 (Fit All)")
        btn_fit.clicked.connect(lambda: self.canvas.fit_view())
        v_lay.addWidget(btn_fit, 5, 0, 1, 2)

        vbox.addWidget(grp_view)

        # 4. 분석 결과 브리핑
        grp_stat = QGroupBox("헤딩 변화율 분석")
        s_lay = QVBoxLayout(grp_stat)
        self.lbl_hdg_stat = QLabel("계산 중...")
        self.lbl_hdg_stat.setWordWrap(True)
        self.lbl_hdg_stat.setStyleSheet("font-size: 11px; color: #2ecc71;")
        s_lay.addWidget(self.lbl_hdg_stat)
        vbox.addWidget(grp_stat)

        vbox.addStretch(1)

        # 5. 적용 및 닫기 버튼
        self.btn_apply = QPushButton("메모리에 즉시 적용 (Apply)")
        self.btn_apply.setDefault(True)
        self.btn_apply.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 10px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_apply.clicked.connect(self._on_apply_clicked)

        self.btn_cancel = QPushButton("닫기 / 취소")
        self.btn_cancel.clicked.connect(self.reject)

        vbox.addWidget(self.btn_apply)
        vbox.addWidget(self.btn_cancel)

        root.addWidget(left_panel)

        # 우측 시각화 영역 (캔버스 + 하단 헤딩 그래프)
        right_panel = QWidget()
        rlay = QVBoxLayout(right_panel)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(8)

        self.canvas = FishboneCanvas(self)
        self.graph = HeadingGraphView(self)

        rlay.addWidget(self.canvas, 1)
        rlay.addWidget(self.graph)

        root.addWidget(right_panel, 1)

    # ------------------------------------------------ 데이터 처리 및 렌더링
    def _load_and_compute_initial(self):
        if not self.files or self.cur_idx < 0:
            self.lbl_fname.setText("열린 파일 없음")
            self.btn_apply.setEnabled(False)
            return

        f = self.files[self.cur_idx]
        self.lbl_fname.setText(os.path.basename(f["path"]))
        self.lbl_pings.setText(f"{len(f['meta']):,} pings")

        self._recompute_and_render(initial=True)

    def _recompute_and_render(self, initial=False):
        """파라미터 변경 시 실시간으로 보간을 재계산하고 캔버스와 그래프를 갱신."""
        if not self.files or self.cur_idx < 0:
            return

        f = self.files[self.cur_idx]
        meta = f["meta"]

        hdg_mode = self.cmb_hdg_mode.currentData()
        pos_sec = float(self.spn_pos_smooth.value())
        hdg_sec = float(self.spn_hdg_smooth.value())

        results, stats = process_navigation(
            meta,
            spike_filter=True,
            max_speed_mps=12.0,
            smooth_window_sec=pos_sec,
            heading_smooth_sec=hdg_sec,
            interp_method="hermite",
            heading_mode=hdg_mode,
        )

        self.current_results = results
        self.current_stats = stats

        def_sr = results.get("default_slant_range", 80.0)
        self.lbl_file_swath.setText(f"{def_sr:.1f} m (좌우 총 {def_sr*2:.1f} m)")

        if initial:
            self.spn_ray_len.blockSignals(True)
            self.spn_ray_len.setValue(int(round(def_sr)))
            self.spn_ray_len.blockSignals(False)

        self.lbl_rate.setText(f"{stats.get('avg_ping_rate', 0.0):.1f} Hz (약 {stats.get('pings_per_update', 0.0):.1f}핑/초)")

        mean_chg = stats.get("mean_hdg_change_deg", 0.0)
        max_chg = stats.get("max_hdg_change_deg", 0.0)
        self.lbl_hdg_stat.setText(
            f"• 핑당 평균 각도 변화: {mean_chg:.3f}°\n"
            f"• 핑당 최대 각도 변화: {max_chg:.2f}°\n"
            f"• 적용 모드: {stats.get('applied_heading_mode', '').upper()}\n"
            f"• 스파이크 검출 복원: {stats.get('spikes_detected', 0)}건\n"
            f"• 평균 선속: {stats.get('avg_speed_mps', 0.0):.2f} m/s ({stats.get('avg_speed_knots', 0.0):.1f} kts)"
        )

        if initial:
            self.canvas.set_data(
                results["raw_east"], results["raw_north"], results["raw_headings"],
                results.get("raw_node_idx"),
                results["east"], results["north"], results["headings"],
                results.get("range_port"), results.get("range_stbd"),
                def_sr
            )
        else:
            self.canvas.update_interp_data(
                results["east"], results["north"], results["headings"],
                results["raw_headings"]
            )

        # 그래프에 보간 전 거친 헤딩과 보간 후 유려한 헤딩 및 1초 노드 전달
        raw_hd_to_plot = results.get("raw_plot_headings", results["raw_headings"])
        self.graph.set_headings(raw_hd_to_plot, results["headings"], results.get("raw_node_idx"))

    def _on_view_param_changed(self):
        """가시 길이, 간격, 보기 모드, 겹침 방지 변경."""
        self.canvas.user_ray_length = float(self.spn_ray_len.value())
        self.canvas.stride = int(self.cmb_stride.currentData())
        self.canvas.view_mode = self.cmb_view_mode.currentData()
        self.canvas.anti_overlap = self.chk_anti_overlap.isChecked()
        self.canvas.update()

    # ------------------------------------------------ 메모리 적용
    def _on_apply_clicked(self):
        """보정 결과를 메모리(meta) 및 사이드카에 즉시 적용하고 대화상자 종료."""
        if not self.current_results or not self.files or self.cur_idx < 0:
            self.reject()
            return

        f = self.files[self.cur_idx]
        apply_interpolated_nav_to_meta(f["meta"], self.current_results)
        try:
            save_nav(f["path"], f["meta"])
        except Exception as e:
            print("[경고] 사이드카 저장 실패:", e)

        self.accept()
