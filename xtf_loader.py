# -*- coding: utf-8 -*-
"""
xtf_loader.py — XTF 파일 로더 (Sidescan Sonar & Norbit MBES Backscatter 지원)
 - 일반 Sidescan Sonar (EdgeTech, Klein 등) 완벽 지원
 - Norbit MBES (iWBMS, WINGHEAD 등) multibeam XTF export 포맷 지원 (Backscatter / Snippet / Bathy)
 - Single composite swath / Dual channel / Multi-channel 자동 감지 및 스마트 매핑
 - raw-byte XTFPingChanHeader 폴백 디코딩 및 음수 dB 백스캐터 정규화
"""
from __future__ import annotations

import math
import struct
import numpy as np
import pyxtf
from processing.agc import apply_agc

# ==================================================== pyxtf Monkey-Patch
# Norbit MBES 및 멀티채널/Bathy XTF 파일에서 pyxtf의 IndexError 및 패킷 누락 방지
def _apply_pyxtf_patch():
    try:
        import pyxtf.xtf_ctypes as xc
        orig_fn = xc.XTFPingHeader.create_from_buffer

        @classmethod
        def _patched_create_from_buffer(cls, buffer, file_header=None):
            if not file_header:
                raise RuntimeError('Initialization of XTFPingHeader from buffer requires file_header to be passed.')

            obj = super(xc.XTFPingHeader, cls).create_from_buffer(buffer=buffer)
            obj.ping_chan_headers = []
            obj.data = None

            # sonar 뿐만 아니라 bathy, snippet 등 채널이 뒤따르는 패킷 지원
            is_channel_packet = (
                obj.HeaderType == xc.XTFHeaderType.sonar or
                (obj.HeaderType in (xc.XTFHeaderType.bathy, xc.XTFHeaderType.bathy_snippet,
                                    xc.XTFHeaderType.reson_7125_snippet, xc.XTFHeaderType.reson_7125)
                 and getattr(obj, 'NumChansToFollow', 0) > 0)
            )

            if is_channel_packet:
                obj.data = []
                bytes_remaining = obj.NumBytesThisRecord - xc.ctypes.sizeof(xc.XTFPingHeader)

                for i in range(0, obj.NumChansToFollow):
                    if bytes_remaining < xc.ctypes.sizeof(xc.XTFPingChanHeader):
                        break
                    p_chan = xc.XTFPingChanHeader.create_from_buffer(buffer=buffer)
                    obj.ping_chan_headers.append(p_chan)
                    bytes_remaining -= xc.ctypes.sizeof(xc.XTFPingChanHeader)

                    # 채널 인포 안전 조회: ChanInfo 또는 sonar_info 또는 채널 번호 매칭
                    ch_info = None
                    ch_num = getattr(p_chan, 'ChannelNumber', i)
                    if hasattr(file_header, 'ChanInfo') and ch_num < len(file_header.ChanInfo):
                        ch_info = file_header.ChanInfo[ch_num]
                    elif hasattr(file_header, 'sonar_info') and i < len(file_header.sonar_info):
                        ch_info = file_header.sonar_info[i]
                    elif hasattr(file_header, 'ChanInfo') and i < len(file_header.ChanInfo):
                        ch_info = file_header.ChanInfo[i]

                    bytes_per_sample = int(getattr(ch_info, 'BytesPerSample', 2) or 2)
                    reserved = int(getattr(ch_info, 'Reserved', 0) or 0)
                    sample_fmt = getattr(ch_info, 'SampleFormat', 0) or 0

                    n_samples = p_chan.NumSamples if p_chan.NumSamples > 0 else reserved
                    n_bytes = n_samples * bytes_per_sample

                    if n_bytes > bytes_remaining:
                        n_bytes = max(0, bytes_remaining // bytes_per_sample * bytes_per_sample)

                    samples = buffer.read(n_bytes)
                    bytes_remaining -= len(samples)

                    try:
                        sample_dtype = xc.sample_format_dtype[sample_fmt]
                    except KeyError:
                        sample_dtype = xc.xtf_dtype.get(bytes_per_sample, np.int16)

                    samples_arr = np.frombuffer(samples, dtype=sample_dtype)
                    obj.data.append(samples_arr)

                # 패킷 잔여 패딩 바이트 소비
                if bytes_remaining > 0:
                    buffer.read(bytes_remaining)

            elif obj.HeaderType == xc.XTFHeaderType.bathy_xyza:
                n_bytes = obj.NumBytesThisRecord - xc.ctypes.sizeof(xc.XTFPingHeader)
                samples = buffer.read(n_bytes)
                num_xyza = n_bytes // xc.ctypes.sizeof(xc.XTFBeamXYZA)
                xyza_array_type = xc.XTFBeamXYZA * num_xyza
                xyza_array_type._pack_ = 1
                obj.data = xyza_array_type.from_buffer_copy(samples)
            elif obj.HeaderType == xc.XTFHeaderType.reson_7018_watercolumn:
                p_chan = xc.XTFPingChanHeader.create_from_buffer(buffer=buffer)
                obj.ping_chan_headers.append(p_chan)
                n_bytes = obj.NumBytesThisRecord - xc.ctypes.sizeof(xc.XTFPingHeader) - xc.ctypes.sizeof(xc.XTFPingChanHeader)
                obj.data = buffer.read(n_bytes)
            elif obj.HeaderType == xc.XTFHeaderType.q_multibeam:
                pass
            else:
                n_bytes = obj.NumBytesThisRecord - xc.ctypes.sizeof(xc.XTFPingHeader)
                obj.data = buffer.read(n_bytes)

            return obj

        xc.XTFPingHeader.create_from_buffer = _patched_create_from_buffer
    except Exception as e:
        print("[xtf_loader] pyxtf 패치 적용 실패 (기본 모드 유지):", e)

_apply_pyxtf_patch()

PORT, STBD = 0, 1          # 기본 사이드스캔 채널 순서

SUPPORTED_PING_PACKET_TYPES = [
    pyxtf.XTFHeaderType.sonar,
    pyxtf.XTFHeaderType.bathy,
    pyxtf.XTFHeaderType.bathy_snippet,
    pyxtf.XTFHeaderType.reson_7125_snippet,
    pyxtf.XTFHeaderType.reson_7125,
    pyxtf.XTFHeaderType.multibeam_raw_beam_angle,
    pyxtf.XTFHeaderType.bathy_xyza,
    pyxtf.XTFHeaderType.q_multibeam,
]


def load_xtf(path, flip_port=False, swap_channels=False, agc=False, agc_window=301):
    """XTF 파일을 읽어 (img, meta)를 반환.

    flip_port     : Port 채널 샘플을 역순으로 뒤집을지 여부.
    swap_channels : Port와 Starboard 채널 순서를 상호 교환할지 여부.

    img  : uint8 2D 배열 (n_pings, n_port + n_stbd), 0~255 그레이스케일
    meta : 핑별 메타데이터 dict 리스트 (핑 인덱스와 1:1 대응)
    """
    fh, packets = pyxtf.xtf_read(path)

    pings, packet_type = _find_pings(packets)
    if not pings:
        raise ValueError("유효한 소나 또는 멀티빔 핑 패킷(XTF_HEADER_SONAR / BATHY 등)이 없습니다.")

    # 채널 매핑 자동 분석 (Norbit MBES Backscatter, Sidescan, Multi-channel 대응)
    mapping = _resolve_channels(fh, pings[0], swap_channels=swap_channels)
    is_single = mapping["is_single_composite"]
    ch_port = mapping["ch_port"]
    ch_stbd = mapping["ch_stbd"]

    rows, meta = [], []

    for idx, ping in enumerate(pings):
        chans = _extract_ping_channels(ping, fh)
        if not chans:
            continue

        port, stbd = None, None
        rp, rs = 0.0, 0.0

        hdrs = getattr(ping, 'ping_chan_headers', [])

        if is_single:
            # 단일 채널인 경우: swath 전체가 1개의 cross-track 백스캐터 라인으로 저장된 경우
            raw_swath = chans[0] if len(chans) > 0 else None
            if raw_swath is None or len(raw_swath) == 0:
                continue
            mid = len(raw_swath) // 2
            if swap_channels:
                port = raw_swath[mid:].copy()
                stbd = raw_swath[:mid].copy()
            else:
                port = raw_swath[:mid].copy()
                stbd = raw_swath[mid:].copy()

            if flip_port and len(port) > 0:
                port = port[::-1]

            ch_hdr = hdrs[0] if len(hdrs) > 0 else None
            sr_val = float(getattr(ch_hdr, 'SlantRange', 0.0)) if ch_hdr else 0.0
            rp = rs = sr_val
        else:
            # 2개 이상의 채널인 경우: Port와 Starboard 채널에서 각각 샘플 추출
            if ch_port < len(chans):
                port = chans[ch_port]
            if ch_stbd < len(chans):
                stbd = chans[ch_stbd]

            if port is not None and flip_port and len(port) > 0:
                port = port[::-1]

            if port is None and stbd is None:
                continue
            if port is None:
                port = np.zeros(0, dtype=np.float32)
            if stbd is None:
                stbd = np.zeros(0, dtype=np.float32)

            ch_p = hdrs[ch_port] if len(hdrs) > ch_port else None
            ch_s = hdrs[ch_stbd] if len(hdrs) > ch_stbd else None
            rp = float(getattr(ch_p, 'SlantRange', 0.0)) if ch_p else 0.0
            rs = float(getattr(ch_s, 'SlantRange', 0.0)) if ch_s else 0.0

        # ---- Slant Range 유효성 검증 및 추정 (MBES export 시 SlantRange=0 대비) ----
        svel = float(getattr(ping, 'ComputedSoundVelocity', 0.0)) or float(getattr(ping, 'SoundVelocity', 0.0)) or 1500.0
        alt = float(getattr(ping, 'SensorPrimaryAltitude', 0.0))
        depth = float(getattr(ping, 'SensorDepth', 0.0))

        if rp <= 0.0:
            ch_p = hdrs[ch_port] if len(hdrs) > ch_port else None
            tdur = float(getattr(ch_p, 'TimeDuration', 0.0)) if ch_p else 0.0
            if tdur > 0.0:
                rp = svel * tdur / 2.0
            elif alt > 0.0:
                rp = alt * 2.5
            elif depth > 0.0:
                rp = depth * 2.5
            else:
                rp = 50.0

        if rs <= 0.0:
            ch_s = hdrs[ch_stbd] if len(hdrs) > ch_stbd else None
            tdur = float(getattr(ch_s, 'TimeDuration', 0.0)) if ch_s else 0.0
            if tdur > 0.0:
                rs = svel * tdur / 2.0
            elif alt > 0.0:
                rs = alt * 2.5
            elif depth > 0.0:
                rs = depth * 2.5
            else:
                rs = 50.0

        sr_max = max(rp, rs) if max(rp, rs) > 0.0 else 50.0

        row_comb = np.concatenate([port, stbd]).astype(np.float32)
        rows.append(row_comb)

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
            'altitude':    alt,
            'depth':       depth,
            'slant_range': sr_max,
            'range_port':  rp,
            'range_stbd':  rs,
            'n_port':      len(port),
            'n_stbd':      len(stbd),
            'swap_channels': swap_channels,
            'packet_type': packet_type.name,
        })

    if not rows:
        raise ValueError("유효한 소나/멀티빔 백스캐터 데이터가 없습니다.")

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

    # ---- 1~99 percentile 클리핑 → 0~255 uint8 정규화 (음수 dB 백스캐터도 지원) ----
    finite_mask = np.isfinite(img_f)
    valid = img_f[finite_mask & (img_f != 0)]
    if not valid.size:
        valid = img_f[finite_mask]
    if valid.size:
        lo, hi = np.percentile(valid, (1, 99))
    else:
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1.0

    img = np.clip((img_f - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)

    return img, meta


# ==================================================== 내부 헬퍼 함수
def _find_pings(packets):
    """지원되는 패킷 타입 중 첫 번째로 존재하는 핑 리스트와 패킷 타입 반환."""
    for ptype in SUPPORTED_PING_PACKET_TYPES:
        pings = packets.get(ptype, [])
        if pings and len(pings) > 0:
            return pings, ptype
    return [], pyxtf.XTFHeaderType.unknown


def _resolve_channels(fh, p0, swap_channels=False):
    """파일 헤더와 첫 핑을 분석하여 Port/Starboard 채널 인덱스를 결정."""
    chans = _extract_ping_channels(p0, fh)
    num_chans = len(chans) if chans else 0

    if num_chans <= 1:
        # 단일 채널 (Full-Swath Composite Backscatter)
        return {
            "is_single_composite": True,
            "ch_port": 0,
            "ch_stbd": 0,
        }

    # 파일 헤더의 ChanInfo 검사
    chan_infos = getattr(fh, 'ChanInfo', [])
    chan_names = []
    chan_types = []
    for i in range(num_chans):
        if i < len(chan_infos):
            ci = chan_infos[i]
            c_name = getattr(ci, 'ChannelName', b'').decode('utf-8', 'replace').strip().lower()
            c_type = int(getattr(ci, 'TypeOfChannel', -1))
        else:
            c_name = f"ch{i}"
            c_type = -1
        chan_names.append(c_name)
        chan_types.append(c_type)

    p_idx, s_idx = None, None

    # 1. TypeOfChannel 기반 탐색 (1=Port, 2=Starboard)
    for i in range(num_chans):
        if chan_types[i] == 1 and p_idx is None:
            p_idx = i
        elif chan_types[i] == 2 and s_idx is None:
            s_idx = i

    # 2. ChannelName 기반 탐색
    if p_idx is None or s_idx is None:
        for i, name in enumerate(chan_names):
            if any(k in name for k in ("port", "ps", "portside")):
                if p_idx is None:
                    p_idx = i
            elif any(k in name for k in ("stbd", "starboard", "sb")):
                if s_idx is None:
                    s_idx = i

    # 3. 만약 여전히 찾지 못했으면, Bathy(3) 채널을 제외한 첫 2개 채널 매핑
    non_bathy = [i for i in range(num_chans) if chan_types[i] != 3 and "bathy" not in chan_names[i]]
    if p_idx is None and non_bathy:
        p_idx = non_bathy[0]
    if s_idx is None and len(non_bathy) > 1:
        s_idx = non_bathy[1]

    # 4. 기본 폴백: 0번, 1번 채널
    if p_idx is None:
        p_idx = 0
    if s_idx is None:
        s_idx = min(1, num_chans - 1)

    if swap_channels:
        p_idx, s_idx = s_idx, p_idx

    return {
        "is_single_composite": False,
        "ch_port": p_idx,
        "ch_stbd": s_idx,
    }


def _extract_ping_channels(ping, fh):
    """핑 객체에서 채널 샘플 배열(1D float32 리스트)을 안전하게 추출."""
    data = getattr(ping, 'data', None)
    if data is None:
        return []

    if isinstance(data, list):
        chans = []
        for ch in data:
            if ch is not None and len(ch) > 0:
                chans.append(np.asarray(ch, dtype=np.float32).ravel())
        return chans

    # data가 raw bytes인 경우 (pyxtf가 HeaderType != sonar 일 때 원시 바이트로 저장한 경우)
    if isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
        num_chans = getattr(ping, 'NumChansToFollow', 0)
        hdrs = getattr(ping, 'ping_chan_headers', [])

        # 만약 ping_chan_headers가 이미 파싱되어 있는 경우
        if hdrs and len(hdrs) == num_chans and num_chans > 0:
            chans = []
            offset = 0
            for i in range(num_chans):
                ch_hdr = hdrs[i]
                n_samp = int(getattr(ch_hdr, 'NumSamples', 0))
                if n_samp <= 0:
                    continue
                # 기본 바이트 크기 결정
                bytes_per_samp = 2  # default 16-bit
                if hasattr(fh, 'sonar_info') and i < len(fh.sonar_info):
                    bytes_per_samp = fh.sonar_info[i].BytesPerSample or 2
                chunk_len = n_samp * bytes_per_samp
                if offset + chunk_len <= len(raw):
                    samples = np.frombuffer(raw[offset:offset + chunk_len], dtype=np.int16 if bytes_per_samp == 2 else np.uint8)
                    chans.append(samples.astype(np.float32))
                    offset += chunk_len
            if chans:
                return chans

        # 원시 바이트 내에 XTFPingChanHeader(64 bytes)가 포함되어 있는 경우 디코딩
        if len(raw) >= 64 and num_chans > 0:
            chans = []
            offset = 0
            for i in range(num_chans):
                if offset + 64 > len(raw):
                    break
                # NumSamples 추출 (XTFPingChanHeader offset 40, uint32)
                try:
                    n_samp = struct.unpack_from('<I', raw, offset + 40)[0]
                    offset += 64
                    if n_samp > 0 and n_samp <= 100000:
                        chunk_len = n_samp * 2  # 16-bit 가정
                        if offset + chunk_len <= len(raw):
                            arr = np.frombuffer(raw[offset:offset + chunk_len], dtype=np.int16).astype(np.float32)
                            chans.append(arr)
                            offset += chunk_len
                            continue
                        elif offset + n_samp <= len(raw):
                            arr = np.frombuffer(raw[offset:offset + n_samp], dtype=np.uint8).astype(np.float32)
                            chans.append(arr)
                            offset += n_samp
                            continue
                except Exception:
                    break
            if chans:
                return chans

        # 그 외의 경우: raw bytes 전체를 16비트 정수(또는 8비트) 1D 배열로 취급
        if len(raw) >= 2:
            arr = np.frombuffer(raw, dtype=np.int16 if len(raw) % 2 == 0 else np.uint8)
            return [arr.astype(np.float32)]

    # XTFBeamXYZA ctypes array인 경우 (HeaderType == bathy_xyza)
    try:
        from pyxtf.xtf_ctypes import XTFBeamXYZA
        if hasattr(data, '_type_') and issubclass(data._type_, XTFBeamXYZA):
            x_vals = [float(b.dPosOffsetTrX) for b in data]
            ampl = [float(b.usAmpl) for b in data]
            if x_vals and ampl:
                # X좌표 기준 정렬하여 1D 백스캐터 라인으로 생성
                order = np.argsort(x_vals)
                ampl_sorted = np.array(ampl, dtype=np.float32)[order]
                return [ampl_sorted]
    except Exception:
        pass

    return []


def _fmt_time(ping):
    """핑 헤더의 날짜/시각 필드 → 'YYYY-MM-DD HH:MM:SS.mmm' 문자열."""
    try:
        return (f"{ping.Year:04d}-{ping.Month:02d}-{ping.Day:02d} "
                f"{ping.Hour:02d}:{ping.Minute:02d}:{ping.Second:02d}"
                f".{getattr(ping, 'HSeconds', 0):02d}0")
    except (AttributeError, TypeError):
        return ""


def read_xtf_channels(path, flip_port=True, swap_channels=False):
    """모자이크 빌더(mosaic_min.py)용 채널 분리 로더.
    Port, Starboard 2D float32 배열 및 메타데이터 dict 리스트 반환.
    Sidescan Sonar 및 Norbit MBES 백스캐터 모두 지원.
    """
    fh, packets = pyxtf.xtf_read(path)
    pings, packet_type = _find_pings(packets)
    if not pings:
        raise ValueError("유효한 소나 또는 멀티빔 핑 패킷이 없습니다.")

    mapping = _resolve_channels(fh, pings[0], swap_channels=swap_channels)
    is_single = mapping["is_single_composite"]
    ch_port = mapping["ch_port"]
    ch_stbd = mapping["ch_stbd"]

    pl, sl, meta = [], [], []

    for idx, ping in enumerate(pings):
        chans = _extract_ping_channels(ping, fh)
        if not chans:
            continue

        port, stbd = None, None
        rp, rs = 0.0, 0.0
        hdrs = getattr(ping, 'ping_chan_headers', [])

        if is_single:
            raw_swath = chans[0] if len(chans) > 0 else None
            if raw_swath is None or len(raw_swath) == 0:
                continue
            mid = len(raw_swath) // 2
            if swap_channels:
                port = raw_swath[mid:].copy()
                stbd = raw_swath[:mid].copy()
            else:
                port = raw_swath[:mid].copy()
                stbd = raw_swath[mid:].copy()

            if flip_port and len(port) > 0:
                port = port[::-1]

            ch_hdr = hdrs[0] if len(hdrs) > 0 else None
            sr_val = float(getattr(ch_hdr, 'SlantRange', 0.0)) if ch_hdr else 0.0
            rp = rs = sr_val
        else:
            if ch_port < len(chans):
                port = chans[ch_port]
            if ch_stbd < len(chans):
                stbd = chans[ch_stbd]

            if port is not None and flip_port and len(port) > 0:
                port = port[::-1]

            if port is None and stbd is None:
                continue
            if port is None:
                port = np.zeros(0, dtype=np.float32)
            if stbd is None:
                stbd = np.zeros(0, dtype=np.float32)

            ch_p = hdrs[ch_port] if len(hdrs) > ch_port else None
            ch_s = hdrs[ch_stbd] if len(hdrs) > ch_stbd else None
            rp = float(getattr(ch_p, 'SlantRange', 0.0)) if ch_p else 0.0
            rs = float(getattr(ch_s, 'SlantRange', 0.0)) if ch_s else 0.0

        # Slant Range 추정 및 보완
        svel = float(getattr(ping, 'ComputedSoundVelocity', 0.0)) or float(getattr(ping, 'SoundVelocity', 0.0)) or 1500.0
        alt = float(getattr(ping, 'SensorPrimaryAltitude', 0.0))
        depth = float(getattr(ping, 'SensorDepth', 0.0))

        if rp <= 0.0:
            ch_p = hdrs[ch_port] if len(hdrs) > ch_port else None
            tdur = float(getattr(ch_p, 'TimeDuration', 0.0)) if ch_p else 0.0
            if tdur > 0.0:
                rp = svel * tdur / 2.0
            elif alt > 0.0:
                rp = alt * 2.5
            elif depth > 0.0:
                rp = depth * 2.5
            else:
                rp = 50.0

        if rs <= 0.0:
            ch_s = hdrs[ch_stbd] if len(hdrs) > ch_stbd else None
            tdur = float(getattr(ch_s, 'TimeDuration', 0.0)) if ch_s else 0.0
            if tdur > 0.0:
                rs = svel * tdur / 2.0
            elif alt > 0.0:
                rs = alt * 2.5
            elif depth > 0.0:
                rs = depth * 2.5
            else:
                rs = 50.0

        pl.append(port.astype(np.float32))
        sl.append(stbd.astype(np.float32))

        meta.append({
            "no": int(getattr(ping, "PingNumber", idx)),
            "time": _fmt_time(ping),
            "x": float(getattr(ping, "SensorXcoordinate", 0.0)),
            "y": float(getattr(ping, "SensorYcoordinate", 0.0)),
            "hd": float(getattr(ping, "SensorHeading", 0.0)),
            "alt": alt,
            "depth": depth,
            "rp": rp,
            "rs": rs,
        })

    if not meta:
        raise ValueError("유효한 소나/멀티빔 데이터가 없습니다.")

    # 0-padding
    w_p = max((r.size for r in pl), default=0)
    out_p = np.zeros((len(pl), max(w_p, 1)), dtype=np.float32)
    for i, r in enumerate(pl):
        if r.size:
            out_p[i, :r.size] = r

    w_s = max((r.size for r in sl), default=0)
    out_s = np.zeros((len(sl), max(w_s, 1)), dtype=np.float32)
    for i, r in enumerate(sl):
        if r.size:
            out_s[i, :r.size] = r

    return out_p, out_s, meta

