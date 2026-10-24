# -*- coding: utf-8 -*-
"""nav_dialog.py — 항법 자료 오측/빈도 처리 및 핑 보간 설정 대화상자 (PySide6)"""
from __future__ import annotations

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QDoubleSpinBox, QCheckBox, QComboBox,
    QRadioButton, QButtonGroup, QMessageBox, QFrame,
)

from processing.nav_interpolator import build_continuous_timeline


class NavProcessDialog(QDialog):
    """항법 보정 및 매 핑별 위치/헤딩 보간 파라미터 설정 대화상자."""

    def __init__(self, parent=None, files=None, cur_idx=0):
        super().__init__(parent)
        self.setWindowTitle("항법 보정 및 핑 보간 (Navigation Processing)")
        self.resize(520, 560)

        self.files = files or []
        self.cur_idx = max(0, min(cur_idx, len(self.files) - 1)) if self.files else -1

        self._build_ui()
        self._analyze_current_file()

    def _build_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setSpacing(12)

        # 1. 파일 분석 요약 카드
        grp_info = QGroupBox("항법 정보 및 Ping Update Rate 분석")
        info_lay = QGridLayout(grp_info)
        info_lay.setSpacing(6)

        self.lbl_filename = QLabel("-")
        self.lbl_filename.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.lbl_pings = QLabel("-")
        self.lbl_duration = QLabel("-")
        self.lbl_ping_rate = QLabel("-")
        self.lbl_ping_rate.setStyleSheet("font-weight: bold; color: #d35400;")
        self.lbl_gps_rate = QLabel("-")
        self.lbl_ratio = QLabel("-")
        self.lbl_diagnosis = QLabel("-")
        self.lbl_diagnosis.setWordWrap(True)
        self.lbl_diagnosis.setStyleSheet("color: #7f8c8d; font-size: 11px;")

        info_lay.addWidget(QLabel("대상 파일:"), 0, 0)
        info_lay.addWidget(self.lbl_filename, 0, 1)
        info_lay.addWidget(QLabel("총 소나 핑:"), 1, 0)
        info_lay.addWidget(self.lbl_pings, 1, 1)
        info_lay.addWidget(QLabel("기록 시간:"), 2, 0)
        info_lay.addWidget(self.lbl_duration, 2, 1)
        info_lay.addWidget(QLabel("추정 Ping Rate:"), 3, 0)
        info_lay.addWidget(self.lbl_ping_rate, 3, 1)
        info_lay.addWidget(QLabel("GPS 갱신 빈도:"), 4, 0)
        info_lay.addWidget(self.lbl_gps_rate, 4, 1)
        info_lay.addWidget(QLabel("1초당 핑 개수:"), 5, 0)
        info_lay.addWidget(self.lbl_ratio, 5, 1)
        info_lay.addWidget(self.lbl_diagnosis, 6, 0, 1, 2)

        vbox.addWidget(grp_info)

        # 2. 보정 파라미터 그룹
        grp_param = QGroupBox("보정 및 보간 파라미터 설정")
        param_lay = QGridLayout(grp_param)
        param_lay.setSpacing(8)

        # 스파이크 필터
        self.chk_spike = QCheckBox("스파이크 검출 및 이상치 복원")
        self.chk_spike.setChecked(True)
        self.spn_speed = QDoubleSpinBox()
        self.spn_speed.setRange(1.0, 50.0)
        self.spn_speed.setValue(12.0)
        self.spn_speed.setSuffix(" m/s")
        self.spn_speed.setToolTip("지정한 속도를 초과하는 비정상적 위치 점프를 스파이크로 감지합니다.")

        param_lay.addWidget(self.chk_spike, 0, 0)
        param_lay.addWidget(QLabel("최대 허용 선속:"), 0, 1)
        param_lay.addWidget(self.spn_speed, 0, 2)

        # 기준 위치 스무딩
        self.spn_smooth = QDoubleSpinBox()
        self.spn_smooth.setRange(0.0, 30.0)
        self.spn_smooth.setValue(3.0)
        self.spn_smooth.setSingleStep(0.5)
        self.spn_smooth.setSuffix(" 초 (sec)")
        self.spn_smooth.setToolTip("GPS 수신 지터를 제거하기 위한 가우시안 스무딩 윈도우 크기입니다.")

        param_lay.addWidget(QLabel("기준 위치 스무딩:"), 1, 0)
        param_lay.addWidget(self.spn_smooth, 1, 1, 1, 2)

        # 보간 알고리즘
        self.cmb_interp = QComboBox()
        self.cmb_interp.addItem("Hermite Cubic Spline (유려한 곡선/선속 연속 보간)", "hermite")
        self.cmb_interp.addItem("Linear Vector (선형 벡터 등속 보간)", "linear")

        param_lay.addWidget(QLabel("핑별 위치 보간:"), 2, 0)
        param_lay.addWidget(self.cmb_interp, 2, 1, 1, 2)

        # 헤딩 보정 방식
        self.cmb_hdg = QComboBox()
        self.cmb_hdg.addItem("센서 헤딩(SensorHeading) 360° unwrap 보간", "sensor")
        self.cmb_hdg.addItem("항적 진행방향(Course Over Ground)으로 대체", "cog")

        param_lay.addWidget(QLabel("헤딩(Heading) 보정:"), 3, 0)
        param_lay.addWidget(self.cmb_hdg, 3, 1, 1, 2)

        # 사이드카 저장
        self.chk_sidecar = QCheckBox("사이드카 파일(nav/<파일명>.nav.csv) 자동 저장")
        self.chk_sidecar.setChecked(True)
        self.chk_sidecar.setToolTip("보정된 항법을 사이드카에 저장하여 모자이크 생성 및 재로드 시 자동 동기화합니다.")
        param_lay.addWidget(self.chk_sidecar, 4, 0, 1, 3)

        vbox.addWidget(grp_param)

        # 3. 적용 대상 선택
        grp_target = QGroupBox("적용 대상")
        target_lay = QHBoxLayout(grp_target)
        self.rb_current = QRadioButton("현재 활성 파일만 적용")
        self.rb_all = QRadioButton("열려있는 모든 파일 일괄 적용")
        self.rb_current.setChecked(True)
        if len(self.files) <= 1:
            self.rb_all.setEnabled(False)
        target_lay.addWidget(self.rb_current)
        target_lay.addWidget(self.rb_all)

        vbox.addWidget(grp_target)

        # 4. 하단 버튼
        btn_box = QHBoxLayout()
        btn_box.addStretch(1)

        self.btn_apply = QPushButton("보정 및 보간 적용 (Apply)")
        self.btn_apply.setDefault(True)
        self.btn_apply.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_apply.clicked.connect(self.accept)

        self.btn_cancel = QPushButton("취소 (Cancel)")
        self.btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(self.btn_apply)
        btn_box.addWidget(self.btn_cancel)
        vbox.addLayout(btn_box)

    def _analyze_current_file(self):
        """현재 선택된 XTF 파일의 항법 데이터 특성을 분석하여 화면에 표시."""
        if not self.files or self.cur_idx < 0:
            self.lbl_filename.setText("열린 파일 없음")
            self.btn_apply.setEnabled(False)
            return

        f = self.files[self.cur_idx]
        path = f.get("path", "")
        meta = f.get("meta", [])
        self.lbl_filename.setText(os.path.basename(path))

        n = len(meta)
        self.lbl_pings.setText(f"{n:,} pings")

        _, stats = build_continuous_timeline(meta)
        dur = stats.get("duration_sec", 0.0)
        p_rate = stats.get("avg_ping_rate", 0.0)
        g_rate = stats.get("gps_update_rate", 0.0)
        ratio = stats.get("pings_per_update", 0.0)

        mins = int(dur // 60)
        secs = dur % 60
        self.lbl_duration.setText(f"{mins}분 {secs:.1f}초 ({dur:.1f}s)")
        self.lbl_ping_rate.setText(f"{p_rate:.1f} Hz")
        self.lbl_gps_rate.setText(f"{g_rate:.2f} Hz (약 {stats.get('gps_updates', 0):,}회 갱신)")
        self.lbl_ratio.setText(f"약 {ratio:.1f} 핑 / 1회 갱신")

        if ratio >= 3.0:
            self.lbl_diagnosis.setText(
                f"⚠️ GPS 1회 갱신당 약 {ratio:.0f}개의 핑이 동일 좌표를 공유하고 있습니다.\n"
                f"   보정을 실행하면 매 핑마다 연속 선속/방향 기반의 새 위치가 정밀 보간됩니다."
            )
            self.lbl_diagnosis.setStyleSheet("color: #d35400; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_diagnosis.setText("ℹ️ GPS 갱신 빈도와 핑 빈도가 유사합니다.")
            self.lbl_diagnosis.setStyleSheet("color: #27ae60; font-size: 11px;")

    def get_params(self) -> dict:
        """대화상자 설정 파라미터 반환."""
        return {
            "spike_filter": self.chk_spike.isChecked(),
            "max_speed_mps": float(self.spn_speed.value()),
            "smooth_window_sec": float(self.spn_smooth.value()),
            "interp_method": self.cmb_interp.currentData(),
            "use_cog_heading": (self.cmb_hdg.currentData() == "cog"),
            "save_sidecar": self.chk_sidecar.isChecked(),
            "apply_all": self.rb_all.isChecked(),
        }
