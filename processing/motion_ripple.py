# -*- coding: utf-8 -*-
"""motion_ripple.py — 토우피쉬 롤링/피칭 리플 효과(Motion Ripple Effect) 해석 및 보정 모듈

이론적 배경:
-----------
  * 예인형 사이드스캔 소나(Towed Side Scan Sonar)는 해상 파도(Swell)에 의해 토우피쉬가
    주기적인 롤링(Rolling) 및 피칭(Pitching)/히브(Heave) 운동을 겪는다.
  * 롤링(Rolling):
    - 토우피쉬가 한쪽(우현)으로 기울면 우현 빔의 입사각(grazing angle)이 가팔라져 강한 반사파(명부, Bright band)가
      형성되는 반면, 반대편(좌현)은 빔이 들려 약한 반사파(암부, Dark band)가 형성된다.
    - 반주기 뒤에는 반대로 좌현이 밝아지고 우현이 어두워진다.
    - 따라서 좌/우현 간에 약 180° 위상차(Anti-phase, 음의 상관관계)를 갖는 교차 줄무늬(Ripple banding)가 발생한다.
  * 피칭/히브(Pitching/Heave):
    - 전후 방향 기울임이나 상하 운동에 의해 양현 빔의 방사 중심축이 동시에 변동하여,
      양현 모두 동시에 밝아지고 어두워지는 동위상(In-phase, 양의 상관관계) 줄무늬가 발생한다.
  * 본 모듈은 마우스로 지정된 Waterfall ROI 영역의 핑별 강도 시계열을 분석하여:
    ① FFT 전력 스펙트럼 및 지배 파랑 주기(Dominant Swell Period) 자동 검출
    ② 좌/우현 상호상관(Cross-Correlation)을 통한 롤링 vs 피칭 운동 모드 진단
    ③ 적응형 롤/피치 성분 분리 및 주파수 노치 필터링을 통한 리플 아티팩트 제거
"""
from __future__ import annotations

import numpy as np


def smooth_1d(arr: np.ndarray, window: int = 21) -> np.ndarray:
    """1D 이동평균 평활화 (Edge 패딩으로 경계 왜곡 최소화)."""
    a = np.asarray(arr, dtype=np.float64)
    if a.size < 3 or window < 2:
        return a.copy()
    w = max(3, int(window))
    if w % 2 == 0:
        w += 1
    w = min(w, a.size if a.size % 2 == 1 else a.size - 1)
    if w < 3:
        return a.copy()
    pad = w // 2
    padded = np.pad(a, pad, mode="edge")
    kernel = np.ones(w, dtype=np.float64) / float(w)
    return np.convolve(padded, kernel, mode="valid")


def extract_channel_profiles(
    img: np.ndarray,
    meta: list[dict] | None = None,
    r0: int = 0,
    r1: int | None = None,
    col_range: tuple[int, int] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """지정된 핑 구간에서 Port/Starboard 유효 해저면 평균 강도 프로파일 추출.

    반환: (port_prof, stbd_prof, sym_pitch, antisym_roll)
    """
    n_pings, n_cols = img.shape[:2]
    r0 = max(0, int(r0))
    r1 = n_pings if r1 is None else min(n_pings, int(r1))
    if r1 <= r0:
        r1 = min(n_pings, r0 + 100)

    sub = img[r0:r1].astype(np.float32)
    n_rows, w = sub.shape

    # Nadir 및 양현 분할
    mid = w // 2
    if meta and len(meta) > r0:
        m0 = meta[r0]
        np_ = int(m0.get("n_port") or mid)
        mid = np_

    # 수층(Water Column) 및 원거리 노이즈 제외한 안정된 해저면 샘플링 영역
    # Port: 0 ~ mid (Nadir에서 좌측으로 최소 15% 바깥 ~ 85% 구간)
    # Stbd: mid ~ w (Nadir에서 우측으로 최소 15% 바깥 ~ 85% 구간)
    if col_range is not None:
        c0, c1 = col_range
        c0, c1 = max(0, c0), min(w, c1)
        if c1 > mid and c0 < mid:
            p_c0, p_c1 = c0, mid
            s_c0, s_c1 = mid, c1
        elif c1 <= mid:
            p_c0, p_c1 = c0, c1
            s_c0, s_c1 = mid + (mid - p_c1), mid + (mid - p_c0)
        else:
            s_c0, s_c1 = c0, c1
            p_c0, p_c1 = max(0, mid - (s_c1 - mid)), max(0, mid - (s_c0 - mid))
    else:
        # 기본 양현 해저면 구간 자동 산출
        p_c0 = int(mid * 0.15)
        p_c1 = int(mid * 0.85)
        s_c0 = mid + int((w - mid) * 0.15)
        s_c1 = mid + int((w - mid) * 0.85)

    p_c0, p_c1 = max(0, p_c0), min(mid, max(p_c0 + 5, p_c1))
    s_c0, s_c1 = max(mid, s_c0), min(w, max(s_c0 + 5, s_c1))

    port_prof = np.mean(sub[:, p_c0:p_c1], axis=1)
    stbd_prof = np.mean(sub[:, s_c0:s_c1], axis=1)

    # 대칭 성분(Symmetric ≈ Pitching/Heave)과 반대칭 성분(Anti-symmetric ≈ Rolling)
    sym_pitch = 0.5 * (stbd_prof + port_prof)
    antisym_roll = 0.5 * (stbd_prof - port_prof)

    return port_prof, stbd_prof, sym_pitch, antisym_roll


def analyze_motion_ripple(
    port_prof: np.ndarray,
    stbd_prof: np.ndarray,
    ping_rate_hz: float = 10.0,
) -> dict:
    """좌/우현 강도 프로파일을 분석하여 파랑 주기, 운동 모드(롤링 vs 피칭), 리플 진폭 계산."""
    n = len(port_prof)
    if n < 8:
        return {
            "dominant_period_pings": 0.0,
            "dominant_period_sec": 0.0,
            "dominant_freq_hz": 0.0,
            "motion_mode": "미확인 (자료 부족)",
            "correlation": 0.0,
            "modulation_depth_pct": 0.0,
            "freqs": np.zeros(0),
            "psd_stbd": np.zeros(0),
            "psd_port": np.zeros(0),
            "psd_roll": np.zeros(0),
            "psd_pitch": np.zeros(0),
        }

    # 1. 기저 경향(저주파 지형/해저면 특성) 제거를 위한 디트렌딩
    detrend_w = max(15, min(n // 4 * 2 + 1, 61))
    p_trend = smooth_1d(port_prof, detrend_w)
    s_trend = smooth_1d(stbd_prof, detrend_w)

    p_osc = port_prof - p_trend
    s_osc = stbd_prof - s_trend

    sym_osc = 0.5 * (s_osc + p_osc)
    roll_osc = 0.5 * (s_osc - p_osc)

    # 2. 좌/우현 상호상관 계수 (Pearson Correlation)
    std_p = float(np.std(p_osc))
    std_s = float(np.std(s_osc))
    if std_p > 1e-4 and std_s > 1e-4:
        r_ps = float(np.corrcoef(p_osc, s_osc)[0, 1])
    else:
        r_ps = 0.0

    # 3. FFT 주파수 스펙트럼 분석 (단위: cycles/ping)
    win = np.hanning(n)
    fft_s = np.fft.rfft(s_osc * win)
    fft_p = np.fft.rfft(p_osc * win)
    fft_roll = np.fft.rfft(roll_osc * win)
    fft_pitch = np.fft.rfft(sym_osc * win)
    freqs = np.fft.rfftfreq(n, d=1.0)  # cycles per ping

    psd_s = np.abs(fft_s) ** 2
    psd_p = np.abs(fft_p) ** 2
    psd_roll = np.abs(fft_roll) ** 2
    psd_pitch = np.abs(fft_pitch) ** 2

    # 파랑 리플에 해당하는 주파수 범위 검색 (보통 5 ~ 80 핑/주기, 즉 0.0125 ~ 0.2 cycles/ping)
    valid_f = (freqs >= 0.01) & (freqs <= 0.35)
    if not np.any(valid_f):
        valid_f = freqs > 0.0

    # 주 전력 피크 검출
    total_psd = psd_s + psd_p
    peak_idx = np.argmax(total_psd[valid_f])
    dom_f = float(freqs[valid_f][peak_idx])

    if dom_f > 1e-5:
        dom_period_pings = 1.0 / dom_f
        rate = max(0.1, float(ping_rate_hz))
        dom_period_sec = dom_period_pings / rate
        dom_freq_hz = dom_f * rate
    else:
        dom_period_pings = 0.0
        dom_period_sec = 0.0
        dom_freq_hz = 0.0

    # 4. 운동 모드 판별
    # 롤링은 좌우 역위상(r_ps < -0.2), 피칭/히브는 좌우 동위상(r_ps > 0.2)
    if r_ps <= -0.25:
        motion_mode = "롤링(Rolling) 우세 [좌우 역위상 교차 패턴]"
    elif r_ps >= 0.25:
        motion_mode = "피칭(Pitching)/히브 우세 [양현 동위상 명암 반복]"
    else:
        motion_mode = "롤링 + 피칭 복합 운동 (Coupled Ripple)"

    # 5. 리플 변조 깊이 (Modulation Depth %)
    mean_val = float(np.mean(0.5 * (port_prof + stbd_prof)))
    ripple_amp = float(np.percentile(np.abs(s_osc), 90))
    mod_pct = (ripple_amp / max(mean_val, 1.0)) * 100.0

    return {
        "dominant_period_pings": dom_period_pings,
        "dominant_period_sec": dom_period_sec,
        "dominant_freq_hz": dom_freq_hz,
        "dominant_freq_cpp": dom_f,
        "motion_mode": motion_mode,
        "correlation": r_ps,
        "modulation_depth_pct": mod_pct,
        "p_osc": p_osc,
        "s_osc": s_osc,
        "sym_osc": sym_osc,
        "roll_osc": roll_osc,
        "freqs": freqs,
        "psd_stbd": psd_s,
        "psd_port": psd_p,
        "psd_roll": psd_roll,
        "psd_pitch": psd_pitch,
    }


def correct_motion_ripple(
    img: np.ndarray,
    method: str = "adaptive_destripe",
    strength: float = 0.85,
    window: int | None = None,
    f0: float | None = None,
    bw: float = 0.03,
    r0: int = 0,
    r1: int | None = None,
    meta: list[dict] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """소나 영상에서 파도에 의한 롤링/피칭 리플 효과를 제거 및 보정.

    매개변수:
      img      : 2D uint8 또는 float32 소나 영상 (n_pings, n_samples)
      method   : 'adaptive_destripe' (적응형 디스트라이핑, 추천),
                 'decoupled_roll_pitch' (롤/피치 성분 분리 보정),
                 'fft_notch' (주파수 대역차단 노치 필터)
      strength : 보정 강도 (0.0 ~ 1.0)
      window   : 이동 윈도우 크기 (핑 단위, None 시 파랑 주기에 자동 연동)
      f0       : 노치 중심 주파수 (cycles/ping)
      bw       : 노치 대역폭
      r0, r1   : 보정 적용할 핑 행 범위 (None 시 전체)
      meta     : 핑 메타데이터

    반환:
      (corr_img, ripple_pattern): 보정된 uint8 영상과 추출된 리플 노이즈 성분
    """
    n_pings, w = img.shape[:2]
    r0 = max(0, int(r0))
    r1 = n_pings if r1 is None else min(n_pings, int(r1))
    strength = min(1.0, max(0.0, float(strength)))

    sub_orig = img[r0:r1].astype(np.float32)
    n_sub = sub_orig.shape[0]
    if n_sub < 4 or strength <= 0.0:
        return img.copy(), np.zeros_like(img)

    # 윈도우 크기 기본값 (리플 주기의 약 1.5 ~ 2배, 기본 27)
    if window is None or window < 3:
        w_filt = 27
    else:
        w_filt = int(window)
    if w_filt % 2 == 0:
        w_filt += 1
    w_filt = min(w_filt, n_sub if n_sub % 2 == 1 else n_sub - 1)

    ripple_layer = np.zeros_like(sub_orig)

    if method == "decoupled_roll_pitch":
        # 1. 2D 롤/피치 성분 분리 보정 (물리적 대칭/반대칭 모델 기반)
        mid = w // 2
        if meta and len(meta) > r0:
            m0 = meta[r0]
            np_ = int(m0.get("n_port") or mid)
            if 0 < np_ < w:
                mid = np_

        # 2D 컬럼별 저주파 기저 신호 (이동평균)
        n = sub_orig.shape[0]
        c = np.vstack([np.zeros((1, w), dtype=np.float32), np.cumsum(sub_orig, axis=0)])
        hi = np.minimum(np.arange(n) + w_filt // 2 + 1, n)
        lo = np.maximum(np.arange(n) - w_filt // 2, 0)
        cnt = (hi - lo)[:, None].astype(np.float32)
        lp = (c[hi] - c[lo]) / np.maximum(cnt, 1.0)
        R_total = sub_orig - lp

        half = min(mid, w - mid)
        p_idx = np.arange(mid - 1, mid - 1 - half, -1)
        s_idx = np.arange(mid, mid + half)

        P_rip = R_total[:, p_idx]
        S_rip = R_total[:, s_idx]

        # 대칭(Pitching/Heave) & 반대칭(Rolling) 성분 분해
        R_pitch = 0.5 * (S_rip + P_rip)
        R_roll = 0.5 * (S_rip - P_rip)

        ripple_layer = R_total.copy()
        ripple_layer[:, s_idx] = R_pitch + R_roll
        ripple_layer[:, p_idx] = R_pitch - R_roll

    elif method == "fft_notch":
        # 2. 2D FFT 노치 필터링 (기본파 및 2차 고조파 대역 차단)
        if f0 is None or f0 <= 0:
            f0 = 0.045
        bw = max(0.012, f0 * 0.75)

        fft = np.fft.rfft(sub_orig, axis=0)
        freqs = np.fft.rfftfreq(n_sub)

        # 기본파(f0) 및 2차 고조파(2*f0) 가우시안 밴드스톱 노치 프로파일
        notch1 = 1.0 - np.exp(-0.5 * ((freqs - f0) / (bw * 0.5)) ** 2)
        notch2 = 1.0 - np.exp(-0.5 * ((freqs - 2.0 * f0) / (bw * 0.5)) ** 2)
        notch = np.minimum(notch1, notch2)
        notch = (1.0 - strength) + strength * notch
        filtered_fft = fft * notch[:, None]

        sub_clean = np.fft.irfft(filtered_fft, n=n_sub, axis=0)
        ripple_layer = sub_orig - sub_clean

    else:
        # 3. 적응형 컬럼별 디스트라이핑 (순수 NumPy 고속 이동평균 필터)
        n = sub_orig.shape[0]
        c = np.vstack([np.zeros((1, w), dtype=np.float32), np.cumsum(sub_orig, axis=0)])
        hi = np.minimum(np.arange(n) + w_filt // 2 + 1, n)
        lo = np.maximum(np.arange(n) - w_filt // 2, 0)
        cnt = (hi - lo)[:, None].astype(np.float32)
        lp = (c[hi] - c[lo]) / np.maximum(cnt, 1.0)

        ripple_layer = sub_orig - lp

    # 최종 보정 적용
    corr_sub = sub_orig - strength * ripple_layer
    corr_sub = np.clip(corr_sub, 0, 255).astype(np.uint8)

    # 전체 이미지 복사본에 ROI 반영
    out_img = img.copy()
    out_img[r0:r1] = corr_sub

    # 시각화용 추출 리플 노이즈 (명암 대비 최적화)
    rip_amp = float(np.percentile(np.abs(ripple_layer), 95))
    scale = 55.0 / max(rip_amp, 1.0)
    full_ripple = np.zeros_like(img, dtype=np.float32)
    full_ripple[r0:r1] = ripple_layer * (strength * scale)

    return out_img, np.clip(full_ripple + 128.0, 0, 255).astype(np.uint8)
