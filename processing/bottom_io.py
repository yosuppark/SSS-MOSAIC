# -*- coding: utf-8 -*-
"""
Created on Thu Jul 30 19:22:30 2026

@author: swath
"""

"""bottom_io.py — 바탐(altitude) 사이드카 파일 입출력
XTF 원본은 절대 수정하지 않고, 원본 파일 옆 bottom/ 하위폴더에
<원본명>.bottom.csv 로 핑별 altitude를 저장/로드한다.
"""
import os
import csv


def bottom_path(xtf_path):
    """<xtf 폴더>/bottom/<원본명>.bottom.csv"""
    d = os.path.join(os.path.dirname(xtf_path), "bottom")
    name = os.path.splitext(os.path.basename(xtf_path))[0]
    return os.path.join(d, name + ".bottom.csv")


def save_bottom(xtf_path, meta):
    """meta의 현재 altitude를 사이드카에 기록."""
    path = bottom_path(xtf_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["ping_no", "altitude_m"])
        for m in meta:
            w.writerow([m["ping_no"], f"{m['altitude']:.3f}"])
    return path


def load_bottom(xtf_path):
    """사이드카가 있으면 {ping_no: altitude} dict, 없으면 None."""
    path = bottom_path(xtf_path)
    if not os.path.exists(path):
        return None
    alt = {}
    try:
        with open(path, newline="", encoding="utf-8") as fp:
            r = csv.reader(fp)
            next(r, None)                    # 헤더 skip
            for row in r:
                if len(row) < 2:
                    continue
                alt[int(row[0])] = float(row[1])
    except (ValueError, OSError):
        return None                          # 손상 파일 → 무시
    return alt if alt else None


def sync_bottom(xtf_path, meta):
    """열기/재로드 직후 호출.
    - 사이드카 있음 → meta의 altitude를 사이드카 값으로 덮어씀
    - 사이드카 없음 → 원본 altitude로 사이드카 생성
    반환: True(사이드카 적용됨) / False(신규 생성)"""
    alt = load_bottom(xtf_path)
    if alt is None:
        save_bottom(xtf_path, meta)
        return False
    for m in meta:                # ping_no 기준 매칭 (개수 불일치 안전)
        a = alt.get(m["ping_no"])
        if a is not None:
            m["altitude"] = a
    return True