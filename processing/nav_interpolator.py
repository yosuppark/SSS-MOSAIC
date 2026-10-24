# -*- coding: utf-8 -*-
r"""nav_interpolator.py — GPS 항법 자료 스파이크 제거, 위치/헤딩 스무딩 및 핑(Ping)별 보간 엔진

XTF 파일의 GPS 정보는 보통 1초에 1회(1 Hz) 갱신되는 반면,
사이드스캔 소나의 핑은 1초에 10~30회(10~30 Hz) 이상 기록됩니다.
이로 인해 1초 동안 동일한 GPS 좌표와 Heading이 기록되어 모자이크 생성 시 계단식 점핑 현상이 발생합니다.
또한 소나 장비에 정밀 Heading 센서가 없는 경우, GPS Course를 기반으로 헤딩을 산출하되
고주파 지터와 각도 점프를 제거한 '부드럽고 점진적인 회전(Smooth & Progressive Variation)'이 필수적입니다.

본 모듈의 핵심 기능:
  1. 시간축 및 서브세컨드 해상도 복원 / Ping Update Rate 계산
  2. 비정상 좌표(0, NaN) 및 비정상 속도/거리 스파이크 검출 및 복원
  3. 로컬 평면(m) 투영 기반 기준 위치 스무딩
  4. 인접 픽스 간 선속(Speed) 및 진행방향(Course) 계산
  5. 핑간 시간을 반영한 매 핑별 위치(Hermite Spline / Linear) 정밀 보간
  6. 단위 방향 벡터 $(\sin\theta, \cos\theta)$ 가우시안 스무딩을 통한 $360^\circ$ 래핑 오류 없는 헤딩 보간
  7. 생선 가시(Fishbone = 좌우 빔 방향 선분) 좌표 산출 헬퍼 제공
"""
from __future__ import annotations

import re
import math
from datetime import datetime
import numpy as np


# ==============================================================================
# 1. 시간 파싱 및 타임라인 생성
# ==============================================================================

_TIME_RE = re.compile(
    r'(?:(\d{4})-(\d{2})-(\d{2})\s+)?(\d{1,2}):(\d{1,2}):(\d{1,2})(?:\.(\d+))?'
)

def parse_time_to_seconds(time_str: str) -> float | None:
    """'YYYY-MM-DD HH:MM:SS.mmm' 또는 'HH:MM:SS.mmm' 문자열을 자정 이후 초(float)로 변환."""
    if not time_str or not isinstance(time_str, str):
        return None
    m = _TIME_RE.match(time_str.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, frac = m.groups()
    try:
        h = int(hour)
        mi = int(minute)
        s = int(second)
        f_sec = 0.0
        if frac:
            f_sec = float("0." + frac)
        sec = h * 3600.0 + mi * 60.0 + s + f_sec
        if year and month and day:
            dt = datetime(int(year), int(month), int(day))
            sec += (dt - datetime(int(year), 1, 1)).total_seconds()
        return sec
    except Exception:
        return None


def build_continuous_timeline(meta: list[dict]) -> tuple[np.ndarray, dict]:
    """메타데이터 목록으로부터 서브세컨드 해상도의 단조 증가 시간 배열(초)과 Ping Rate 통계를 산출."""
    n = len(meta)
    if n == 0:
        return np.zeros(0, dtype=np.float64), {
            "duration_sec": 0.0, "avg_ping_rate": 0.0,
            "gps_updates": 0, "gps_update_rate": 0.0, "pings_per_update": 0.0
        }

    raw_times = np.zeros(n, dtype=np.float64)
    has_valid_time = False

    for i, m in enumerate(meta):
        t_val = parse_time_to_seconds(m.get("time", ""))
        if t_val is not None:
            raw_times[i] = t_val
            has_valid_time = True
        else:
            raw_times[i] = np.nan

    if not has_valid_time or np.all(np.isnan(raw_times)):
        dt_default = 0.05
        timeline = np.arange(n, dtype=np.float64) * dt_default
        return timeline, {
            "duration_sec": float((n - 1) * dt_default),
            "avg_ping_rate": 20.0,
            "gps_updates": max(1, int(n // 20)),
            "gps_update_rate": 1.0,
            "pings_per_update": 20.0
        }

    # NaN 채우기
    valid_idx = np.where(np.isfinite(raw_times))[0]
    if len(valid_idx) < n:
        if len(valid_idx) == 0:
            raw_times = np.arange(n, dtype=np.float64) * 0.05
        elif len(valid_idx) == 1:
            raw_times[:] = raw_times[valid_idx[0]] + (np.arange(n) - valid_idx[0]) * 0.05
        else:
            raw_times = np.interp(np.arange(n), valid_idx, raw_times[valid_idx])

    timeline = raw_times.copy()
    for i in range(1, n):
        if timeline[i] < timeline[i - 1]:
            timeline[i] = timeline[i - 1]

    # 동일 시각 구간 균등 분배
    i = 0
    while i < n:
        j = i
        while j < n and timeline[j] == timeline[i]:
            j += 1
        count = j - i
        if count > 1:
            if j < n:
                dt_block = timeline[j] - timeline[i]
                if dt_block <= 0.0:
                    dt_block = 1.0
            elif i > 0:
                dt_block = timeline[i] - timeline[i - 1]
                if dt_block <= 0.0:
                    dt_block = 1.0
            else:
                dt_block = 1.0

            step = dt_block / float(count)
            for k in range(count):
                timeline[i + k] = timeline[i] + k * step
        i = j

    duration = float(timeline[-1] - timeline[0])
    if duration <= 1e-4:
        duration = float(max((n - 1) * 0.05, 0.05))
        timeline = timeline[0] + np.arange(n, dtype=np.float64) * (duration / max(n - 1, 1))

    avg_ping_rate = float((n - 1) / duration) if duration > 0 else 0.0

    xs = np.array([m.get("x", 0.0) for m in meta], dtype=np.float64)
    ys = np.array([m.get("y", 0.0) for m in meta], dtype=np.float64)
    changed = np.ones(n, dtype=bool)
    if n > 1:
        changed[1:] = (xs[1:] != xs[:-1]) | (ys[1:] != ys[:-1])
    gps_updates = int(np.sum(changed))
    gps_update_rate = float(gps_updates / duration) if duration > 0 else 1.0
    pings_per_update = float(n / max(gps_updates, 1))

    stats = {
        "duration_sec": duration,
        "avg_ping_rate": avg_ping_rate,
        "gps_updates": gps_updates,
        "gps_update_rate": gps_update_rate,
        "pings_per_update": pings_per_update,
    }
    return timeline, stats


# ==============================================================================
# 2. 로컬 평면 좌표계 (WGS84 Equidistant Projection in meters)
# ==============================================================================

def lonlat_to_local_meters(lons: np.ndarray, lats: np.ndarray, ref_lon: float, ref_lat: float) -> tuple[np.ndarray, np.ndarray]:
    """경위도(deg)를 기준점 기준의 로컬 평면 미터 좌표(Easting, Northing)로 변환."""
    rad_lat = math.radians(ref_lat)
    m_per_deg_lat = 111132.954 - 559.822 * math.cos(2 * rad_lat) + 1.175 * math.cos(4 * rad_lat)
    m_per_deg_lon = (111412.84 * math.cos(rad_lat) - 93.5 * math.cos(3 * rad_lat))

    east = (lons - ref_lon) * m_per_deg_lon
    north = (lats - ref_lat) * m_per_deg_lat
    return east, north


def local_meters_to_lonlat(east: np.ndarray, north: np.ndarray, ref_lon: float, ref_lat: float) -> tuple[np.ndarray, np.ndarray]:
    """로컬 평면 미터 좌표(Easting, Northing)를 WGS84 경위도(deg)로 역변환."""
    rad_lat = math.radians(ref_lat)
    m_per_deg_lat = 111132.954 - 559.822 * math.cos(2 * rad_lat) + 1.175 * math.cos(4 * rad_lat)
    m_per_deg_lon = (111412.84 * math.cos(rad_lat) - 93.5 * math.cos(3 * rad_lat))

    lons = ref_lon + east / max(m_per_deg_lon, 1e-6)
    lats = ref_lat + north / max(m_per_deg_lat, 1e-6)
    return lons, lats


# ==============================================================================
# 3. 스파이크 검출 및 이상치 복원 (Spike Detection & Outlier Removal)
# ==============================================================================

def detect_and_clean_spikes(
    lons: np.ndarray,
    lats: np.ndarray,
    times: np.ndarray,
    max_speed_mps: float = 12.0,
    mad_threshold: float = 4.5,
) -> tuple[np.ndarray, np.ndarray, int]:
    """터무니없는 좌표(0, NaN) 및 비정상 속도/거리 스파이크를 검출하고 보간하여 복원."""
    n = len(lons)
    if n < 3:
        return lons.copy(), lats.copy(), 0

    invalid = (
        ~np.isfinite(lons) | ~np.isfinite(lats) |
        (np.abs(lons) < 1e-6) & (np.abs(lats) < 1e-6) |
        (np.abs(lons) > 180.0) | (np.abs(lats) > 90.0)
    )

    valid_indices = np.where(~invalid)[0]
    if len(valid_indices) == 0:
        return lons.copy(), lats.copy(), 0

    ref_lon = float(np.median(lons[valid_indices]))
    ref_lat = float(np.median(lats[valid_indices]))

    east, north = lonlat_to_local_meters(lons, lats, ref_lon, ref_lat)
    east[invalid] = np.nan
    north[invalid] = np.nan

    idx_arr = np.arange(n)
    if len(valid_indices) < n:
        east[invalid] = np.interp(idx_arr[invalid], valid_indices, east[valid_indices])
        north[invalid] = np.interp(idx_arr[invalid], valid_indices, north[valid_indices])

    spikes = np.zeros(n, dtype=bool)

    dt = np.diff(times)
    dt = np.where(dt > 1e-4, dt, 1e-4)
    de = np.diff(east)
    dn = np.diff(north)
    dist = np.hypot(de, dn)
    speeds = dist / dt

    high_speed = np.where(speeds > max_speed_mps)[0]
    for idx in high_speed:
        w0 = max(0, idx - 10)
        w1 = min(n, idx + 11)
        med_e = np.median(east[w0:w1])
        med_n = np.median(north[w0:w1])
        d0 = math.hypot(east[idx] - med_e, north[idx] - med_n)
        d1 = math.hypot(east[idx + 1] - med_e, north[idx + 1] - med_n)
        if d0 > d1:
            spikes[idx] = True
        else:
            spikes[idx + 1] = True

    win_size = min(21, n if n % 2 == 1 else n - 1)
    if win_size >= 5:
        half_w = win_size // 2
        pad_e = np.pad(east, half_w, mode="edge")
        pad_n = np.pad(north, half_w, mode="edge")
        for i in range(n):
            local_e = pad_e[i : i + win_size]
            local_n = pad_n[i : i + win_size]
            me = np.median(local_e)
            mn = np.median(local_n)
            diffs = np.hypot(local_e - me, local_n - mn)
            mad = np.median(diffs)
            curr_diff = math.hypot(east[i] - me, north[i] - mn)
            if mad > 1e-3 and curr_diff > max(mad * mad_threshold, 30.0):
                spikes[i] = True

    all_bad = invalid | spikes
    spike_count = int(np.sum(all_bad))

    good_indices = np.where(~all_bad)[0]
    if len(good_indices) >= 2:
        clean_east = np.interp(idx_arr, good_indices, east[good_indices])
        clean_north = np.interp(idx_arr, good_indices, north[good_indices])
    elif len(good_indices) == 1:
        clean_east = np.full(n, east[good_indices[0]])
        clean_north = np.full(n, north[good_indices[0]])
    else:
        clean_east, clean_north = east, north

    clean_lons, clean_lats = local_meters_to_lonlat(clean_east, clean_north, ref_lon, ref_lat)
    return clean_lons, clean_lats, spike_count


# ==============================================================================
# 4. 기준 위치 스무딩 및 필터링
# ==============================================================================

def gaussian_smooth_1d(arr: np.ndarray, sigma_pts: float) -> np.ndarray:
    """1차원 가우시안 필터 (반사 패딩, scipy 비의존 순수 numpy)."""
    if sigma_pts <= 0.5 or len(arr) < 3:
        return arr.copy()
    radius = int(math.ceil(3.0 * sigma_pts))
    kernel_x = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (kernel_x / sigma_pts) ** 2)
    kernel /= kernel.sum()

    padded = np.pad(arr, radius, mode="reflect")
    smoothed = np.convolve(padded, kernel, mode="same")
    return smoothed[radius : radius + len(arr)]


def smooth_trajectory_meters(
    east: np.ndarray,
    north: np.ndarray,
    times: np.ndarray,
    smooth_window_sec: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """로컬 평면 좌표를 가우시안 스무딩하여 GPS 지터/노이즈를 제거한 기준 위치 생성."""
    n = len(east)
    if n < 3 or smooth_window_sec <= 0.0:
        return east.copy(), north.copy()

    total_time = times[-1] - times[0]
    if total_time <= 1e-4:
        return east.copy(), north.copy()

    avg_hz = (n - 1) / total_time
    sigma_pts = max(1.0, (smooth_window_sec * avg_hz) / 4.0)

    sm_east = gaussian_smooth_1d(east, sigma_pts)
    sm_north = gaussian_smooth_1d(north, sigma_pts)
    return sm_east, sm_north


# ==============================================================================
# 5. 선속/방향 계산 및 핑별 위치 보간
# ==============================================================================

def hermite_interpolate_vector(
    t_nodes: np.ndarray,
    p_nodes: np.ndarray,
    t_query: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Cubic Hermite Spline 보간: 위치 및 1차 미분(속도 벡터)을 동시에 계산."""
    m = len(t_nodes)
    n = len(t_query)
    if m < 2:
        pos = np.repeat(p_nodes[:1], n, axis=0)
        vel = np.zeros((n, 2), dtype=np.float64)
        return pos, vel

    v_nodes = np.zeros_like(p_nodes)
    dt_nodes = np.diff(t_nodes)
    dt_nodes = np.where(dt_nodes > 1e-5, dt_nodes, 1e-5)

    dp_nodes = np.diff(p_nodes, axis=0)
    sec_slopes = dp_nodes / dt_nodes[:, None]

    if m == 2:
        v_nodes[0] = sec_slopes[0]
        v_nodes[1] = sec_slopes[0]
    else:
        v_nodes[0] = sec_slopes[0]
        v_nodes[-1] = sec_slopes[-1]
        for k in range(1, m - 1):
            h0 = dt_nodes[k - 1]
            h1 = dt_nodes[k]
            v_nodes[k] = (h1 * sec_slopes[k - 1] + h0 * sec_slopes[k]) / (h0 + h1)

    indices = np.searchsorted(t_nodes, t_query, side="right") - 1
    indices = np.clip(indices, 0, m - 2)

    pos_out = np.zeros((n, 2), dtype=np.float64)
    vel_out = np.zeros((n, 2), dtype=np.float64)

    for k in range(m - 1):
        mask = (indices == k)
        if not np.any(mask):
            continue
        h = dt_nodes[k]
        t0 = t_nodes[k]
        u = (t_query[mask] - t0) / h
        u = np.clip(u, 0.0, 1.0)
        u2 = u * u
        u3 = u2 * u

        h00 = 2 * u3 - 3 * u2 + 1
        h10 = u3 - 2 * u2 + u
        h01 = -2 * u3 + 3 * u2
        h11 = u3 - u2

        dh00 = (6 * u2 - 6 * u) / h
        dh10 = (3 * u2 - 4 * u + 1)
        dh01 = (-6 * u2 + 6 * u) / h
        dh11 = (3 * u2 - 2 * u)

        pk = p_nodes[k]
        pk1 = p_nodes[k + 1]
        vk = v_nodes[k]
        vk1 = v_nodes[k + 1]

        pos_out[mask] = (
            h00[:, None] * pk +
            h10[:, None] * (h * vk) +
            h01[:, None] * pk1 +
            h11[:, None] * (h * vk1)
        )
        vel_out[mask] = (
            dh00[:, None] * pk +
            dh10[:, None] * vk +
            dh01[:, None] * pk1 +
            dh11[:, None] * vk1
        )

    return pos_out, vel_out


# ==============================================================================
# 6. 헤딩 스무딩 핵심: 단위 벡터 2차 가우시안 필터링 (점진적 회전 보장)
# ==============================================================================

def calculate_smooth_cog(
    east: np.ndarray,
    north: np.ndarray,
    times: np.ndarray,
    heading_smooth_sec: float = 5.0,
) -> np.ndarray:
    """스무딩된 위치 시계열로부터 단위 접선 방향 벡터를 산출하고 2차 가우시안 필터링을 통해
    노이즈가 완전히 제거된 매우 부드럽고 점진적인 Course Over Ground (0~360 deg) 계산.

    360도 경계 점프(wrap-around)가 수학적으로 완벽히 회피되며 핑간 변화율이 0.1~0.3도로 매끄럽습니다.
    """
    n = len(east)
    if n < 3:
        return np.zeros(n, dtype=np.float64)

    dt_total = times[-1] - times[0]
    avg_hz = (n - 1) / max(dt_total, 1e-4)

    # 1. 국소 차분 스텝 (약 0.5초 상당)
    step = max(1, int(round(avg_hz * 0.5)))
    pad_e = np.pad(east, step, mode="edge")
    pad_n = np.pad(north, step, mode="edge")
    de = pad_e[2 * step :] - pad_e[: -2 * step]
    dn = pad_n[2 * step :] - pad_n[: -2 * step]

    # 2. 단위 방향 벡터화
    mag = np.hypot(de, dn)
    good = mag > 1e-4

    ux = np.zeros(n, dtype=np.float64)
    uy = np.zeros(n, dtype=np.float64)
    ux[good] = de[good] / mag[good]
    uy[good] = dn[good] / mag[good]

    if not np.all(good):
        good_idx = np.where(good)[0]
        if len(good_idx) > 0:
            all_idx = np.arange(n)
            ux[~good] = np.interp(all_idx[~good], good_idx, ux[good_idx])
            uy[~good] = np.interp(all_idx[~good], good_idx, uy[good_idx])
        else:
            uy[:] = 1.0  # 기본 북쪽

    # 3. 단위 방향 벡터에 가우시안 스무딩 적용
    sigma_hdg = max(1.0, (heading_smooth_sec * avg_hz) / 4.0)
    sm_ux = gaussian_smooth_1d(ux, sigma_hdg)
    sm_uy = gaussian_smooth_1d(uy, sigma_hdg)

    # 4. 각도 복원 (0 ~ 360 deg)
    cog_rad = np.arctan2(sm_ux, sm_uy)
    cog_deg = np.degrees(cog_rad) % 360.0
    return cog_deg


def smooth_sensor_heading(
    headings_raw: np.ndarray,
    times: np.ndarray,
    heading_smooth_sec: float = 5.0,
) -> np.ndarray | None:
    """센서 헤딩에서 0값/결측치를 제거하고 단위 벡터 스무딩을 통해 부드러운 헤딩 생성."""
    n = len(headings_raw)
    valid_h = np.isfinite(headings_raw) & (headings_raw > 0.0) & (headings_raw <= 360.0)
    if np.sum(valid_h) < 3:
        return None

    rads = np.radians(headings_raw)
    ux = np.sin(rads)
    uy = np.cos(rads)

    idx_all = np.arange(n)
    idx_good = np.where(valid_h)[0]
    ux_interp = np.interp(idx_all, idx_good, ux[idx_good])
    uy_interp = np.interp(idx_all, idx_good, uy[idx_good])

    dt_total = times[-1] - times[0]
    avg_hz = (n - 1) / max(dt_total, 1e-4)
    sigma_hdg = max(1.0, (heading_smooth_sec * avg_hz) / 4.0)

    sm_ux = gaussian_smooth_1d(ux_interp, sigma_hdg)
    sm_uy = gaussian_smooth_1d(uy_interp, sigma_hdg)

    return np.degrees(np.arctan2(sm_ux, sm_uy)) % 360.0


# ==============================================================================
# 7. 생선 가시(Fishbone) 빔 좌표 산출 헬퍼
# ==============================================================================

def compute_fishbone_rays(
    east: np.ndarray,
    north: np.ndarray,
    headings: np.ndarray,
    ray_length_m: float = 30.0,
    stride: int = 1,
    range_port: np.ndarray | None = None,
    range_stbd: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """항적 궤적과 각 핑의 헤딩으로부터 좌우 생선 가시(Starboard/Port 빔 선분) 좌표를 계산.
    range_port / range_stbd가 주어지면 파일의 실제 비대칭 슬랜트 레인지를 적용.

    반환: (sub_east, sub_north, port_east, port_north, stbd_east, stbd_north)
    """
    sub_e = east[::stride]
    sub_n = north[::stride]
    sub_hd = headings[::stride]

    rad = np.radians(sub_hd)
    sin_h = np.sin(rad)
    cos_h = np.cos(rad)

    if range_port is not None and len(range_port) == len(east):
        len_p = np.asarray(range_port[::stride], dtype=np.float64)
    else:
        len_p = np.full(len(sub_e), ray_length_m, dtype=np.float64)

    if range_stbd is not None and len(range_stbd) == len(east):
        len_s = np.asarray(range_stbd[::stride], dtype=np.float64)
    else:
        len_s = np.full(len(sub_e), ray_length_m, dtype=np.float64)

    # 유효하지 않은 길이는 ray_length_m으로 대체
    len_p = np.where(len_p > 0, len_p, ray_length_m)
    len_s = np.where(len_s > 0, len_s, ray_length_m)

    # 우현(+90도): (cos_h, -sin_h)
    # 좌현(-90도): (-cos_h, sin_h)
    stbd_e = sub_e + len_s * cos_h
    stbd_n = sub_n - len_s * sin_h
    port_e = sub_e - len_p * cos_h
    port_n = sub_n + len_p * sin_h

    return sub_e, sub_n, port_e, port_n, stbd_e, stbd_n


# ==============================================================================
# 8. 전체 네비게이션 보정 파이프라인
# ==============================================================================

def process_navigation(
    meta: list[dict],
    spike_filter: bool = True,
    max_speed_mps: float = 12.0,
    smooth_window_sec: float = 3.0,
    heading_smooth_sec: float = 5.0,
    interp_method: str = "hermite",
    heading_mode: str = "cog",  # "cog", "sensor", "auto"
) -> tuple[dict, dict]:
    """XTF 핑 메타데이터 리스트의 항법 정보를 보정하고 매 핑별 위치/헤딩을 보간.

    heading_mode:
      - "cog"    : GPS 진행방향을 단위벡터 스무딩하여 매우 유려하고 점진적인 헤딩 산출 (헤딩 센서 부재 시 권장)
      - "sensor" : XTF 내 원본 센서 헤딩의 0/노이즈를 복원하고 단위벡터 스무딩 적용
      - "auto"   : 센서 헤딩 상태를 자동 진단하여 유효하면 센서 스무딩, 무효하면 COG 스무딩 적용
    """
    n = len(meta)
    if n == 0:
        return {}, {}

    times, time_stats = build_continuous_timeline(meta)

    # 원본 raw 항법 데이터 추출 (사이드카/보간된 meta가 전달되어도 원본 보존값 우선 참조)
    lons_raw = np.array([float(m.get("raw_x") if m.get("raw_x") is not None else m.get("x", 0.0)) for m in meta], dtype=np.float64)
    lats_raw = np.array([float(m.get("raw_y") if m.get("raw_y") is not None else m.get("y", 0.0)) for m in meta], dtype=np.float64)
    hdg_raw = np.array([float(m.get("raw_heading") if m.get("raw_heading") is not None else m.get("heading", 0.0)) for m in meta], dtype=np.float64)

    # 1. 스파이크 검출 및 복원
    if spike_filter:
        clean_lons, clean_lats, spike_count = detect_and_clean_spikes(
            lons_raw, lats_raw, times, max_speed_mps=max_speed_mps
        )
    else:
        clean_lons, clean_lats, spike_count = lons_raw.copy(), lats_raw.copy(), 0

    ref_lon = float(np.median(clean_lons))
    ref_lat = float(np.median(clean_lats))

    # 2. 로컬 평면 투영 및 기준 궤적 스무딩
    east, north = lonlat_to_local_meters(clean_lons, clean_lats, ref_lon, ref_lat)
    sm_east, sm_north = smooth_trajectory_meters(east, north, times, smooth_window_sec)

    # 3. 고유 GPS Fix 노드 추출 (1초 단위 GPS 갱신점, 유효 좌표만 선별)
    valid_fix = (lons_raw > 10.0) & (lats_raw > 10.0) & np.isfinite(lons_raw) & np.isfinite(lats_raw)
    changed = np.zeros(n, dtype=bool)
    if n > 1:
        changed[1:] = (lons_raw[1:] != lons_raw[:-1]) | (lats_raw[1:] != lats_raw[:-1])
    changed[0] = True
    changed = changed & valid_fix
    node_idx = np.where(changed)[0]

    # 만약 meta가 이미 매 핑마다 보간되어 node_idx가 비정상적으로 과다한 경우(> 50%),
    # 실제 1초 ping rate를 기준으로 1초 노드 간격을 안전하게 재추출
    if len(node_idx) > 0.5 * n and n > 20:
        step = max(1, int(round(time_stats.get("avg_ping_rate", 10.0))))
        node_idx = np.arange(0, n, step)
        if node_idx[-1] != n - 1:
            node_idx = np.append(node_idx, n - 1)
    elif len(node_idx) < 2:
        step = max(1, int(round(time_stats.get("avg_ping_rate", 10.0))))
        node_idx = np.arange(0, n, step)
        if node_idx[-1] != n - 1:
            node_idx = np.append(node_idx, n - 1)

    t_nodes = times[node_idx]
    p_nodes = np.column_stack([sm_east[node_idx], sm_north[node_idx]])

    # 4. 각 핑별 위치 정밀 보간
    if interp_method == "hermite" and len(node_idx) >= 2:
        pos_interp, vel_interp = hermite_interpolate_vector(t_nodes, p_nodes, times)
        interp_east = pos_interp[:, 0]
        interp_north = pos_interp[:, 1]
        speeds = np.hypot(vel_interp[:, 0], vel_interp[:, 1])
    else:
        interp_east = np.interp(times, t_nodes, p_nodes[:, 0])
        interp_north = np.interp(times, t_nodes, p_nodes[:, 1])
        dt = np.diff(times)
        dt = np.where(dt > 1e-4, dt, 1e-4)
        speeds = np.zeros(n, dtype=np.float64)
        v_seg = np.hypot(np.diff(interp_east), np.diff(interp_north)) / dt
        speeds[:-1] = v_seg
        speeds[-1] = v_seg[-1] if len(v_seg) > 0 else 0.0

    # 5. 헤딩 산출 및 단위벡터 점진적 스무딩
    valid_sensor = (hdg_raw > 0.0) & (hdg_raw <= 360.0)
    is_sensor_usable = (np.sum(valid_sensor) >= 0.5 * n) and (np.std(hdg_raw[valid_sensor]) > 1.0)

    mode_to_use = heading_mode.lower()
    if mode_to_use == "auto":
        mode_to_use = "sensor" if is_sensor_usable else "cog"

    if mode_to_use == "sensor" and is_sensor_usable:
        sm_sensor = smooth_sensor_heading(hdg_raw, times, heading_smooth_sec)
        if sm_sensor is not None:
            final_hdg = sm_sensor
            applied_mode = "sensor"
        else:
            final_hdg = calculate_smooth_cog(interp_east, interp_north, times, heading_smooth_sec)
            applied_mode = "cog(fallback)"
    else:
        # GPS Course 산출 (기본 권장)
        final_hdg = calculate_smooth_cog(interp_east, interp_north, times, heading_smooth_sec)
        applied_mode = "cog"

    # 6. 경위도 복원
    final_lons, final_lats = local_meters_to_lonlat(interp_east, interp_north, ref_lon, ref_lat)

    # 핑당 각도 변화율 평가
    diff_deg = np.abs(np.diff(np.unwrap(np.radians(final_hdg))))
    mean_hdg_change = float(np.degrees(np.mean(diff_deg))) if len(diff_deg) > 0 else 0.0
    max_hdg_change = float(np.degrees(np.max(diff_deg))) if len(diff_deg) > 0 else 0.0

    valid_pts = (lons_raw > 10.0) & (lats_raw > 10.0) & np.isfinite(lons_raw) & np.isfinite(lats_raw)
    disp_lons = np.where(valid_pts, lons_raw, clean_lons)
    disp_lats = np.where(valid_pts, lats_raw, clean_lats)
    raw_east, raw_north = lonlat_to_local_meters(disp_lons, disp_lats, ref_lon, ref_lat)
    avg_speed = float(np.mean(speeds)) if len(speeds) > 0 else 0.0

    stats = dict(time_stats)
    stats.update({
        "pings": n,
        "spikes_detected": spike_count,
        "avg_speed_mps": avg_speed,
        "avg_speed_knots": avg_speed * 1.94384,
        "smooth_window_sec": smooth_window_sec,
        "heading_smooth_sec": heading_smooth_sec,
        "interp_method": interp_method,
        "applied_heading_mode": applied_mode,
        "mean_hdg_change_deg": mean_hdg_change,
        "max_hdg_change_deg": max_hdg_change,
    })

    # 보간 전 원본 1초 GPS 픽스 기반 계단식 COG 시계열 생성 (시계열 비교 플롯용)
    raw_cog_step = np.zeros(n, dtype=np.float64)
    last_valid_cog = 0.0
    for k in range(len(node_idx) - 1):
        i0, i1 = node_idx[k], node_idx[k + 1]
        de_node = raw_east[i1] - raw_east[i0]
        dn_node = raw_north[i1] - raw_north[i0]
        if math.hypot(de_node, dn_node) > 0.05:
            last_valid_cog = float(np.degrees(np.arctan2(de_node, dn_node)) % 360.0)
        raw_cog_step[i0:i1] = last_valid_cog
    if len(node_idx) > 1:
        raw_cog_step[node_idx[-1]:] = last_valid_cog
    if len(node_idx) > 0 and node_idx[0] > 0:
        raw_cog_step[:node_idx[0]] = raw_cog_step[node_idx[0]]

    # 모드에 따른 보간 전 플롯 헤딩
    if applied_mode == "cog":
        raw_plot_hdg = raw_cog_step
    else:
        raw_plot_hdg = hdg_raw.copy()

    # 슬랜트 레인지 배열
    rp_arr = np.array([float(m.get("range_port") or m.get("slant_range") or 0.0) for m in meta], dtype=np.float64)
    rs_arr = np.array([float(m.get("range_stbd") or m.get("slant_range") or 0.0) for m in meta], dtype=np.float64)
    def_sr = float(np.nanmedian(np.maximum(rp_arr, rs_arr))) if n > 0 else 50.0
    if def_sr <= 0:
        def_sr = 50.0

    results = {
        "lons": final_lons,
        "lats": final_lats,
        "headings": final_hdg,
        "speeds": speeds,
        "times": times,
        "east": interp_east,
        "north": interp_north,
        "raw_east": raw_east,
        "raw_north": raw_north,
        "raw_headings": raw_plot_hdg,
        "raw_sensor_headings": hdg_raw,
        "raw_plot_headings": raw_plot_hdg,
        "raw_node_idx": node_idx,
        "range_port": rp_arr,
        "range_stbd": rs_arr,
        "default_slant_range": def_sr,
        "ref_lon": ref_lon,
        "ref_lat": ref_lat,
    }
    return results, stats


def apply_interpolated_nav_to_meta(meta: list[dict], results: dict) -> None:
    """보간된 항법 결과를 meta 리스트 딕셔너리에 직접(in-place) 덮어씌움."""
    lons = results["lons"]
    lats = results["lats"]
    hdgs = results["headings"]
    spds = results["speeds"]

    for i, m in enumerate(meta):
        if "raw_x" not in m:
            m["raw_x"] = float(m.get("x", 0.0))
            m["raw_y"] = float(m.get("y", 0.0))
            m["raw_heading"] = float(m.get("heading", 0.0))
            m["raw_speed"] = float(m.get("speed", 0.0))
        if i < len(lons):
            m["x"] = float(lons[i])
            m["y"] = float(lats[i])
            m["heading"] = float(hdgs[i])
            m["speed"] = float(spds[i])
