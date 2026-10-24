
"""waterfall_view.py — XTF Waterfall Viewer v4
- QGraphicsView 기반 Waterfall (세로=핑 row, 가로=샘플 col)
- 상단 횡거리 축(_TopAxis): port 음수 / nadir 0 / stbd 양수
- 좌측 핑 번호 축(_LeftAxis)
- hover 십자선 + pingHovered(row, col)
- 좌클릭 contact 추가 → contactAdded(row, col)
- add_contact_marker / clear_contact_markers
- Bottom tracking 라인 표시 + 마우스 편집 (start/finish/cancel_bottom_edit)
"""
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (QPen, QColor, QPainter, QPainterPath, QBrush,
                           QImage, QPixmap, QFont)
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QWidget

AXIS_TOP = 22
AXIS_LEFT = 58
DEBUG_AXIS = False

# 축 눈금용 폰트 (DirectWrite 호환 Outline 폰트 지정으로 MS Sans Serif 비트맵 폴백 경고 방지)
_AXIS_FONT = QFont("Malgun Gothic", 8)
_AXIS_FONT.setStyleHint(QFont.SansSerif)
_AXIS_FONT.setFamilies(["Malgun Gothic", "Segoe UI", "Arial", "sans-serif"])


# ================================================================= 축 위젯
class _TopAxis(QWidget):
    """상단 횡거리(m) 축. port=음수, nadir=0, stbd=양수."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self.setFixedHeight(AXIS_TOP)

    def _info(self):
        meta = self._view._meta
        if not meta:
            return None
        m = meta[0]
        rp = float(m.get("range_port") or m.get("slant_range") or 0)
        rs = float(m.get("range_stbd") or m.get("slant_range") or 0)
        np_ = int(m.get("n_port") or 0)
        ns_ = int(m.get("n_stbd") or 0)
        if (rp <= 0 and rs <= 0) or np_ <= 0 or ns_ <= 0:
            return None
        rp = rp if rp > 0 else rs
        rs = rs if rs > 0 else rp
        return rp, rs, np_, ns_

    def _meter_to_col(self, v, rp, rs, np_, ns_):
        if v <= 0:
            return np_ * (1.0 + v / max(rp, 1e-4))
        return np_ + (v / max(rs, 1e-4)) * ns_

    def paintEvent(self, ev):
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            p.fillRect(self.rect(), QColor(30, 30, 30))
            info = self._info()
            if info is None:
                return
            rp, rs, np_, ns_ = info
            if DEBUG_AXIS:
                print(f"[AXIS] w={self.width()} h={self.height()} "
                      f"rp={rp} rs={rs} n_port={np_} n_stbd={ns_}")

            p.setPen(QColor(200, 200, 200))
            p.setFont(_AXIS_FONT)

            # 60px 간격 정도가 되도록 눈금 간격(m) 선택
            x0 = self._view.mapFromScene(QPointF(0, 0)).x()
            x1 = self._view.mapFromScene(QPointF(np_ + ns_, 0)).x()
            tot_range = rp + rs
            px_per_m = max(abs(x1 - x0) / max(tot_range, 1e-4), 1e-9)
            step = 1.0
            for cand in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
                if cand * px_per_m >= 55:
                    step = cand
                    break
            else:
                step = 500

            v = -(int(rp / step)) * step
            while v <= rs + 1e-6:
                col = self._meter_to_col(v, rp, rs, np_, ns_)
                x = self._view.mapFromScene(QPointF(col, 0)).x()
                if -20 <= x <= self.width() + 20:
                    p.drawLine(int(x), AXIS_TOP - 5, int(x), AXIS_TOP - 1)
                    label = "0" if abs(v) < 1e-6 else f"{v:g}"
                    p.drawText(QRectF(x - 28, 1, 56, AXIS_TOP - 7),
                               Qt.AlignCenter, label)
                v += step
            p.setPen(QColor(90, 90, 90))
            p.drawLine(0, AXIS_TOP - 1, self.width(), AXIS_TOP - 1)
        finally:
            p.end()


class _LeftAxis(QWidget):
    """좌측 핑 번호 축."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self.setFixedWidth(AXIS_LEFT)

    def paintEvent(self, ev):
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            p.fillRect(self.rect(), QColor(30, 30, 30))
            meta = self._view._meta
            if not meta:
                return
            n = len(meta)

            p.setPen(QColor(200, 200, 200))
            p.setFont(_AXIS_FONT)

            y0 = self._view.mapFromScene(QPointF(0, 0)).y()
            y1 = self._view.mapFromScene(QPointF(0, n)).y()
            px_per_row = max(abs(y1 - y0) / max(n, 1), 1e-9)
            step = 1
            for cand in (1, 2, 5, 10, 25, 50, 100, 250, 500,
                         1000, 2500, 5000):
                if cand * px_per_row >= 40:
                    step = cand
                    break
            else:
                step = 5000

            row = 0
            while row <= n:
                y = self._view.mapFromScene(QPointF(0, row)).y()
                if -12 <= y <= self.height() + 12:
                    p.drawLine(AXIS_LEFT - 5, int(y), AXIS_LEFT - 1, int(y))
                    p.drawText(QRectF(0, y - 8, AXIS_LEFT - 8, 16),
                               Qt.AlignRight | Qt.AlignVCenter, str(row))
                row += step
            p.setPen(QColor(90, 90, 90))
            p.drawLine(AXIS_LEFT - 1, 0, AXIS_LEFT - 1, self.height())
        finally:
            p.end()


# ================================================================= 메인 뷰
class WaterfallView(QGraphicsView):
    pingHovered = Signal(int, int)          # (row=ping_idx, col=sample_idx)
    contactAdded = Signal(int, int)         # (row=ping_idx, col=sample_idx)
    bottomEditFinished = Signal(object)     # 새 altitude 배열 또는 None(취소)
    bottomProgress = Signal(str)            # 진행 상황 및 가이드 메시지
    roiSelected = Signal(int, int, int, int) # (r0, r1, c0, c1) 마우스 ROI 선택 영역

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QColor(15, 15, 15))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportMargins(AXIS_LEFT, AXIS_TOP, 0, 0)

        self._img_ref = None                # QImage 버퍼 참조 유지
        self._pix_item = self._scene.addPixmap(QPixmap())
        self._pix_item.setZValue(0)
        self._meta = None

        # ---- 십자선 ----
        pen_ch = QPen(QColor(255, 255, 0, 160), 1)
        pen_ch.setCosmetic(True)
        self._ch_h = self._scene.addLine(0, 0, 0, 0, pen_ch)
        self._ch_v = self._scene.addLine(0, 0, 0, 0, pen_ch)
        for it in (self._ch_h, self._ch_v):
            it.setZValue(90)
            it.setVisible(False)

        # ---- contact 마커 ----
        self._contact_items = []

        # ---- 축 ----
        self._top_axis = _TopAxis(self)
        self._left_axis = _LeftAxis(self)
        for sb in (self.horizontalScrollBar(), self.verticalScrollBar()):
            sb.valueChanged.connect(self._top_axis.update)
            sb.valueChanged.connect(self._left_axis.update)

        # ---- bottom tracking ----
        self._init_bottom_items()

        # ---- ROI 선택 (모션 리플 분석용) ----
        self._init_roi_items()

    # ------------------------------------------------ 배치
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self._top_axis.setGeometry(AXIS_LEFT, 0, max(w - AXIS_LEFT, 0),
                                   AXIS_TOP)
        self._left_axis.setGeometry(0, AXIS_TOP, AXIS_LEFT,
                                    max(h - AXIS_TOP, 0))

    def _refresh_axes(self):
        self._top_axis.update()
        self._left_axis.update()

    # ------------------------------------------------ 이미지
    def set_image(self, img, meta):
        """img: 2D uint8 (rows=pings, cols=samples), meta: list[dict]"""
        img = np.ascontiguousarray(img, dtype=np.uint8)
        h, w = img.shape
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        self._img_ref = img                 # GC 방지
        self._pix_item.setPixmap(QPixmap.fromImage(qimg))
        self._scene.setSceneRect(0, 0, w, h)

        self._meta = meta
        self.cancel_bottom_edit(silent=True)
        self.update_bottom_line()
        self._refresh_axes()

    def fit_width(self):
        r = self._scene.sceneRect()
        if r.width() > 0:
            self.resetTransform()
            vw = self.viewport().width()
            self.scale(vw / r.width(), 1.0)
            self._refresh_axes()

    # ------------------------------------------------ contact 마커
    def add_contact_marker(self, row, col, label):
        pen = QPen(QColor(0, 200, 255), 1)
        pen.setCosmetic(True)
        it = self._scene.addEllipse(col - 5, row - 5, 10, 10, pen)
        it.setZValue(80)
        it.setFlag(it.GraphicsItemFlag.ItemIgnoresTransformations)
        it.setTransformOriginPoint(col, row)
        txt = self._scene.addSimpleText(label)
        txt.setBrush(QBrush(QColor(0, 200, 255)))
        txt.setPos(col + 6, row - 6)
        txt.setZValue(80)
        txt.setFlag(txt.GraphicsItemFlag.ItemIgnoresTransformations)
        self._contact_items.extend([it, txt])

    def clear_contact_markers(self):
        for it in self._contact_items:
            self._scene.removeItem(it)
        self._contact_items.clear()

    # ================================================ ROI 선택 (모션 리플 분석용)
    def _init_roi_items(self):
        self._roi_mode = False
        self._roi_start_pos = None
        self._roi_rect_item = None
        self._roi_pen = QPen(QColor(0, 229, 255, 230), 1.5, Qt.DashLine)
        self._roi_pen.setCosmetic(True)
        self._roi_brush = QBrush(QColor(0, 229, 255, 30))

    def set_roi_mode(self, on: bool):
        """마우스 ROI 드래그 선택 모드 켜기/끄기."""
        self._roi_mode = bool(on)
        self._roi_start_pos = None
        if self._roi_mode:
            self.viewport().setCursor(Qt.CrossCursor)
            self.bottomProgress.emit("리플 패턴 영역을 마우스 좌클릭으로 드래그하여 지정하세요.")
        else:
            self.viewport().unsetCursor()

    def clear_roi(self):
        """현재 표시된 ROI 사각형을 씬에서 제거."""
        if self._roi_rect_item is not None:
            self._scene.removeItem(self._roi_rect_item)
            self._roi_rect_item = None

    def set_roi_rect(self, r0: int, r1: int, c0: int, c1: int):
        """특정 핑/열 좌표로 ROI 사각형 표시."""
        self.clear_roi()
        r = QRectF(c0, r0, max(1, c1 - c0), max(1, r1 - r0))
        self._roi_rect_item = self._scene.addRect(r, self._roi_pen, self._roi_brush)
        self._roi_rect_item.setZValue(75)

    # ================================================ bottom tracking
    def _init_bottom_items(self):
        self._bottom_edit = False
        self._bt_edit_mode = "auto"         # "auto" (시드 자동 추적) 또는 "manual" (구간 수동 보정)
        self._buffer_m = 3.5                # 이전 핑 대비 탐색 허용 버퍼 폭 (m)
        self._max_step_m = 0.4              # 핑당 최대 고도 변화량 (m)
        self._initial_alts = None           # 편집 시작 전 원본 고도 백업
        self._current_alts = None           # 현재 추적/편집 중인 고도 배열 (N,)
        self._seed_pts = []                 # [(row, altitude_m)] 시드 포인트
        self._ctrl_pts = []                 # [(row, altitude_m)] 수동 보정 제어점
        self._marker_items = []

        pen_bt = QPen(QColor(0, 230, 118, 220), 1.5)
        pen_bt.setCosmetic(True)
        pen_pv = QPen(QColor(255, 215, 0, 240), 1.8)
        pen_pv.setCosmetic(True)

        self._bt_port = self._scene.addPath(QPainterPath(), pen_bt)
        self._bt_stbd = self._scene.addPath(QPainterPath(), pen_bt)
        self._pv_port = self._scene.addPath(QPainterPath(), pen_pv)
        self._pv_stbd = self._scene.addPath(QPainterPath(), pen_pv)
        for it in (self._bt_port, self._bt_stbd):
            it.setZValue(50)
        for it in (self._pv_port, self._pv_stbd):
            it.setZValue(55)
            it.setVisible(False)

    def _bt_cols(self, m, alt):
        if alt is None or alt <= 0:
            return None
        rp = float(m.get("range_port") or m.get("slant_range") or 0)
        rs = float(m.get("range_stbd") or m.get("slant_range") or 0)
        np_, ns_ = int(m.get("n_port") or 0), int(m.get("n_stbd") or 0)
        if (rp <= 0 and rs <= 0) or np_ <= 0 or ns_ <= 0:
            return None
        rp = rp if rp > 0 else rs
        rs = rs if rs > 0 else rp
        frac_p = min(alt / rp, 1.0)
        frac_s = min(alt / rs, 1.0)
        return (np_ - frac_p * np_, np_ + frac_s * ns_)

    def _col_to_alt(self, m, col):
        rp = float(m.get("range_port") or m.get("slant_range") or 0)
        rs = float(m.get("range_stbd") or m.get("slant_range") or 0)
        np_, ns_ = int(m.get("n_port") or 0), int(m.get("n_stbd") or 0)
        if (rp <= 0 and rs <= 0) or np_ <= 0 or ns_ <= 0:
            return None
        rp = rp if rp > 0 else rs
        rs = rs if rs > 0 else rp
        if col <= np_:
            frac = (np_ - col) / float(max(np_, 1))
            return max(0.0, min(frac, 1.0)) * rp
        else:
            frac = (col - np_) / float(max(ns_, 1))
            return max(0.0, min(frac, 1.0)) * rs

    def _build_paths(self, alt_arr):
        pp, ps = QPainterPath(), QPainterPath()
        if alt_arr is None or len(alt_arr) == 0:
            return pp, ps
        started = False
        for row, m in enumerate(self._meta):
            if row >= len(alt_arr):
                break
            cols = self._bt_cols(m, alt_arr[row])
            if cols is None:
                started = False
                continue
            y = row + 0.5
            if not started:
                pp.moveTo(cols[0], y)
                ps.moveTo(cols[1], y)
                started = True
            else:
                pp.lineTo(cols[0], y)
                ps.lineTo(cols[1], y)
        return pp, ps

    def update_bottom_line(self, altitudes=None):
        if not self._meta:
            self._bt_port.setPath(QPainterPath())
            self._bt_stbd.setPath(QPainterPath())
            return
        if altitudes is None:
            altitudes = [m.get("altitude") for m in self._meta]
        pp, ps = self._build_paths(altitudes)
        self._bt_port.setPath(pp)
        self._bt_stbd.setPath(ps)

    def set_bottom_visible(self, on):
        self._bt_port.setVisible(on)
        self._bt_stbd.setVisible(on)

    # ---------- 편집 파라미터 및 모드 제어 ----------
    def set_edit_mode(self, mode: str):
        """'auto' (시드 자동 추적) 또는 'manual' (구간 수동 보정)."""
        self._bt_edit_mode = str(mode).lower()
        if self._bt_edit_mode == "auto":
            self.bottomProgress.emit(
                f"시드 자동 추적 모드: First Bottom 지점을 클릭하세요. (버퍼: ±{self._buffer_m:.1f}m)"
            )
        else:
            self.bottomProgress.emit(
                "구간 수동 보정 모드: 시작점과 끝점을 클릭하면 선형 보정됩니다."
            )

    def set_buffer_m(self, buf: float):
        """자동 추적 버퍼(m) 변경."""
        self._buffer_m = max(0.2, float(buf))
        if getattr(self, "_bottom_edit", False) and self._seed_pts:
            self._run_auto_track()
            self.bottomProgress.emit(
                f"탐색 버퍼 변경 (±{self._buffer_m:.1f}m) → 자동 재추적 완료"
            )

    def smooth_current_bottom(self, k=5):
        """현재 고도 곡선에 미세 지터 제거(중앙값 필터)를 적용."""
        if not getattr(self, "_bottom_edit", False) or self._current_alts is None:
            return
        try:
            from bottom_loader import median_filter1d
            self._current_alts = median_filter1d(self._current_alts, k=k).astype(np.float32)
            self._reapply_manual_overrides()
            self._update_preview()
            self.bottomProgress.emit(f"고도 데이터 평활화(k={k}) 적용 완료")
        except Exception as e:
            self.bottomProgress.emit(f"평활화 오류: {e}")

    def reset_bottom(self):
        """현재 편집 내용을 초기화하고 시작 시점으로 되돌림."""
        if not getattr(self, "_bottom_edit", False):
            return
        self._seed_pts.clear()
        self._ctrl_pts.clear()
        self._clear_markers()
        if self._initial_alts is not None and np.any(self._initial_alts >= 0.5):
            self._current_alts = self._initial_alts.copy()
        else:
            self._current_alts = None
        self._update_preview()
        self.bottomProgress.emit("바탐 편집 초기화됨. 시드 지점을 새로 클릭하세요.")

    def _reapply_manual_overrides(self):
        """수동 제어점 구간을 현재 고도 배열에 반영."""
        if self._current_alts is None or len(self._ctrl_pts) < 1:
            return
        pts = sorted(self._ctrl_pts, key=lambda x: x[0])
        if len(pts) >= 2:
            for i in range(len(pts) - 1):
                r0, a0 = pts[i]
                r1, a1 = pts[i + 1]
                if r1 > r0:
                    self._current_alts[r0:r1 + 1] = np.interp(
                        np.arange(r0, r1 + 1), [r0, r1], [a0, a1]
                    )
        elif len(pts) == 1:
            r, a = pts[0]
            self._current_alts[r] = a

    def _run_auto_track(self):
        """등록된 시드 포인트들을 기반으로 전체 핑 자동 추적 수행."""
        if not self._seed_pts or not self._meta:
            return
        try:
            from bottom_loader import track_bottom_seeded
            self._current_alts = track_bottom_seeded(
                self._img_ref, self._meta, self._seed_pts,
                buffer_m=self._buffer_m, max_step_m=self._max_step_m
            )
            self._reapply_manual_overrides()
            self._rebuild_markers()
            self._update_preview()
        except Exception as e:
            self.bottomProgress.emit(f"자동 추적 오류: {e}")

    # ---------- 편집 워크플로우 ----------
    def start_bottom_edit(self):
        if not self._meta:
            return False
        self._bottom_edit = True
        self._bt_edit_mode = "auto"
        self._seed_pts.clear()
        self._ctrl_pts.clear()
        self._clear_markers()

        # 기존 고도 확인
        self._initial_alts = np.array(
            [float(m.get("altitude") or 0.0) for m in self._meta], dtype=np.float32
        )
        if np.any(self._initial_alts >= 0.5):
            self._current_alts = self._initial_alts.copy()
        else:
            self._current_alts = None

        self._pv_port.setVisible(True)
        self._pv_stbd.setVisible(True)
        self._update_preview()
        self.viewport().setCursor(Qt.CrossCursor)

        valid_cnt = int(np.count_nonzero(self._initial_alts >= 0.5)) if self._initial_alts is not None else 0
        if valid_cnt > 0:
            self.bottomProgress.emit(
                f"바탐 편집 활성화: 기존 고도({valid_cnt}개 핑) 확인됨. 시드를 클릭하면 자동 재추적됩니다."
            )
        else:
            self.bottomProgress.emit(
                "바탐 편집 활성화: 영상에서 해저면(First Bottom) 지점을 마우스 좌클릭하세요."
            )
        return True

    def finish_bottom_edit(self):
        if not self._bottom_edit:
            return
        final_alt = self._current_alts.copy() if self._current_alts is not None else None
        self._exit_edit()
        if final_alt is not None:
            self.update_bottom_line(final_alt)
        self.bottomEditFinished.emit(final_alt)

    def cancel_bottom_edit(self, silent=False):
        if not getattr(self, "_bottom_edit", False):
            return
        self._exit_edit()
        if not silent:
            self.bottomEditFinished.emit(None)

    def _exit_edit(self):
        self._bottom_edit = False
        self._seed_pts.clear()
        self._ctrl_pts.clear()
        self._clear_markers()
        self._pv_port.setPath(QPainterPath())
        self._pv_stbd.setPath(QPainterPath())
        self._pv_port.setVisible(False)
        self._pv_stbd.setVisible(False)
        self.viewport().unsetCursor()

    def _clear_markers(self):
        for it in self._marker_items:
            self._scene.removeItem(it)
        self._marker_items.clear()

    def _update_preview(self):
        if self._current_alts is not None:
            pp, ps = self._build_paths(self._current_alts)
            self._pv_port.setPath(pp)
            self._pv_stbd.setPath(ps)
        else:
            self._pv_port.setPath(QPainterPath())
            self._pv_stbd.setPath(QPainterPath())

    def _rebuild_markers(self):
        self._clear_markers()
        if not self._meta:
            return

        # 1. 자동 추적 시드 포인트 (골든 옐로우 / 시안 테두리 원)
        pen_seed = QPen(QColor(0, 220, 255), 1.5)
        pen_seed.setCosmetic(True)
        brush_seed = QBrush(QColor(255, 215, 0, 230))

        for row, alt in self._seed_pts:
            if row < 0 or row >= len(self._meta):
                continue
            cols = self._bt_cols(self._meta[row], alt)
            if cols is None:
                continue
            for c in cols:
                it = self._scene.addEllipse(c - 4, row - 4, 8, 8, pen_seed, brush_seed)
                it.setZValue(65)
                it.setFlag(it.GraphicsItemFlag.ItemIgnoresTransformations)
                self._marker_items.append(it)

        # 2. 수동 보정 제어점 (화이트 테두리 / 오렌지-레드 사각)
        pen_ctrl = QPen(QColor(255, 255, 255), 1.5)
        pen_ctrl.setCosmetic(True)
        brush_ctrl = QBrush(QColor(255, 75, 75, 230))

        for row, alt in self._ctrl_pts:
            if row < 0 or row >= len(self._meta):
                continue
            cols = self._bt_cols(self._meta[row], alt)
            if cols is None:
                continue
            for c in cols:
                it = self._scene.addRect(c - 3.5, row - 3.5, 7, 7, pen_ctrl, brush_ctrl)
                it.setZValue(66)
                it.setFlag(it.GraphicsItemFlag.ItemIgnoresTransformations)
                self._marker_items.append(it)

    # ================================================ 마우스 / 키
    def mouseMoveEvent(self, ev):
        # ---- ROI 선택 모드 드래그 ----
        if getattr(self, "_roi_mode", False) and getattr(self, "_roi_start_pos", None) is not None:
            pos = self.mapToScene(ev.position().toPoint())
            rect = QRectF(self._roi_start_pos, pos).normalized()
            if self._roi_rect_item is not None:
                self._roi_rect_item.setRect(rect)
            ev.accept()
            return

        if self._meta:
            pos = self.mapToScene(ev.position().toPoint())
            row, col = int(pos.y()), int(pos.x())
            r = self._scene.sceneRect()
            if 0 <= row < len(self._meta) and 0 <= col < int(r.width()):
                self._ch_h.setLine(r.left(), pos.y(), r.right(), pos.y())
                self._ch_v.setLine(pos.x(), r.top(), pos.x(), r.bottom())
                self._ch_h.setVisible(True)
                self._ch_v.setVisible(True)
                self.pingHovered.emit(row, col)
            else:
                self._ch_h.setVisible(False)
                self._ch_v.setVisible(False)
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        self._ch_h.setVisible(False)
        self._ch_v.setVisible(False)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        # ---- ROI 드래그 선택 모드 ----
        if getattr(self, "_roi_mode", False) and self._meta:
            if ev.button() == Qt.LeftButton:
                pos = self.mapToScene(ev.position().toPoint())
                self._roi_start_pos = pos
                self.clear_roi()
                self._roi_rect_item = self._scene.addRect(QRectF(pos, pos), self._roi_pen, self._roi_brush)
                self._roi_rect_item.setZValue(75)
                ev.accept()
                return
            elif ev.button() == Qt.RightButton:
                self.clear_roi()
                self._roi_start_pos = None
                self.set_roi_mode(False)
                ev.accept()
                return

        # ---- 바탐 편집 모드 ----
        if getattr(self, "_bottom_edit", False) and self._meta:
            pos = self.mapToScene(ev.position().toPoint())
            row = int(pos.y())
            if 0 <= row < len(self._meta):
                if ev.button() == Qt.LeftButton:
                    alt = self._col_to_alt(self._meta[row], pos.x())
                    if alt is not None and alt >= 0.3:
                        if self._bt_edit_mode == "auto":
                            # 시드 포인트 추가 / 갱신
                            self._seed_pts = [p for p in self._seed_pts if abs(p[0] - row) > 10]
                            self._seed_pts.append((row, alt))
                            self._seed_pts.sort(key=lambda x: x[0])
                            self._run_auto_track()
                            self.bottomProgress.emit(
                                f"자동 추적 완료: {len(self._meta)}개 핑 "
                                f"(시드 {len(self._seed_pts)}개, 버퍼 ±{self._buffer_m:.1f}m)"
                            )
                        else:
                            # 수동 보정 제어점 추가
                            self._ctrl_pts = [p for p in self._ctrl_pts if abs(p[0] - row) > 5]
                            self._ctrl_pts.append((row, alt))
                            self._ctrl_pts.sort(key=lambda x: x[0])
                            if self._current_alts is None:
                                self._current_alts = np.full(len(self._meta), alt, dtype=np.float32)
                            self._reapply_manual_overrides()
                            self._rebuild_markers()
                            self._update_preview()
                            self.bottomProgress.emit(
                                f"수동 제어점 추가됨 (총 {len(self._ctrl_pts)}개 포인트)"
                            )
                elif ev.button() == Qt.RightButton:
                    if self._bt_edit_mode == "auto" and self._seed_pts:
                        i = min(range(len(self._seed_pts)),
                                key=lambda k: abs(self._seed_pts[k][0] - row))
                        self._seed_pts.pop(i)
                        if self._seed_pts:
                            self._run_auto_track()
                        else:
                            if self._initial_alts is not None and np.any(self._initial_alts >= 0.5):
                                self._current_alts = self._initial_alts.copy()
                            else:
                                self._current_alts = None
                            self._rebuild_markers()
                            self._update_preview()
                        self.bottomProgress.emit("시드 포인트 제거됨")
                    elif self._bt_edit_mode == "manual" and self._ctrl_pts:
                        i = min(range(len(self._ctrl_pts)),
                                key=lambda k: abs(self._ctrl_pts[k][0] - row))
                        self._ctrl_pts.pop(i)
                        if self._seed_pts:
                            self._run_auto_track()
                        else:
                            if self._initial_alts is not None and np.any(self._initial_alts >= 0.5):
                                self._current_alts = self._initial_alts.copy()
                            self._reapply_manual_overrides()
                            self._rebuild_markers()
                            self._update_preview()
                        self.bottomProgress.emit("수동 제어점 제거됨")
            ev.accept()
            return

        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        # ---- ROI 드래그 선택 완료 ----
        if getattr(self, "_roi_mode", False) and getattr(self, "_roi_start_pos", None) is not None:
            pos = self.mapToScene(ev.position().toPoint())
            rect = QRectF(self._roi_start_pos, pos).normalized()
            self._roi_start_pos = None
            if self._meta:
                n = len(self._meta)
                r0 = max(0, min(n - 1, int(rect.top())))
                r1 = max(0, min(n, int(rect.bottom())))
                c0 = max(0, int(rect.left()))
                c1 = int(rect.right())
                if r1 - r0 >= 5:
                    self.roiSelected.emit(r0, r1, c0, c1)
                    self.bottomProgress.emit(f"ROI 선택됨: Ping #{r0} ~ #{r1} ({r1 - r0} 핑)")
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # 바탐 편집 또는 ROI 선택 중에는 더블클릭 무시 (제어점 오작동 방지)
        if getattr(self, "_bottom_edit", False) or getattr(self, "_roi_mode", False):
            ev.accept()
            return
        # ---- 더블클릭 = contact 추가 ----
        if ev.button() == Qt.LeftButton and self._meta:
            pos = self.mapToScene(ev.position().toPoint())
            row, col = int(pos.y()), int(pos.x())
            r = self._scene.sceneRect()
            if 0 <= row < len(self._meta) and 0 <= col < int(r.width()):
                self.contactAdded.emit(row, col)
                ev.accept()
                return
        super().mouseDoubleClickEvent(ev)
        
    def keyPressEvent(self, ev):
        if getattr(self, "_roi_mode", False) and ev.key() == Qt.Key_Escape:
            self.clear_roi()
            self.set_roi_mode(False)
            ev.accept()
            return
        if getattr(self, "_bottom_edit", False):
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.finish_bottom_edit()
                return
            if ev.key() == Qt.Key_Escape:
                self.cancel_bottom_edit()
                return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            self._refresh_axes()
            ev.accept()
            return
        super().wheelEvent(ev)
        self._refresh_axes()
