# -*- coding: utf-8 -*-
"""
Created on Fri Jul 31 23:20:26 2026

@author: swath
"""

import os
import math
import struct
import numpy as np
from datetime import datetime, timezone
import pyxtf

# ==========================================
# 1. Navico Mercator -> WGS84 위경도 변환
# ==========================================
POLAR_EARTH_RADIUS = 6356752.3142
RAD_TO_DEG = 180.0 / math.pi

def mercator_to_wgs84(easting: int, northing: int):
    """Navico SL2 Easting/Northing(Mercator meters) -> (Longitude, Latitude)"""
    if easting == 0 and northing == 0:
        return 0.0, 0.0
    lon = (easting / POLAR_EARTH_RADIUS) * RAD_TO_DEG
    lat = ((2.0 * math.atan(math.exp(northing / POLAR_EARTH_RADIUS))) - (math.pi / 2.0)) * RAD_TO_DEG
    return lon, lat


# ==========================================
# 2. SL2 파일 파서
# ==========================================
class SL2Reader:
    """Navico SL2 (Format 2) 바이너리 프레임 파서"""
    FRAME_HEADER_SIZE = 144

    def __init__(self, filepath: str):
        self.filepath = filepath

    def parse_frames(self):
        pings = {}  # {time_offset: {'left': frame, 'right': frame, 'meta': ...}}

        with open(self.filepath, 'rb') as f:
            header_bytes = f.read(8)
            if len(header_bytes) < 8:
                return []
            
            fmt, version, bytes_per_sounding = struct.unpack('<HHH', header_bytes[:6])
            
            while True:
                frame_bytes = f.read(self.FRAME_HEADER_SIZE)
                if len(frame_bytes) < self.FRAME_HEADER_SIZE:
                    break

                (
                    frame_offset, last_primary, last_sec, last_ds, last_left, last_right, last_composite,
                    frame_size, prev_frame_size, channel_type, packet_size, frame_index,
                    upper_limit, lower_limit, _, _, _, _, _, _, creation_time, water_depth, keel_depth
                ) = struct.unpack('<IIIIIIIHHHIIffHHHHHHIf', frame_bytes[:68])

                speed_gps = struct.unpack('<f', frame_bytes[100:104])[0]
                temp_c = struct.unpack('<f', frame_bytes[104:108])[0]
                easting = struct.unpack('<i', frame_bytes[108:112])[0]
                northing = struct.unpack('<i', frame_bytes[112:116])[0]
                cog_rad = struct.unpack('<f', frame_bytes[120:124])[0]
                altitude_ft = struct.unpack('<f', frame_bytes[124:128])[0]
                heading_rad = struct.unpack('<f', frame_bytes[128:132])[0]
                time_offset_ms = struct.unpack('<I', frame_bytes[140:144])[0]

                sounding_data = f.read(packet_size)
                if len(sounding_data) < packet_size:
                    break

                # SideScan Channel (3: Left, 4: Right, 5: Composite)
                if channel_type in (3, 4, 5):
                    lon, lat = mercator_to_wgs84(easting, northing)
                    
                    ping_entry = pings.setdefault(time_offset_ms, {
                        'time_ms': time_offset_ms,
                        'creation_time': creation_time,
                        'lon': lon, 'lat': lat,
                        'depth_m': water_depth * 0.3048,
                        'altitude_m': altitude_ft * 0.3048 if altitude_ft > -9000 else 0.0,
                        'heading_deg': heading_rad * RAD_TO_DEG % 360,
                        'speed_kts': speed_gps,
                        'left_samples': None,
                        'right_samples': None
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

        valid_pings = [p for p in pings.values() if p['left_samples'] is not None or p['right_samples'] is not None]
        valid_pings.sort(key=lambda x: x['time_ms'])
        return valid_pings


# ==========================================
# 3. SL2 -> XTF 변환기 클래스
# ==========================================
class SL2ToXTFConverter:
    """SL2 파싱 결과를 pyxtf 구조체로 변환하여 저장"""

    @staticmethod
    def convert(sl2_path: str, xtf_path: str):
        reader = SL2Reader(sl2_path)
        pings = reader.parse_frames()

        if not pings:
            print(f"[!] 변환 실패: '{sl2_path}'에서 유효한 사이드스캔 데이터 프레임을 찾을 수 없습니다.")
            return False

        # 샘플 수 결정 (첫번째 유효 핑 기준)
        sample_count = 0
        for p in pings:
            if p['left_samples'] is not None:
                sample_count = len(p['left_samples'])
                break
            elif p['right_samples'] is not None:
                sample_count = len(p['right_samples'])
                break

        if sample_count == 0:
            sample_count = 1000

        # --- 1) File Header 설정 ---
        file_header = pyxtf.XTFHeader()
        file_header.NumberOfSonarChannels = 2

        # Port Channel (0)
        chan_attr_port = pyxtf.XTFPingChanAttrs()
        chan_attr_port.NumSamples = sample_count
        chan_attr_port.ChannelNumber = 0

        # Starboard Channel (1)
        chan_attr_stbd = pyxtf.XTFPingChanAttrs()
        chan_attr_stbd.NumSamples = sample_count
        chan_attr_stbd.ChannelNumber = 1

        file_header.ChanAddressAttrs = [chan_attr_port, chan_attr_stbd]

        # --- 2) Ping Packet 생성 ---
        sonar_packets = []

        for idx, p in enumerate(pings):
            ping = pyxtf.XTFPingHeader()
            ping.PingNumber = idx

            # 시간 설정 (Creation Time / POSIX Time)
            t_val = p['creation_time']
            if t_val > 0:
                dt = datetime.fromtimestamp(t_val, tz=timezone.utc)
            else:
                dt = datetime.now(timezone.utc)

            ping.Year = dt.year
            ping.Month = dt.month
            ping.Day = dt.day
            ping.Hour = dt.hour
            ping.Minute = dt.minute
            ping.Second = dt.second
            ping.HSeconds = int(dt.microsecond / 10000)

            # 위치 및 항법 메타데이터
            ping.SensorXcoordinate = p['lon']
            ping.SensorYcoordinate = p['lat']
            ping.SensorHeading = p['heading_deg']
            ping.SensorSpeed = p['speed_kts']
            ping.SensorPrimaryAltitude = p['altitude_m']
            ping.WaterDepth = p['depth_m']

            # 샘플 데이터 배열 준비 (2 채널 x sample_count)
            port_data = p['left_samples'] if p['left_samples'] is not None else np.zeros(sample_count, dtype=np.uint8)
            stbd_data = p['right_samples'] if p['right_samples'] is not None else np.zeros(sample_count, dtype=np.uint8)

            # 길이 맞춤 (크기가 다를 경우 padding/crop)
            if len(port_data) != sample_count:
                port_data = np.pad(port_data[:sample_count], (0, max(0, sample_count - len(port_data))))
            if len(stbd_data) != sample_count:
                stbd_data = np.pad(stbd_data[:sample_count], (0, max(0, sample_count - len(stbd_data))))

            # XTF Ping data: Shape (Channels, Samples)
            ping.data = np.vstack([port_data, stbd_data])
            sonar_packets.append(ping)

        # --- 3) XTF 파일 쓰기 ---
        packets_dict = {
            pyxtf.XTFHeaderType.sonar: sonar_packets
        }
        
        pyxtf.save_file(xtf_path, file_header, packets_dict)
        print(f"[✓] 변환 완료: '{sl2_path}' -> '{xtf_path}' (총 {len(sonar_packets)} Pings)")
        return True


# ==========================================
# 실행부 (사용 예시)
# ==========================================
if __name__ == "__main__":
    input_sl2 = "Sonar0001.sl2"
    # 파일명과 확장자 분리 후 .xtf 결합
    output_xtf = os.path.splitext(input_sl2)[0] + ".xtf"

    if os.path.exists(input_sl2):
        SL2ToXTFConverter.convert(input_sl2, output_xtf)
    else:
        print(f"테스트용 파일 '{input_sl2}'이 존재하지 않습니다.")