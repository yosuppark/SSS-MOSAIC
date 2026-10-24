# -*- coding: utf-8 -*-
"""
Created on Mon Jul 27 15:59:42 2026

@author: swath
"""

"""XTF 파일 로더 (pyxtf 기반)
 - Port + Starboard 채널을 연결해 핑 1개 = 1행인 그레이스케일 배열 생성
 - Port 채널은 역순 저장 가능성이 있어 뒤집어 표시 (nadir가 중앙에 오도록)
 - 반환 meta 키:
     ping, time, x(경도), y(위도), heading, altitude,
     slant_range, n_port, n_stbd
   → main.py(항적/Swath), waterfall_view.py(Bottom Track)에서 사용
"""
import numpy as np
import pyxtf
from processing.agc import apply_agc

PORT, STBD = 0, 1          # 일반적인 사이드스캔 채널 순서



def load_xtf(path, flip_port=False, swap_channels=False, agc=False, agc_window=301):
    """XTF 파일을 읽어 (img, meta)를 반환.

    flip_port     : Port 채널 샘플을 역순으로 뒤집을지 여부.
    swap_channels : Port와 Starboard 채널 순서를 상호 교환할지 여부 (제조사 채널 순서 상이 대응).

    img  : uint8 2D 배열 (n_pings, n_port + n_stbd), 0~255 그레이스케일
    meta : 핑별 메타데이터 dict 리스트 (핑 인덱스와 1:1 대응)
    """
    fh, packets = pyxtf.xtf_read(path)

    pings = packets.get(pyxtf.XTFHeaderType.sonar, [])
    if not pings:
        raise ValueError("소나 핑 패킷(XTF_HEADER_SONAR)이 없습니다.")

    ch_port = STBD if swap_channels else PORT
    ch_stbd = PORT if swap_channels else STBD

    rows, meta = [], []

    for idx, ping in enumerate(pings):
        # ---- 채널 데이터 추출 (swap_channels 반영) ----
        port = _get_channel(ping, ch_port)
        stbd = _get_channel(ping, ch_stbd)

        # Port 저장 순서는 파일마다 다름 → nadir가 중앙에 오도록 옵션 처리
        if port is not None and flip_port:
            port = port[::-1]

        if port is None and stbd is None:
            continue
        if port is None:
            port = np.zeros(0, dtype=np.float32)
        if stbd is None:
            stbd = np.zeros(0, dtype=np.float32)

        rows.append(np.concatenate([port, stbd]).astype(np.float32))

        # ---- 채널 헤더 SlantRange 추출 ----
        hdrs = ping.ping_chan_headers
        ch_p = hdrs[ch_port] if len(hdrs) > ch_port else None
        ch_s = hdrs[ch_stbd] if len(hdrs) > ch_stbd else None
        rp = float(getattr(ch_p, 'SlantRange', 0.0)) if ch_p else 0.0
        rs = float(getattr(ch_s, 'SlantRange', 0.0)) if ch_s else 0.0
        sr_max = max(rp, rs) if max(rp, rs) > 0 else (rp or rs)

        raw_lon = float(getattr(ping, 'SensorXcoordinate', 0.0))
        raw_lat = float(getattr(ping, 'SensorYcoordinate', 0.0))
        raw_hdg = float(getattr(ping, 'SensorHeading', 0.0))
        raw_spd = float(getattr(ping, 'SensorSpeed', 0.0))

        meta.append({
            'ping_no':     int(getattr(ping, 'PingNumber', idx)),
            'time':        _fmt_time(ping),
            'x':           raw_lon,  # 경도 (보간 시 갱신됨)
            'y':           raw_lat,  # 위도 (보간 시 갱신됨)
            'heading':     raw_hdg,
            'speed':       raw_spd,
            'raw_x':       raw_lon,  # 원본 보존 (보간 후에도 유지)
            'raw_y':       raw_lat,  # 원본 보존
            'raw_heading': raw_hdg,  # 원본 보존
            'raw_speed':   raw_spd,  # 원본 보존
            'altitude':    float(getattr(ping, 'SensorPrimaryAltitude', 0.0)),
            'slant_range': sr_max,
            'range_port':  rp,
            'range_stbd':  rs,
            'n_port':      len(port),
            'n_stbd':      len(stbd),
            'swap_channels': swap_channels,
        })

    if not rows:
        raise ValueError("유효한 소나 데이터가 없습니다.")

    # ---- 핑별 길이 차이 → 최대 폭 기준 0-padding ----
    width = max(len(r) for r in rows)
    img_f = np.zeros((len(rows), width), dtype=np.float32)
    for i, r in enumerate(rows):
        img_f[i, :len(r)] = r

    # ---- 사이드카 바탐(bottom) 정보가 있으면 먼저 동기화 ----
    try:
        from processing.bottom_io import sync_bottom
        sync_bottom(path, meta)
    except Exception:
        pass

    if agc:
        img_f = apply_agc(img_f, meta=meta, window=agc_window)

    # ---- 1~99 percentile 클리핑 → 0~255 uint8 ----
    # padding(0)이 percentile을 왜곡하지 않도록 유효 샘플만 사용
    valid = img_f[img_f > 0]
    if valid.size:
        lo, hi = np.percentile(valid, (1, 99))
    else:
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1.0
    img = np.clip((img_f - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)

    return img, meta

# ------------------------------------------------ 내부 헬퍼
def _get_channel(ping, ch):
    """핑에서 채널 샘플을 1D float32 배열로 추출. 없으면 None."""
    try:
        data = ping.data[ch]
    except (IndexError, TypeError, KeyError):
        return None
    if data is None or len(data) == 0:
        return None
    return np.asarray(data, dtype=np.float32).ravel()


def _fmt_time(ping):
    """핑 헤더의 날짜/시각 필드 → 'YYYY-MM-DD HH:MM:SS.mmm' 문자열."""
    try:
        return (f"{ping.Year:04d}-{ping.Month:02d}-{ping.Day:02d} "
                f"{ping.Hour:02d}:{ping.Minute:02d}:{ping.Second:02d}"
                f".{getattr(ping, 'HSeconds', 0):02d}0")
    except (AttributeError, TypeError):
        return ""