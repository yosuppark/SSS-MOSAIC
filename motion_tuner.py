# -*- coding: utf-8 -*-
"""motion_tuner.py - 모션 보정 파라미터 시각 튜닝 다이얼로그 (PySide6)

waterfall_view.py 와 동일한 PySide6 스택을 사용한다.

    from motion_tuner import open_motion_tuner
    open_motion_tuner(self, port, stbd, motion, title=fname)

좌: 원본 / 중: 보정 / 우: 차이맵(128=변화없음)
슬라이더를 움직이면 즉시 재계산되고 하단에 level std 감소율이 표시된다.
모자이크 적용 여부와 무관한 독립 검토 도구다.
"""

from __future__ import annotations

import json
import os

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from motion_tuner_core import (ManualModel, TermCache, apply_gain, diff_image8,
                               gain_map, level_std, score, seed_from_fit,
                               to_image8)

TERMS = ("roll", "pitch", "roll_rate", "pitch_rate")
GRANGE = {"roll": 0.10, "pitch": 0.15, "roll_rate": 0.05, "pitch_rate": 0.10}
STEP = 1000.0


def _qpix(arr):
    a = np.ascontiguousarray(np.asarray(arr, dtype=np.uint8))
    if a.ndim != 2 or a.size == 0:
        a = np.zeros((1, 1), dtype=np.uint8)
    h, w = a.shape
    img = QImage(a.data, w, h, w, QImage.Format_Grayscale8).copy()
    return QPixmap.fromImage(img)


class _Pane(QWidget):
    """제목 + 이미지 라벨."""

    def __init__(self, title):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        self.cap = QLabel(title)
        self.cap.setAlignment(Qt.AlignCenter)
        self.lab = QLabel()
        self.lab.setAlignment(Qt.AlignCenter)
        self.lab.setMinimumSize(220, 380)
        self.lab.setStyleSheet("background:#101010;")
        v.addWidget(self.cap)
        v.addWidget(self.lab, 1)
        self._pm = None

    def set_image(self, arr):
        self._pm = _qpix(arr)
        self._draw()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._draw()

    def _draw(self):
        if self._pm is None:
            return
        self.lab.setPixmap(self._pm.scaled(self.lab.size(), Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation))


class MotionTunerDialog(QDialog):
    """port/stbd 각각의 수동 모션 게인을 눈으로 맞춘다."""

    def __init__(self, parent, port, stbd, motion, title=""):
        super().__init__(parent)
        self.setWindowTitle("모션 보정 튜너  %s" % str(title))
        self.resize(1180, 820)
        self.amp = {"port": np.asarray(port, dtype=float),
                    "stbd": np.asarray(stbd, dtype=float)}
        self.motion = motion
        self.models = {"port": ManualModel(), "stbd": ManualModel()}
        self.caches = {}
        self.base = {}
        self.side = "port"
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._recalc)
        self._build()
        self._load_model()
        self._recalc()

    # -------------------------------------------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.cb_side = QComboBox()
        self.cb_side.addItems(["port", "stbd"])
        self.cb_side.currentTextChanged.connect(self._side_changed)
        self.chk = QCheckBox("보정 적용")
        self.chk.setChecked(True)
        self.chk.stateChanged.connect(self._touch)
        self.sp_win = QSpinBox()
        self.sp_win.setRange(11, 601)
        self.sp_win.setSingleStep(10)
        self.sp_win.setValue(101)
        self.sp_win.valueChanged.connect(self._win_changed)
        self.sp_db = QDoubleSpinBox()
        self.sp_db.setRange(0.5, 20.0)
        self.sp_db.setSingleStep(0.5)
        self.sp_db.setValue(6.0)
        self.sp_db.valueChanged.connect(self._touch)
        for w in (QLabel("채널"), self.cb_side, self.chk, QLabel("디트렌드 창"),
                  self.sp_win, QLabel("최대 dB"), self.sp_db):
            top.addWidget(w)
        top.addStretch(1)
        root.addLayout(top)

        views = QHBoxLayout()
        self.p_raw = _Pane("원본")
        self.p_cor = _Pane("보정")
        self.p_dif = _Pane("차이 (±3 dB)")
        for p in (self.p_raw, self.p_cor, self.p_dif):
            views.addWidget(p, 1)
        root.addLayout(views, 1)

        gb = QGroupBox("항별 게인 [dB/단위] 및 지연 [ping]")
        g = QGridLayout(gb)
        self.sl, self.lg, self.vl = {}, {}, {}
        for r, nm in enumerate(TERMS):
            s = QSlider(Qt.Horizontal)
            s.setRange(int(-GRANGE[nm] * STEP), int(GRANGE[nm] * STEP))
            s.valueChanged.connect(self._touch)
            k = QSpinBox()
            k.setRange(-15, 15)
            k.valueChanged.connect(self._touch)
            lv = QLabel("0.0000")
            lv.setMinimumWidth(64)
            g.addWidget(QLabel(nm), r, 0)
            g.addWidget(s, r, 1)
            g.addWidget(lv, r, 2)
            g.addWidget(QLabel("lag"), r, 3)
            g.addWidget(k, r, 4)
            self.sl[nm], self.lg[nm], self.vl[nm] = s, k, lv
        root.addWidget(gb)

        gb2 = QGroupBox("거리 가중 (nadir 0 → far 1)")
        g2 = QGridLayout(gb2)
        self.rng = {}
        for c, (nm, lo, hi, dv) in enumerate((("near", 0.0, 1.0, 0.35),
                                              ("far", 0.0, 1.0, 0.55),
                                              ("tail", 0.2, 1.5, 1.0))):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(0.05)
            sp.setValue(dv)
            sp.valueChanged.connect(self._touch)
            g2.addWidget(QLabel(nm), 0, c * 2)
            g2.addWidget(sp, 0, c * 2 + 1)
            self.rng[nm] = sp
        root.addWidget(gb2)

        self.stat = QLabel("-")
        self.stat.setStyleSheet("font-family:Consolas;")
        root.addWidget(self.stat)

        btn = QHBoxLayout()
        for txt, fn in (("자동 적합값 불러오기", self._auto),
                        ("초기화", self._reset), ("저장", self._save),
                        ("불러오기", self._open), ("닫기", self.accept)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            btn.addWidget(b)
        btn.addStretch(1)
        root.addLayout(btn)

    # -------------------------------------------------- 상태
    def _cache(self):
        key = (self.side, int(self.sp_win.value()))
        c = self.caches.get(key)
        if c is None:
            c = TermCache(self.motion, self.amp[self.side].shape[0],
                          win=int(self.sp_win.value()))
            self.caches[key] = c
        return c

    def _load_model(self):
        self._busy = True
        m = self.models[self.side]
        for nm in TERMS:
            self.sl[nm].setValue(int(round(m.gains.get(nm, 0.0) * STEP)))
            self.lg[nm].setValue(int(m.lags.get(nm, 0)))
        self.rng["near"].setValue(m.near)
        self.rng["far"].setValue(m.far)
        self.rng["tail"].setValue(m.tail)
        self.sp_db.setValue(m.max_db)
        self.chk.setChecked(bool(m.enabled))
        self._busy = False

    def _store_model(self):
        m = self.models[self.side]
        for nm in TERMS:
            m.gains[nm] = self.sl[nm].value() / STEP
            m.lags[nm] = int(self.lg[nm].value())
            self.vl[nm].setText("%+.4f" % m.gains[nm])
        m.near = float(self.rng["near"].value())
        m.far = float(self.rng["far"].value())
        m.tail = float(self.rng["tail"].value())
        m.max_db = float(self.sp_db.value())
        m.enabled = bool(self.chk.isChecked())
        return m

    def _touch(self, *_):
        if not self._busy:
            self._timer.start()

    def _win_changed(self, *_):
        self.base.clear()
        self._touch()

    def _side_changed(self, txt):
        self.side = str(txt)
        self._load_model()
        self._recalc()

    # -------------------------------------------------- 계산
    def _recalc(self):
        A = self.amp.get(self.side)
        if A is None or A.ndim != 2 or A.size == 0:
            self.stat.setText("데이터 없음")
            return
        m = self._store_model()
        win = int(self.sp_win.value())
        b = self.base.get((self.side, win))
        if b is None:
            b = (to_image8(A)[0], level_std(A, win))
            self.base[(self.side, win)] = b
        img0, std0 = b
        G = gain_map(m, self._cache(), A.shape[1], nadir_first=True)
        out = apply_gain(A, G)
        std1 = level_std(out, win)
        self.p_raw.set_image(img0)
        self.p_cor.set_image(to_image8(out)[0])
        self.p_dif.set_image(diff_image8(A, out, span_db=3.0))
        self.stat.setText(
            "[%s]  level std %.3f -> %.3f dB   (%+.1f%%)   |gain|max %.2f dB   "
            "pings=%d  samples=%d"
            % (self.side, std0, std1, score(std0, std1),
               float(np.abs(G).max()) if G.size else 0.0, A.shape[0], A.shape[1]))

    # -------------------------------------------------- 버튼
    def _auto(self):
        try:
            from motion_gain import fit_motion_gain
            fit = fit_motion_gain(self.amp[self.side], self.motion,
                                  win=int(self.sp_win.value()), nbins=24,
                                  max_lag=15, nadir_first=True, side=self.side,
                                  auto=False)
            self.models[self.side] = seed_from_fit(fit)
        except Exception as exc:
            self.stat.setText("자동 적합 실패: %s" % exc)
            return
        self._load_model()
        self._recalc()

    def _reset(self):
        self.models[self.side] = ManualModel()
        self._load_model()
        self._recalc()

    def _save(self):
        fn, _ = QFileDialog.getSaveFileName(self, "프리셋 저장", "motion_preset.json",
                                            "JSON (*.json)")
        if not fn:
            return
        self._store_model()
        d = {k: v.to_dict() for k, v in self.models.items()}
        d["win"] = int(self.sp_win.value())
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        self.stat.setText("저장: %s" % os.path.basename(fn))

    def _open(self):
        fn, _ = QFileDialog.getOpenFileName(self, "프리셋 불러오기", "",
                                            "JSON (*.json)")
        if not fn:
            return
        try:
            with open(fn, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as exc:
            self.stat.setText("불러오기 실패: %s" % exc)
            return
        for k in ("port", "stbd"):
            if k in d:
                self.models[k] = ManualModel.from_dict(d[k])
        if "win" in d:
            self._busy = True
            self.sp_win.setValue(int(d["win"]))
            self._busy = False
        self.base.clear()
        self._load_model()
        self._recalc()

    # -------------------------------------------------- 결과
    def params(self):
        """{'port': {...}, 'stbd': {...}} 형태로 현재 설정 반환."""
        self._store_model()
        return {k: v.to_dict() for k, v in self.models.items()}


def open_motion_tuner(parent, port, stbd, motion, title=""):
    """모달 실행 후 파라미터 dict 반환 (취소 시 None)."""
    dlg = MotionTunerDialog(parent, port, stbd, motion, title)
    if dlg.exec():
        return dlg.params()
    return None