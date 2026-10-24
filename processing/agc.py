# -*- coding: utf-8 -*-
"""
agc.py — 소나 방사보정(Radiometric Correction) 및 각도별 이득 보정(Angle Varied Gain, AVG / TVG)

참고 문헌 (Radiometric Processing Reference):
--------------------------------------------
  * Beaudoin, J.D., Hughes Clarke, J.E., Van Den Ameele, E.J., and Gardner, J.V.
    "Geometric and Radiometric Correction of Multibeam Backscatter Derived from RESON 8101 Systems"
    Ocean Mapping Group (OMG), University of New Brunswick / NOAA / USGS.
    - Section: "Removal of Angular Response" (pp. 17-21, Figures 19, 20, 21)

원리 및 개선 효과:
-----------------
  1. 해저면 직하방(First Bottom / Near-Nadir)과 원거리(Far Range)의 신호 불균형 해소:
     - 음향 신호는 센서로부터 멀어질수록 기하학적 확산 손실(Spherical Spreading, 40 log R)과
       해수 흡수 감쇄(Absorption, 2 alpha R)로 인해 자연스럽게 약화된다 (TVG 효과 보상).
     - 해저면 직하방(First Bottom 직후, 입사각 ~0° / Grazing angle ~90°)은 정반사(Specular Return)로 인해
       음파 강도가 비정상적으로 매우 세고 하얗게 포화(Saturation)되는 반면,
       외곽(Far range)으로 갈수록 입사각이 비스듬해져(Lambertian 산란 강도 급감) 어두워진다.
  2. First Bottom(해저면 최초 도달) 기준 보정 및 수층(Water Column) 보호:
     - 기존의 단순 열별 AGC는 First Bottom 위치를 인지하지 못하여 수층(Water Column)의 미세 노이즈까지
       과도하게 증폭하고, 바탐 경계부의 밝기를 왜곡시켰다.
     - 본 모듈은 매 핑(Ping)별 First Bottom(바탐 고도, Altitude)을 기준으로:
       ① 수층(Nadir ~ First Bottom): 원본 신호 유지 및 잡음 증폭 차단 (Water Column Preservation)
       ② 해저면(First Bottom ~ Swath Edge): First Bottom을 원점(d=0)으로 정렬하여
          Along-track 이동 윈도우 기반 평균 각도 응답 곡선(Angular Response Curve, ARC)을 추출하고,
          원거리 감쇄 보상 및 직하방 강반사 억제(Angle Varied Gain, AVG)를 적용한다.
"""
from __future__ import annotations

import numpy as np


def detect_first_bottom_fallback(
    img_f: np.ndarray,
    mid_col: int,
    noise_mult: float = 1.6,
    min_thresh: float = 25.0
) -> tuple[np.ndarray, np.ndarray]:
    """메타데이터 고도가 없을 때 영상 강도 도약(Gradient/Threshold)을 이용해 핑별 First Bottom 추정."""
    n_pings, w = img_f.shape
    bt_p = np.zeros(n_pings, dtype=np.int32)
    bt_s = np.zeros(n_pings, dtype=np.int32)

    # 센터 Nadir 부근 수층 노이즈 바닥값 추정
    c0 = max(0, mid_col - 12)
    c1 = min(w, mid_col + 12)
    nadir_sub = img_f[:, c0:c1]
    base_nadir = np.median(nadir_sub, axis=1)

    for i in range(n_pings):
        row = img_f[i]
        th = max(float(base_nadir[i]) * noise_mult, min_thresh)

        # 우현 (Nadir -> Starboard)
        s_part = row[mid_col + 8:]
        idx_s = np.where(s_part > th)[0]
        if len(idx_s) > 0:
            bt_s[i] = mid_col + 8 + idx_s[0]
        else:
            bt_s[i] = mid_col + int((w - mid_col) * 0.35)

        # 좌현 (Nadir -> Port)
        p_part = row[mid_col - 8 : : -1]
        idx_p = np.where(p_part > th)[0]
        if len(idx_p) > 0:
            bt_p[i] = mid_col - 8 - idx_p[0]
        else:
            bt_p[i] = int(mid_col * 0.65)

    # 1D 평활화로 단일 핑 튐 제거
    def _smooth_1d_int(arr: np.ndarray, win: int = 9) -> np.ndarray:
        if len(arr) < win or win < 3:
            return arr
        pad = win // 2
        padded = np.pad(arr.astype(np.float32), pad, mode="edge")
        res = np.convolve(padded, np.ones(win, dtype=np.float32) / float(win), mode="valid")
        return np.round(res).astype(np.int32)

    return _smooth_1d_int(bt_p), _smooth_1d_int(bt_s)


def extract_first_bottom_columns(
    img_shape: tuple[int, int],
    meta: list[dict] | None = None
) -> tuple[np.ndarray, np.ndarray, int]:
    """각 핑의 Port/Starboard First Bottom 컬럼 인덱스 및 Nadir 컬럼 반환."""
    n_pings, w = img_shape
    mid = w // 2
    if meta and len(meta) > 0:
        np_0 = meta[0].get("n_port")
        if np_0 and 0 < int(np_0) < w:
            mid = int(np_0)

    bt_p = np.zeros(n_pings, dtype=np.int32)
    bt_s = np.zeros(n_pings, dtype=np.int32)
    need_fallback = False

    for i in range(n_pings):
        alt = 0.0
        rp, rs = 70.0, 70.0
        np_i, ns_i = mid, w - mid
        if meta and i < len(meta):
            m = meta[i]
            alt = float(m.get("altitude") or 0.0)
            rp = float(m.get("range_port") or m.get("slant_range") or 70.0)
            rs = float(m.get("range_stbd") or m.get("slant_range") or 70.0)
            np_i = int(m.get("n_port") or mid)
            ns_i = int(m.get("n_stbd") or (w - mid))

        if alt >= 0.5 and rp > 0 and rs > 0:
            frac_p = min(max(alt / rp, 0.0), 1.0)
            frac_s = min(max(alt / rs, 0.0), 1.0)
            bt_p[i] = int(round(np_i - frac_p * np_i))
            bt_s[i] = int(round(np_i + frac_s * ns_i))
        else:
            need_fallback = True
            bt_p[i] = -1
            bt_s[i] = -1

    if need_fallback:
        # 고도가 없는 핑은 기본 위치 추정
        for i in range(n_pings):
            if bt_p[i] < 0:
                bt_p[i] = int(mid * 0.65)
            if bt_s[i] < 0:
                bt_s[i] = mid + int((w - mid) * 0.35)

    bt_p = np.clip(bt_p, 0, mid).astype(np.int32)
    bt_s = np.clip(bt_s, mid, w - 1).astype(np.int32)

    return bt_p, bt_s, mid


def apply_agc(
    img_f: np.ndarray,
    meta: list[dict] | None = None,
    window: int = 301,
    strength: float = 1.0,
    preserve_water_column: bool = True,
    water_column_gain: float = 1.0,
    gain_clip: tuple[float, float] = (0.25, 4.0),
    eps: float = 1e-3
) -> np.ndarray:
    """방사보정(Radiometric Correction) 및 각도별 이득 보정(Angle Varied Gain, AVG / TVG).

    매개변수:
      img_f                 : 2D uint8 또는 float32 소나 영상 (n_pings, n_samples)
      meta                  : 핑별 메타데이터 리스트 (altitude, range_port, range_stbd 등)
      window                : along-track 이동평균 창 크기 (기본 301 핑)
      strength              : 보정 강도 (0.0=원본 유지 ~ 1.0=완전 평탄화)
      preserve_water_column : True 시 수층(Water Column) 잡음 증폭 차단 및 원본 보존
      water_column_gain     : 수층 이득 계수 (기본 1.0 = 원본 유지)
      gain_clip             : (min_gain, max_gain) 과도한 잡음 증폭 방지용 이득 클리핑 범위
      eps                   : 0 나눗셈 방지 엡실론

    반환:
      입력과 동일한 타입의 보정된 영상 (uint8 또는 float32)
    """
    is_uint8 = (img_f.dtype == np.uint8)
    arr = img_f.astype(np.float32)
    n_pings, w = arr.shape

    if n_pings < 2 or strength <= 0.0:
        return img_f.copy()

    # 1. First Bottom 위치 파악
    bt_p, bt_s, mid = extract_first_bottom_columns((n_pings, w), meta)

    # 2. First Bottom 기준 해저면 배열(Bottom-relative) 구성
    max_d_s = w - mid
    max_d_p = mid

    stbd_mat = np.zeros((n_pings, max_d_s), dtype=np.float32)
    stbd_msk = np.zeros((n_pings, max_d_s), dtype=np.float32)
    port_mat = np.zeros((n_pings, max_d_p), dtype=np.float32)
    port_msk = np.zeros((n_pings, max_d_p), dtype=np.float32)

    for i in range(n_pings):
        bp, bs = bt_p[i], bt_s[i]
        slen = w - bs
        if slen > 0:
            stbd_mat[i, :slen] = arr[i, bs:]
            stbd_msk[i, :slen] = 1.0
        plen = bp
        if plen > 0:
            port_mat[i, :plen] = arr[i, bp - 1 : : -1]
            port_msk[i, :plen] = 1.0

    # 3. Along-track 이동평균 (Pure NumPy cumsum)
    win = max(11, int(window))
    if win % 2 == 0:
        win += 1

    def _moving_avg_2d(mat: np.ndarray, msk: np.ndarray, w_size: int) -> np.ndarray:
        n, cols = mat.shape
        c_m = np.vstack([np.zeros((1, cols), dtype=np.float32), np.cumsum(mat, axis=0)])
        c_k = np.vstack([np.zeros((1, cols), dtype=np.float32), np.cumsum(msk, axis=0)])
        hi = np.minimum(np.arange(n) + w_size // 2 + 1, n)
        lo = np.maximum(np.arange(n) - w_size // 2, 0)
        num = c_m[hi] - c_m[lo]
        den = c_k[hi] - c_k[lo]
        return num / np.maximum(den, 1.0)

    s_local = _moving_avg_2d(stbd_mat, stbd_msk, win)
    p_local = _moving_avg_2d(port_mat, port_msk, win)

    # 4. 레인지(d) 축 방향 스무딩 (Beam Pattern & Angular Response 곡선 추출)
    kw = 31
    def _smooth_range(mat: np.ndarray, kernel_w: int) -> np.ndarray:
        n, cols = mat.shape
        c = np.hstack([np.zeros((n, 1), dtype=np.float32), np.cumsum(mat, axis=1)])
        hi = np.minimum(np.arange(cols) + kernel_w // 2 + 1, cols)
        lo = np.maximum(np.arange(cols) - kernel_w // 2, 0)
        cnt = (hi - lo)[None, :].astype(np.float32)
        return (c[:, hi] - c[:, lo]) / np.maximum(cnt, 1.0)

    s_trend = _smooth_range(s_local, kw)
    p_trend = _smooth_range(p_local, kw)

    # 5. 기준 목표 강도(Reference Level) 산출
    # Beaudoin et al. (p. 17): 오프-나디르(Off-nadir, d=30~250) 안정된 해저면의 평균을 타깃으로 설정
    d1_s, d2_s = min(30, max_d_s - 1), min(260, max_d_s)
    ref_s = s_trend[:, d1_s:d2_s].mean(axis=1, keepdims=True)

    d1_p, d2_p = min(30, max_d_p - 1), min(260, max_d_p)
    ref_p = p_trend[:, d1_p:d2_p].mean(axis=1, keepdims=True)

    ref_target = 0.5 * (ref_s + ref_p)
    ref_target = np.maximum(ref_target, 10.0)

    # 6. 각도별 이득 곡선 (AVG Gain Curve) 계산
    min_g, max_g = gain_clip
    gain_s = np.clip(ref_target / np.maximum(s_trend, eps), min_g, max_g)
    gain_p = np.clip(ref_target / np.maximum(p_trend, eps), min_g, max_g)

    # 보정 강도(strength) 조절
    strength = min(1.0, max(0.0, float(strength)))
    if strength < 1.0:
        gain_s = 1.0 + strength * (gain_s - 1.0)
        gain_p = 1.0 + strength * (gain_p - 1.0)

    # 바탐 경계부 부드러운 블렌딩 (d=0..3 스텝 아티팩트 제거)
    blend_s = np.minimum(np.arange(max_d_s, dtype=np.float32) / 4.0, 1.0)[None, :]
    gain_s = 1.0 + blend_s * (gain_s - 1.0)

    blend_p = np.minimum(np.arange(max_d_p, dtype=np.float32) / 4.0, 1.0)[None, :]
    gain_p = 1.0 + blend_p * (gain_p - 1.0)

    # 7. 영상에 이득 적용
    out = arr.copy()
    for i in range(n_pings):
        bp, bs = bt_p[i], bt_s[i]

        # 우현 해저면 (First bottom -> Swath outer edge)
        slen = w - bs
        if slen > 0:
            out[i, bs:] = arr[i, bs:] * gain_s[i, :slen]

        # 좌현 해저면 (First bottom -> Swath outer edge)
        plen = bp
        if plen > 0:
            out[i, :bp] = arr[i, :bp] * gain_p[i, :plen][::-1]

        # 수층 (Water column): 원본 보존
        if preserve_water_column:
            if water_column_gain != 1.0:
                out[i, bp:bs] = arr[i, bp:bs] * float(water_column_gain)

    if is_uint8:
        return np.clip(out, 0, 255).astype(np.uint8)
    return out
