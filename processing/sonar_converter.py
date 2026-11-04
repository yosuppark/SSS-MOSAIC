# -*- coding: utf-8 -*-
"""processing/sonar_converter.py
Humminbird (.DAT, .SON, .IDX) & Lowrance (.SL2, .SL3, .SLG) Sonar Reader & XTF Converter.

References:
 - CameronBodine/PINGMapper & PINGVerter (https://github.com/CameronBodine/PINGMapper)
 - OpenSounder / python-sllib (https://github.com/opensounder/python-sllib)
 - PyHum (https://github.com/dbuscombe-usgs/PyHum)
 - OpenSeaMap Humminbird specifications

Provides:
 - LowranceReader: Decodes SLG (v1), SL2 (v2, 144B), SL3 (v3, 168B)
 - HumminbirdReader: Decodes Humminbird DAT metadata and paired B002.SON (Port) / B003.SON (Starboard)
 - convert_sonar_to_xtf: Universal converter to standard dual-channel XTF format for instant mosaicing.
 - inspect_sonar_file: Quick metadata probe (file format, ping count, coordinates, range).
"""
from __future__ import annotations

import os
import math
import struct
import numpy as np
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List, Tuple
import pyxtf

# ==================================================== 좌표계 변환 상수
EARTH_RADIUS = 6356752.3142
RAD_TO_DEG = 180.0 / math.pi
FEET_TO_METERS = 0.3048
KNOTS_TO_MS = 0.514444

HUM_EARTH_RADIUS = 6378388.0
HUM_FLATTENING_FACTOR = 1.0067642927


def navico_mercator_to_wgs84(easting: int, northing: int) -> Tuple[float, float]:
    """Lowrance / Navico Mercator meters -> (lon, lat) WGS84 degrees."""
    if easting == 0 and northing == 0:
        return 0.0, 0.0
    lon = (float(easting) / EARTH_RADIUS) * RAD_TO_DEG
    try:
        temp = math.exp(float(northing) / EARTH_RADIUS)
        lat = ((2.0 * math.atan(temp)) - (math.pi / 2.0)) * RAD_TO_DEG
    except OverflowError:
        lat = 0.0
    return lon, lat


def humminbird_mercator_to_wgs84(utm_e: int, utm_n: int) -> Tuple[float, float]:
    """Humminbird projected easting/northing -> (lon, lat) WGS84 degrees."""
    if utm_e == 0 and utm_n == 0:
        return 0.0, 0.0
    lon = (float(utm_e) * RAD_TO_DEG) / HUM_EARTH_RADIUS
    try:
        exp_val = math.exp(float(utm_n) / HUM_EARTH_RADIUS)
        lat = math.atan(math.tan(math.atan(exp_val) * 2.0 - (math.pi / 2.0)) * HUM_FLATTENING_FACTOR) * RAD_TO_DEG
    except (OverflowError, ValueError):
        lat = 0.0
    return lon, lat


# ==================================================== Lowrance 파서 (SLG, SL2, SL3)
class LowranceReader:
    """Lowrance .slg, .sl2, .sl3 바이너리 프레임 파서"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.format_ver = 2  # default SL2
        self.header_size = 144
        self._probe_file_header()

    def _probe_file_header(self):
        with open(self.filepath, 'rb') as f:
            hb = f.read(8)
            if len(hb) >= 6:
                fmt, ver, block_sz = struct.unpack('<HHH', hb[:6])
                self.format_ver = fmt
                if fmt == 1:
                    self.header_size = 144  # SLG variable
                elif fmt == 2:
                    self.header_size = 144  # SL2 standard
                elif fmt == 3:
                    self.header_size = 168  # SL3 extended
                else:
                    self.header_size = 144

    def parse_pings(self, max_pings: Optional[int] = None,
                    progress_callback: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
        """사이드스캔 핑(Left/Right/Composite) 프레임 파싱."""
        file_size = os.path.getsize(self.filepath)
        pings = {}  # time_ms -> ping record

        with open(self.filepath, 'rb') as f:
            f.seek(8)  # skip 8-byte file header
            bytes_read = 8

            while True:
                if max_pings and len(pings) >= max_pings:
                    break

                pos = f.tell()
                if pos >= file_size:
                    break

                hdr_bytes = f.read(self.header_size)
                if len(hdr_bytes) < self.header_size:
                    break
                bytes_read += len(hdr_bytes)

                if self.format_ver == 3:
                    # SL3 (168 bytes)
                    (frame_offset, frame_version, frame_size, prev_frame_size,
                     channel_type, unknown14, frame_id, min_range, max_range) = struct.unpack('<IIHHHIfff', hdr_bytes[:28])

                    creation_time = struct.unpack('<I', hdr_bytes[40:44])[0]
                    packet_size = struct.unpack('<I', hdr_bytes[44:48])[0]
                    water_depth = struct.unpack('<f', hdr_bytes[48:52])[0]

                    gps_speed = struct.unpack('<f', hdr_bytes[84:88])[0]
                    easting = struct.unpack('<i', hdr_bytes[92:96])[0]
                    northing = struct.unpack('<i', hdr_bytes[96:100])[0]
                    altitude_ft = struct.unpack('<f', hdr_bytes[108:112])[0]
                    heading_rad = struct.unpack('<f', hdr_bytes[112:116])[0]
                    time_offset_ms = struct.unpack('<I', hdr_bytes[128:132])[0]

                else:
                    # SL2 (144 bytes)
                    (frame_offset, last_pri, last_sec, last_ds, last_left, last_right, last_composite,
                     frame_size, prev_frame_size, channel_type, packet_size, frame_index,
                     upper_limit, lower_limit, _, _, _, _, _, _, creation_time, water_depth, keel_depth) = struct.unpack(
                        '<IIIIIIIHHHIIffHHHHHHIff', hdr_bytes[:74])

                    gps_speed = struct.unpack('<f', hdr_bytes[100:104])[0]
                    easting = struct.unpack('<i', hdr_bytes[108:112])[0]
                    northing = struct.unpack('<i', hdr_bytes[112:116])[0]
                    altitude_ft = struct.unpack('<f', hdr_bytes[124:128])[0]
                    heading_rad = struct.unpack('<f', hdr_bytes[128:132])[0]
                    time_offset_ms = struct.unpack('<I', hdr_bytes[140:144])[0]

                # 안전 검사: packet_size 계산
                if packet_size == 0 or packet_size > 65536:
                    packet_size = max(0, frame_size - self.header_size)

                sounding_data = f.read(packet_size)
                bytes_read += len(sounding_data)
                if len(sounding_data) < packet_size:
                    break

                # SideScan Channels:
                # 3 = Left/Port, 4 = Right/Starboard, 5 = Composite (Left + Right combined)
                if channel_type in (3, 4, 5):
                    lon, lat = navico_mercator_to_wgs84(easting, northing)
                    depth_m = water_depth * FEET_TO_METERS if water_depth > 0 else 0.0
                    alt_m = altitude_ft * FEET_TO_METERS if altitude_ft > -9000 else 0.0
                    heading_deg = (heading_rad * RAD_TO_DEG) % 360.0

                    key = time_offset_ms if time_offset_ms > 0 else pos
                    ping_entry = pings.setdefault(key, {
                        'time_ms': time_offset_ms,
                        'creation_time': creation_time,
                        'lon': lon,
                        'lat': lat,
                        'depth_m': depth_m,
                        'altitude_m': alt_m,
                        'heading_deg': heading_deg,
                        'speed_kts': float(gps_speed),
                        'left_samples': None,
                        'right_samples': None,
                    })

                    samples = np.frombuffer(sounding_data, dtype=np.uint8)

                    if channel_type == 3:
                        ping_entry['left_samples'] = samples
                    elif channel_type == 4:
                        ping_entry['right_samples'] = samples
                    elif channel_type == 5:
                        half = len(samples) // 2
                        ping_entry['left_samples'] = samples[:half]
                        ping_entry['right_samples'] = samples[half:]

                if progress_callback and file_size > 0 and len(pings) % 50 == 0:
                    pct = int(min(99, pos * 100 // file_size))
                    progress_callback(pct, len(pings))

        valid_pings = [p for p in pings.values() if p['left_samples'] is not None or p['right_samples'] is not None]
        valid_pings.sort(key=lambda x: x['time_ms'])
        return valid_pings


# ==================================================== Humminbird 파서 (DAT, SON, IDX)
class HumminbirdReader:
    """Humminbird .DAT metadata 및 B002.SON(Port) / B003.SON(Starboard) 파서"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.dat_file = ""
        self.son_dir = ""
        self.port_son = ""
        self.stbd_son = ""
        self.port_idx = ""
        self.stbd_idx = ""
        self._resolve_paths()

    def _resolve_paths(self):
        base, ext = os.path.splitext(self.filepath)
        ext_lower = ext.lower()

        if ext_lower == ".dat":
            self.dat_file = self.filepath
            # DAT 파일과 동일한 이름의 폴더 또는 RECORD 폴더 탐색
            stem = os.path.basename(base)
            parent = os.path.dirname(self.filepath)
            candidates = [
                os.path.join(parent, stem),
                os.path.join(parent, "RECORD", stem),
                os.path.join(parent, stem.replace("Rec", "R")),
                parent
            ]
            for cand in candidates:
                if os.path.isdir(cand):
                    self.son_dir = cand
                    break
            if not self.son_dir:
                self.son_dir = parent
        else:
            # .son 또는 .idx 파일 직접 지정
            self.son_dir = os.path.dirname(self.filepath)
            stem = os.path.basename(self.son_dir)
            parent = os.path.dirname(self.son_dir)
            cand_dat = os.path.join(parent, stem + ".DAT")
            if os.path.isfile(cand_dat):
                self.dat_file = cand_dat

        # Port / Starboard 파일 검색
        # B002 = Sidescan Port, B003 = Sidescan Starboard
        for fn in os.listdir(self.son_dir) if os.path.isdir(self.son_dir) else []:
            fn_u = fn.upper()
            full = os.path.join(self.son_dir, fn)
            if fn_u == "B002.SON":
                self.port_son = full
            elif fn_u == "B003.SON":
                self.stbd_son = full
            elif fn_u == "B002.IDX":
                self.port_idx = full
            elif fn_u == "B003.IDX":
                self.stbd_idx = full

    @staticmethod
    def _read_index_offsets(idx_path: str) -> List[Tuple[int, int]]:
        """Humminbird .IDX 파일에서 (record_num, file_offset) 목록 반환."""
        offsets = []
        if not idx_path or not os.path.isfile(idx_path):
            return offsets
        with open(idx_path, 'rb') as f:
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    break
                rec_num, pos = struct.unpack('>ii', chunk)
                offsets.append((rec_num, pos))
        return offsets

    def _parse_single_son(self, son_path: str, idx_path: str, max_pings: Optional[int] = None) -> List[Dict[str, Any]]:
        """단일 .SON 파일에서 핑 헤더 및 샘플 배열 추출."""
        if not son_path or not os.path.isfile(son_path):
            return []

        records = []
        offsets = self._read_index_offsets(idx_path)
        file_len = os.path.getsize(son_path)

        with open(son_path, 'rb') as f:
            if offsets:
                # 인덱스 파일 기반 탐색
                for i, (rec_num, pos) in enumerate(offsets):
                    if max_pings and len(records) >= max_pings:
                        break
                    if pos >= file_len:
                        break
                    f.seek(pos)
                    rec = self._parse_son_record(f, file_len)
                    if rec:
                        records.append(rec)
            else:
                # 인덱스 파일 없을 때: 0xC0DEAB21 매직 넘버 기반 순차 탐색
                while f.tell() < file_len:
                    if max_pings and len(records) >= max_pings:
                        break
                    pos = f.tell()
                    magic = f.read(4)
                    if len(magic) < 4:
                        break
                    if magic == b'\xc0\xde\xab!':
                        f.seek(pos)
                        rec = self._parse_son_record(f, file_len)
                        if rec:
                            records.append(rec)
                    else:
                        f.seek(pos + 1)
        return records

    def _parse_son_record(self, f, file_len: int) -> Optional[Dict[str, Any]]:
        """Humminbird SON 핑 레코드 1개 파싱 (헤더 + 샘플 데이터)."""
        start_pos = f.tell()
        magic = f.read(4)
        if len(magic) < 4 or magic != b'\xc0\xde\xab!':
            return None

        rec_num = 0
        time_s = 0
        utm_e = 0
        utm_n = 0
        heading_deg = 0.0
        speed_kts = 0.0
        depth_m = 0.0
        ping_cnt = 0

        # 헤더 태그 파싱 루프 (최대 200바이트 탐색)
        header_found = False
        while (f.tell() - start_pos) < 200:
            tag_b = f.read(1)
            if not tag_b:
                break
            tag = tag_b[0]

            if tag == 0x21:  # 33 = header end
                header_found = True
                break
            elif tag == 128:  # 0x80: record number
                b = f.read(4)
                if len(b) == 4:
                    rec_num = struct.unpack('>I', b)[0]
            elif tag == 129:  # 0x81: time
                b = f.read(4)
                if len(b) == 4:
                    time_s = struct.unpack('>I', b)[0]
            elif tag == 130:  # 0x82: utm_e
                b = f.read(4)
                if len(b) == 4:
                    utm_e = struct.unpack('>i', b)[0]
            elif tag == 131:  # 0x83: utm_n
                b = f.read(4)
                if len(b) == 4:
                    utm_n = struct.unpack('>i', b)[0]
            elif tag == 132:  # 0x84: gps1 + heading (1/10 deg)
                b = f.read(4)
                if len(b) == 4:
                    gps1, hdg_raw = struct.unpack('>HH', b)
                    heading_deg = (hdg_raw / 10.0) % 360.0
            elif tag == 133:  # 0x85: gps2 + speed (cm/s)
                b = f.read(4)
                if len(b) == 4:
                    gps2, spd_raw = struct.unpack('>HH', b)
                    speed_kts = (spd_raw / 100.0) * 1.94384
            elif tag == 135:  # 0x87: depth
                b = f.read(4)
                if len(b) == 4:
                    dep_raw = struct.unpack('>I', b)[0]
                    depth_m = dep_raw / 10.0 if dep_raw < 100000 else dep_raw / 100.0
            elif tag == 160:  # 0xA0: ping_cnt (샘플 바이트 수)
                b = f.read(4)
                if len(b) == 4:
                    ping_cnt = struct.unpack('>I', b)[0]
            else:
                # 기타 1바이트 또는 4바이트 태그 건너뛰기
                if tag < 100:
                    f.seek(1, 1)
                else:
                    f.seek(4, 1)

        if not header_found or ping_cnt <= 0 or ping_cnt > 16384:
            return None

        # 샘플 페이로드 읽기
        samples = f.read(ping_cnt)
        if len(samples) < ping_cnt:
            return None

        lon, lat = humminbird_mercator_to_wgs84(utm_e, utm_n)

        return {
            'rec_num': rec_num,
            'time_s': time_s,
            'lon': lon,
            'lat': lat,
            'heading_deg': heading_deg,
            'speed_kts': speed_kts,
            'depth_m': depth_m,
            'samples': np.frombuffer(samples, dtype=np.uint8)
        }

    def parse_pings(self, max_pings: Optional[int] = None,
                    progress_callback: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
        """Port(B002)와 Starboard(B003)를 동기화하여 좌우 결합 핑 리스트 생성."""
        port_recs = self._parse_single_son(self.port_son, self.port_idx, max_pings=max_pings)
        stbd_recs = self._parse_single_son(self.stbd_son, self.stbd_idx, max_pings=max_pings)

        if not port_recs and not stbd_recs:
            return []

        # Port 또는 Starboard 중 많은 쪽 기준 매칭
        n_pings = max(len(port_recs), len(stbd_recs))
        if max_pings:
            n_pings = min(n_pings, max_pings)
        combined = []

        for i in range(n_pings):
            p = port_recs[i] if i < len(port_recs) else None
            s = stbd_recs[i] if i < len(stbd_recs) else None
            ref = p if p else s

            entry = {
                'time_ms': ref['time_s'] * 1000,
                'creation_time': ref['time_s'],
                'lon': ref['lon'],
                'lat': ref['lat'],
                'depth_m': ref['depth_m'],
                'altitude_m': ref['depth_m'] * 0.7 if ref['depth_m'] > 0 else 5.0,
                'heading_deg': ref['heading_deg'],
                'speed_kts': ref['speed_kts'],
                'left_samples': p['samples'] if p else None,
                'right_samples': s['samples'] if s else None,
            }
            combined.append(entry)

            if progress_callback and i % 50 == 0:
                progress_callback(int(i * 100 // n_pings), len(combined))

        return combined


# ==================================================== 통합 변환 엔진
def convert_sonar_to_xtf(input_path: str, output_xtf_path: Optional[str] = None,
                         progress_callback: Optional[Callable[[int, str], None]] = None) -> str:
    """Humminbird (.DAT, .SON) 또는 Lowrance (.SL2, .SL3, .SLG) 파일을 표준 XTF 파일로 변환."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"입력 파일이 존재하지 않습니다: {input_path}")

    ext = os.path.splitext(input_path)[1].lower()
    if output_xtf_path is None:
        base = os.path.splitext(input_path)[0]
        output_xtf_path = base + ".xtf"

    if progress_callback:
        progress_callback(10, f"포맷 감지 중: {ext} ...")

    # 1. 파일 형식에 따른 리더 선택
    if ext in (".sl2", ".sl3", ".slg"):
        reader = LowranceReader(input_path)
        pings = reader.parse_pings(
            progress_callback=lambda pct, cnt: progress_callback(10 + int(pct * 0.6), f"Lowrance 프레임 파싱 중 ({cnt} Pings)...") if progress_callback else None
        )
    elif ext in (".dat", ".son", ".idx"):
        reader = HumminbirdReader(input_path)
        pings = reader.parse_pings(
            progress_callback=lambda pct, cnt: progress_callback(10 + int(pct * 0.6), f"Humminbird 핑 파싱 중 ({cnt} Pings)...") if progress_callback else None
        )
    else:
        raise ValueError(f"지원하지 않는 소나 파일 형식입니다: {ext}")

    if not pings:
        raise ValueError(f"'{input_path}'에서 유효한 사이드스캔 데이터(Port/Starboard)를 찾을 수 없습니다.")

    if progress_callback:
        progress_callback(75, f"XTF 헤더 및 채널 구조체 생성 중 (총 {len(pings)} Pings)...")

    # 2. 샘플 수 결정 (첫 번째 유효 핑 기준)
    sample_count = 0
    for p in pings:
        if p['left_samples'] is not None and len(p['left_samples']) > 0:
            sample_count = len(p['left_samples'])
            break
        elif p['right_samples'] is not None and len(p['right_samples']) > 0:
            sample_count = len(p['right_samples'])
            break
    if sample_count == 0:
        sample_count = 1000

    # 3. pyxtf XTFFileHeader 구성
    file_header = pyxtf.XTFFileHeader()
    file_header.FileFormat = 123  # Standard XTF 0x7B
    file_header.SystemType = 1
    file_header.NumberOfSonarChannels = 2
    file_header.NavUnits = 3      # 3: Lat/Long in degrees

    file_header.ChanInfo[0].TypeOfChannel = 1  # Port Sidescan
    file_header.ChanInfo[0].BytesPerSample = 1
    file_header.ChanInfo[0].SampleFormat = 0   # 8-bit unsigned
    file_header.ChanInfo[0].ChannelName = b'Port'

    file_header.ChanInfo[1].TypeOfChannel = 2  # Starboard Sidescan
    file_header.ChanInfo[1].BytesPerSample = 1
    file_header.ChanInfo[1].SampleFormat = 0   # 8-bit unsigned
    file_header.ChanInfo[1].ChannelName = b'Starboard'

    # 4. XTFPingHeader 바이너리 스트림 기록
    if progress_callback:
        progress_callback(80, f"XTF 파일 디스크 기록 시작: {os.path.basename(output_xtf_path)} ...")

    base_dt = datetime.now(timezone.utc)
    record_bytes_total = 256 + 2 * (64 + sample_count)
    total_written = 0

    with open(output_xtf_path, 'wb') as f_out:
        f_out.write(file_header.to_bytes())

        for idx, p in enumerate(pings):
            ping = pyxtf.XTFPingHeader()
            ping.MagicNumber = 0xFACE
            ping.HeaderType = 0  # 0: XTF_HEADER_SONAR
            ping.NumChansToFollow = 2
            ping.NumBytesThisRecord = record_bytes_total
            ping.PingNumber = idx

            # 시간 정보
            t_val = p.get('creation_time', 0)
            if t_val and t_val > 1000000000:
                try:
                    dt = datetime.fromtimestamp(t_val, tz=timezone.utc)
                except Exception:
                    dt = base_dt
            else:
                dt = base_dt

            ping.Year = dt.year
            ping.Month = dt.month
            ping.Day = dt.day
            ping.Hour = dt.hour
            ping.Minute = dt.minute
            ping.Second = dt.second
            ping.HSeconds = int(dt.microsecond / 10000)

            # 네비게이션 및 센서 메타데이터
            ping.SensorXcoordinate = float(p.get('lon', 0.0))
            ping.SensorYcoordinate = float(p.get('lat', 0.0))
            ping.SensorHeading = float(p.get('heading_deg', 0.0))
            ping.SensorSpeed = float(p.get('speed_kts', 0.0))
            alt_m = float(p.get('altitude_m', 0.0))
            dep_m = float(p.get('depth_m', 0.0))
            ping.SensorPrimaryAltitude = alt_m
            ping.WaterDepth = dep_m
            ping.SensorDepth = dep_m
            ping.ComputedSoundVelocity = 1500.0

            # 좌/우 채널 샘플 데이터
            p_data = p.get('left_samples')
            s_data = p.get('right_samples')

            if p_data is None or len(p_data) == 0:
                p_data = np.zeros(sample_count, dtype=np.uint8)
            elif len(p_data) != sample_count:
                p_data = np.pad(p_data[:sample_count], (0, max(0, sample_count - len(p_data))))
            else:
                p_data = np.asarray(p_data, dtype=np.uint8)

            if s_data is None or len(s_data) == 0:
                s_data = np.zeros(sample_count, dtype=np.uint8)
            elif len(s_data) != sample_count:
                s_data = np.pad(s_data[:sample_count], (0, max(0, sample_count - len(s_data))))
            else:
                s_data = np.asarray(s_data, dtype=np.uint8)

            slant_range = max(20.0, dep_m * 2.5) if dep_m > 0 else 50.0

            ch0 = pyxtf.XTFPingChanHeader()
            ch0.ChannelNumber = 0
            ch0.NumSamples = sample_count
            ch0.SlantRange = slant_range

            ch1 = pyxtf.XTFPingChanHeader()
            ch1.ChannelNumber = 1
            ch1.NumSamples = sample_count
            ch1.SlantRange = slant_range

            ping.ping_chan_headers = [ch0, ch1]
            ping.data = [p_data, s_data]

            f_out.write(ping.to_bytes())
            total_written += 1

            if progress_callback and idx % 100 == 0:
                pct = 80 + int(idx * 18 // len(pings))
                progress_callback(pct, f"XTF 기록 중 ({idx}/{len(pings)})...")

    if progress_callback:
        progress_callback(100, f"변환 완료! (총 {total_written} Pings)")

    return output_xtf_path


def inspect_sonar_file(filepath: str) -> Dict[str, Any]:
    """소나 파일 정보를 신속히 조회하여 요약 딕셔너리 반환."""
    ext = os.path.splitext(filepath)[1].lower()
    sz_mb = os.path.getsize(filepath) / (1024 * 1024)

    info = {
        'path': filepath,
        'filename': os.path.basename(filepath),
        'size_mb': sz_mb,
        'format': ext.upper()[1:],
        'pings': 0,
        'start_time': None,
        'valid': False,
        'description': '',
    }

    try:
        if ext in ('.sl2', '.sl3', '.slg'):
            r = LowranceReader(filepath)
            p = r.parse_pings(max_pings=10)
            info['format'] = f"Lowrance SL{r.format_ver}"
            info['pings'] = len(p)
            info['valid'] = len(p) > 0
            info['description'] = f"Lowrance SL{r.format_ver} Log (Frame: {r.header_size}B)"
        elif ext in ('.dat', '.son', '.idx'):
            r = HumminbirdReader(filepath)
            p = r.parse_pings(max_pings=10)
            info['format'] = "Humminbird Sonar"
            info['pings'] = len(p)
            info['valid'] = len(p) > 0
            info['description'] = f"Humminbird Sonar (Port: {os.path.basename(r.port_son)}, Stbd: {os.path.basename(r.stbd_son)})"
    except Exception as e:
        info['description'] = f"조회 오류: {e}"

    return info
