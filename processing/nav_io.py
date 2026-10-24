# -*- coding: utf-8 -*-
"""nav_io.py — 항법(Navigation) 사이드카 파일 입출력

XTF 원본은 절대 수정하지 않고, 원본 파일 옆 nav/ 하위폴더에
<원본명>.nav.csv 로 핑별 보정된 위치(lon, lat), heading, speed를 저장/로드합니다.
bottom_io.py 와 동일한 사이드카 설계 패턴을 따릅니다.
"""
from __future__ import annotations

import os
import csv


def nav_path(xtf_path: str) -> str:
    """<xtf 폴더>/nav/<원본명>.nav.csv"""
    d = os.path.join(os.path.dirname(xtf_path), "nav")
    name = os.path.splitext(os.path.basename(xtf_path))[0]
    return os.path.join(d, name + ".nav.csv")


def save_nav(xtf_path: str, meta: list[dict]) -> str | None:
    """meta의 보정된 x(lon), y(lat), heading, speed를 사이드카 CSV에 기록."""
    path = nav_path(xtf_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fp:
            w = csv.writer(fp)
            w.writerow(["ping_no", "time", "lon", "lat", "heading", "speed_mps"])
            for m in meta:
                p_no = m.get("ping_no", 0)
                t_str = m.get("time", "")
                lon = float(m.get("x", 0.0))
                lat = float(m.get("y", 0.0))
                hdg = float(m.get("heading", 0.0))
                spd = float(m.get("speed", 0.0))
                w.writerow([p_no, t_str, f"{lon:.9f}", f"{lat:.9f}", f"{hdg:.2f}", f"{spd:.3f}"])
        return path
    except OSError as e:
        print(f"[경고] nav 사이드카 저장 건너뜀/실패 ({path}): {e}")
        return None


def load_nav(xtf_path: str) -> dict[int, dict] | None:
    """사이드카가 있으면 {ping_no: {'x': lon, 'y': lat, 'heading': hd, 'speed': sp}} dict, 없으면 None."""
    path = nav_path(xtf_path)
    if not os.path.exists(path):
        return None
    nav_data = {}
    try:
        with open(path, newline="", encoding="utf-8") as fp:
            r = csv.reader(fp)
            header = next(r, None)
            if not header or len(header) < 5:
                return None
            for row in r:
                if len(row) < 5:
                    continue
                try:
                    p_no = int(row[0])
                    lon = float(row[2])
                    lat = float(row[3])
                    hdg = float(row[4])
                    spd = float(row[5]) if len(row) > 5 else 0.0
                    nav_data[p_no] = {
                        "x": lon,
                        "y": lat,
                        "heading": hdg,
                        "speed": spd,
                    }
                except (ValueError, IndexError):
                    continue
    except (OSError, UnicodeDecodeError):
        return None
    return nav_data if nav_data else None


def sync_nav(xtf_path: str, meta: list[dict]) -> bool:
    """열기/재로드 직후 호출.
    - 사이드카 있음 → meta의 x, y, heading을 사이드카 보정값으로 덮어씀
    - 사이드카 없음 → False 반환 (원본 값 유지)
    반환: True(사이드카 적용됨) / False(미적용)
    """
    data = load_nav(xtf_path)
    if data is None:
        return False
    applied = 0
    for m in meta:
        if "raw_x" not in m:
            m["raw_x"] = float(m.get("x", 0.0))
            m["raw_y"] = float(m.get("y", 0.0))
            m["raw_heading"] = float(m.get("heading", 0.0))
            m["raw_speed"] = float(m.get("speed", 0.0))
        entry = data.get(m.get("ping_no"))
        if entry is not None:
            m["x"] = entry["x"]
            m["y"] = entry["y"]
            m["heading"] = entry["heading"]
            m["speed"] = entry["speed"]
            applied += 1
    return applied > 0
