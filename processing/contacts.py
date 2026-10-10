# -*- coding: utf-8 -*-
"""
Created on Tue Jul 28 20:14:06 2026

@author: swath
"""

"""processing/contacts.py — Contact 저장 모듈
기능:
  save_contact(img, meta, ping_idx, sample_idx, num, xtf_path)
    1) contact 지리좌표 계산
       - across-track slant 거리 = |sample - nadir_col| * (slant_range / n_side)
       - ground_range = sqrt(slant^2 - altitude^2)   (고도 보정)
       - heading ±90° 방향으로 offset → 경위도 환산
    2) contact 중심 300x300 grayscale 썸네일 PNG 저장
    3) contacts/contactlist.csv 에 한 행 추가
       (filename, date, time, longitude, latitude)
출력 위치: 프로젝트 루트의 contacts/ 폴더 (없으면 자동 생성)
"""
import os
import csv
import math
from datetime import datetime

import numpy as np
from PySide6.QtGui import QImage

CONTACT_DIR = "contacts"
CSV_NAME = "contactlist.csv"
CSV_FIELDS = ["filename", "date", "time", "longitude", "latitude"]
THUMB_SIZE = 300                     # 썸네일 한 변 픽셀 수


# ==================================================== 좌표 계산
def _nadir_col(meta):
    """waterfall 이미지의 nadir 열 = 파일 내 최대 port 샘플 수"""
    return max(m.get("n_port", 0) for m in meta) if meta else 0


def contact_lonlat(meta, ping_idx, sample_idx):
    """(lon, lat) 반환. 좌표가 없으면 (0.0, 0.0)"""
    m = meta[ping_idx]
    lon = float(m.get("x", 0.0))
    lat = float(m.get("y", 0.0))
    heading = float(m.get("heading", 0.0))
    alt = float(m.get("altitude", 0.0))
    sr = float(m.get("slant_range", 0.0))

    nadir = _nadir_col(meta)
    d_cols = sample_idx - nadir              # 음수=Port, 양수=Stbd

    # 열당 미터 (해당 사이드 샘플 수 기준)
    n_side = m.get("n_port", 0) if d_cols < 0 else m.get("n_stbd", 0)
    if sr <= 0 or n_side <= 0 or (lon == 0.0 and lat == 0.0):
        return lon, lat

    slant = abs(d_cols) * sr / n_side
    # 고도 보정: 수평(ground) 거리
    ground = math.sqrt(max(slant * slant - alt * alt, 0.0))

    # 진행방향 기준 좌(Port) -90°, 우(Stbd) +90°
    brg = math.radians(heading + (90.0 if d_cols >= 0 else -90.0))
    de = ground * math.sin(brg)              # 동쪽 offset [m]
    dn = ground * math.cos(brg)              # 북쪽 offset [m]

    # 미터 → 도 (근사식, 소축척 충분)
    lat_c = math.cos(math.radians(lat))
    lon += de / (111320.0 * lat_c) if lat_c > 1e-6 else 0.0
    lat += dn / 111320.0
    return lon, lat


def _date_time_str(m):
    """meta['time'] → (date_str, time_str). 없으면 현재 시각 사용"""
    t = m.get("time")
    if isinstance(t, datetime):
        return t.strftime("%Y-%m-%d"), t.strftime("%H:%M:%S")
    if t:                                    # 문자열 등
        s = str(t)
        if " " in s:
            d, tm = s.split(" ", 1)
            return d, tm
        return "", s
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


# ==================================================== 썸네일
def save_thumbnail(img, ping_idx, sample_idx, out_path, size=THUMB_SIZE):
    """contact 중심 size x size grayscale PNG 저장 (가장자리는 0 패딩)"""
    h, w = img.shape
    half = size // 2
    thumb = np.zeros((size, size), dtype=np.uint8)

    r0, r1 = ping_idx - half, ping_idx + half   # 원본에서 잘라올 범위
    c0, c1 = sample_idx - half, sample_idx + half
    sr0, sc0 = max(r0, 0), max(c0, 0)           # 원본 유효 범위
    sr1, sc1 = min(r1, h), min(c1, w)
    if sr1 > sr0 and sc1 > sc0:
        thumb[sr0 - r0:sr1 - r0, sc0 - c0:sc1 - c0] = img[sr0:sr1, sc0:sc1]

    thumb = np.ascontiguousarray(thumb)
    qimg = QImage(thumb.data, size, size, thumb.strides[0],
                  QImage.Format_Grayscale8)
    qimg.save(out_path, "PNG")


# ==================================================== CSV
def _append_csv(row, out_dir):
    csv_path = os.path.join(out_dir, CSV_NAME)
    new_file = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return csv_path


# ==================================================== 통합 진입점
def save_contact(img, meta, ping_idx, sample_idx, num, xtf_path,
                 out_dir=CONTACT_DIR):
    """contact 1건 저장. 썸네일 PNG + CSV 한 행 추가.
    반환: dict(lon, lat, thumb_path, csv_path)"""
    os.makedirs(out_dir, exist_ok=True)

    lon, lat = contact_lonlat(meta, ping_idx, sample_idx)
    date_s, time_s = _date_time_str(meta[ping_idx])

    base = os.path.splitext(os.path.basename(xtf_path))[0]
    thumb_name = f"C{num:03d}_{base}_p{ping_idx}.png"
    thumb_path = os.path.join(out_dir, thumb_name)
    save_thumbnail(img, ping_idx, sample_idx, thumb_path)

    csv_path = _append_csv({
        "filename": thumb_name,
        "date": date_s,
        "time": time_s,
        "longitude": f"{lon:.7f}",
        "latitude": f"{lat:.7f}",
    }, out_dir)

    return {"lon": lon, "lat": lat,
            "thumb_path": thumb_path, "csv_path": csv_path}