# -*- coding: utf-8 -*-
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
    """meta(또는 altitude 배열, dict)의 현재 altitude를 사이드카에 기록."""
    path = bottom_path(xtf_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["ping_no", "altitude_m"])
        if isinstance(meta, dict):
            for k in sorted(meta.keys(), key=lambda x: int(x)):
                try:
                    v = float(meta[k])
                except (TypeError, ValueError):
                    v = 0.0
                w.writerow([int(k), f"{v:.3f}"])
            return path
        for i, m in enumerate(meta):
            if isinstance(m, dict):
                pno = m.get("ping_no", i)
                alt = float(m.get("altitude", m.get("alt", 0.0)) or 0.0)
            else:
                pno = i
                try:
                    alt = float(m or 0.0)
                except (TypeError, ValueError):
                    alt = 0.0
            w.writerow([pno, f"{alt:.3f}"])
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
                try:
                    alt[int(float(row[0]))] = float(row[1])
                except (ValueError, TypeError):
                    continue
    except (ValueError, OSError):
        return None                          # 손상 파일 → 무시
    return alt if alt else None


def sync_bottom(xtf_path, meta):
    """열기/재로드 직후 호출.
    - 사이드카 있음(유효값 >= 0.5m 존재) → meta의 altitude를 사이드카 값으로 덮어씀
    - 사이드카 없음 → 유효한 altitude가 있을 때만 사이드카 생성
    반환: True(사이드카 적용됨) / False(미적용 또는 신규 생성)"""
    alt = load_bottom(xtf_path)
    if alt is None:
        valid_cnt = sum(1 for m in meta if isinstance(m, dict) and float(m.get("altitude") or 0.0) >= 0.5)
        if valid_cnt > 0:
            save_bottom(xtf_path, meta)
        return False

    valid_alt_cnt = sum(1 for v in alt.values() if float(v) >= 0.5)
    if valid_alt_cnt == 0:
        return False

    applied = 0
    for i, m in enumerate(meta):
        if not isinstance(m, dict):
            continue
        pno = m.get("ping_no", i)
        a = alt.get(pno)
        if a is not None and float(a) >= 0.5:
            m["altitude"] = float(a)
            applied += 1
    return applied > 0
