
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
        sr = float(m.get("slant_range") or 0)
        np_ = int(m.get("n_port") or 0)
        ns_ = int(m.get("n_stbd") or 0)
        if sr <= 0 or np_ <= 0 or ns_ <= 0:
            return None
        return sr, np_, ns_

    def _meter_to_col(self, v, sr, np_, ns_):
        if v <= 0:
            return np_ * (1.0 + v / sr)
        return np_ + (v / sr) * ns_

    def paintEvent(self, ev):
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            p.fillRect(self.rect(), QColor(30, 30, 30))
            info = self._info()
            if info is None:
                return
            sr, np_, ns_ = info
            if DEBUG_AXIS:
                print(f"[AXIS] w={self.width()} h={self.height()} "
                      f"slant={sr} n_port={np_} n_stbd={ns_}")

            p.setPen(QColor(200, 200, 200))
            p.setFont(QFont("", 7))

            # 60px 간격 정도가 되도록 눈금 간격(m) 선택
            x0 = self._view.mapFromScene(QPointF(0, 0)).x()
            x1 = self._view.mapFromScene(QPointF(np_ + ns_, 0)).x()
            px_per_m = max(abs(x1 - x0) / (2 * sr), 1e-9)
            step = 1.0
            for cand in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
                if cand * px_per_m >= 55:
                    step = cand
                    break
            else:
                step = 500

            v = -(int(sr / step)) * step
            while v <= sr + 1e-6:
                col = self._meter_to_col(v, sr, np_, ns_)
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
            p.setFont(QFont("", 7))

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

    # ================================================ bottom tracking
    def _init_bottom_items(self):
        self._bottom_edit = False
        self._ctrl_pts = []                 # [(row, altitude_m)]
        self._ctrl_items = []
        pen_bt = QPen(QColor(0, 230, 118, 220), 1)
        pen_bt.setCosmetic(True)
        pen_pv = QPen(QColor(255, 200, 0, 230), 1)
        pen_pv.setCosmetic(True)
        pen_pv.setStyle(Qt.DashLine)
        self._bt_port = self._scene.addPath(QPainterPath(), pen_bt)
        self._bt_stbd = self._scene.addPath(QPainterPath(), pen_bt)
        self._pv_port = self._scene.addPath(QPainterPath(), pen_pv)
        self._pv_stbd = self._scene.addPath(QPainterPath(), pen_pv)
        for it in (self._bt_port, self._bt_stbd, self._pv_port, self._pv_stbd):
            it.setZValue(50)
        self._pv_port.setVisible(False)
        self._pv_stbd.setVisible(False)

    def _bt_cols(self, m, alt):
        sr = float(m.get("slant_range") or 0)
        np_, ns_ = int(m.get("n_port") or 0), int(m.get("n_stbd") or 0)
        if sr <= 0 or np_ <= 0 or ns_ <= 0 or alt is None or alt <= 0:
            return None
        frac = min(alt / sr, 1.0)
        return (np_ - frac * np_, np_ + frac * ns_)

    def _col_to_alt(self, m, col):
        sr = float(m.get("slant_range") or 0)
        np_, ns_ = int(m.get("n_port") or 0), int(m.get("n_stbd") or 0)
        if sr <= 0 or np_ <= 0 or ns_ <= 0:
            return None
        frac = (np_ - col) / np_ if col <= np_ else (col - np_) / ns_
        return max(0.0, min(frac, 1.0)) * sr

    def _build_paths(self, alt_arr):
        pp, ps = QPainterPath(), QPainterPath()
        started = False
        for row, m in enumerate(self._meta):
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

    # ---------- 편집 워크플로우 ----------
    def start_bottom_edit(self):
        if not self._meta:
            return False
        self._bottom_edit = True
        self._ctrl_pts.clear()
        self._clear_ctrl_items()
        self._pv_port.setVisible(True)
        self._pv_stbd.setVisible(True)
        self.viewport().setCursor(Qt.CrossCursor)
        return True

    def finish_bottom_edit(self):
        if not self._bottom_edit:
            return
        new_alt = self._interp_altitudes() if self._ctrl_pts else None
        self._exit_edit()
        if new_alt is not None:
            self.update_bottom_line(new_alt)
        self.bottomEditFinished.emit(new_alt)

    def cancel_bottom_edit(self, silent=False):
        if not getattr(self, "_bottom_edit", False):
            return
        self._exit_edit()
        if not silent:
            self.bottomEditFinished.emit(None)

    def _exit_edit(self):
        self._bottom_edit = False
        self._ctrl_pts.clear()
        self._clear_ctrl_items()
        self._pv_port.setPath(QPainterPath())
        self._pv_stbd.setPath(QPainterPath())
        self._pv_port.setVisible(False)
        self._pv_stbd.setVisible(False)
        self.viewport().unsetCursor()

    def _clear_ctrl_items(self):
        for it in self._ctrl_items:
            self._scene.removeItem(it)
        self._ctrl_items.clear()

    def _interp_altitudes(self):
        base = np.array([float(m.get("altitude") or 0) for m in self._meta])
        pts = sorted(self._ctrl_pts)
        rows = np.array([p[0] for p in pts], float)
        alts = np.array([p[1] for p in pts], float)
        out = base.copy()
        r0, r1 = int(rows[0]), int(rows[-1])
        out[r0:r1 + 1] = np.interp(np.arange(r0, r1 + 1), rows, alts)
        return out

    def _update_preview(self):
        pp, ps = self._build_paths(self._interp_altitudes())
        self._pv_port.setPath(pp)
        self._pv_stbd.setPath(ps)

    def _rebuild_ctrl_markers(self):
        self._clear_ctrl_items()
        pen = QPen(QColor(255, 60, 60), 1)
        pen.setCosmetic(True)
        brush = QBrush(QColor(255, 60, 60, 180))
        for row, alt in self._ctrl_pts:
            cols = self._bt_cols(self._meta[row], alt)
            if cols is None:
                continue
            for c in cols:
                it = self._scene.addRect(c - 2, row - 1.5, 4, 4, pen, brush)
                it.setZValue(60)
                it.setFlag(it.GraphicsItemFlag.ItemIgnoresTransformations)
                self._ctrl_items.append(it)

    # ================================================ 마우스 / 키
    def mouseMoveEvent(self, ev):
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
        # ---- 바탐 편집 모드 ----
        if getattr(self, "_bottom_edit", False) and self._meta:
            pos = self.mapToScene(ev.position().toPoint())
            row = int(pos.y())
            if 0 <= row < len(self._meta):
                if ev.button() == Qt.LeftButton:
                    alt = self._col_to_alt(self._meta[row], pos.x())
                    if alt is not None and alt > 0:
                        self._ctrl_pts = [p for p in self._ctrl_pts
                                          if p[0] != row]
                        self._ctrl_pts.append((row, alt))
                        self._rebuild_ctrl_markers()
                        self._update_preview()
                elif ev.button() == Qt.RightButton and self._ctrl_pts:
                    i = min(range(len(self._ctrl_pts)),
                            key=lambda k: abs(self._ctrl_pts[k][0] - row))
                    self._ctrl_pts.pop(i)
                    self._rebuild_ctrl_markers()
                    self._update_preview()
            ev.accept()
            return


        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # 바탐 편집 중에는 더블클릭 무시 (제어점 오작동 방지)
        if getattr(self, "_bt_editing", False):   # ← 속성 없으면 False 취급
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
