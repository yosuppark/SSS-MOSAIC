
# -*- coding: utf-8 -*-
"""
mosaic_builder.py
mosaic_dialog.py 의 파라미터 dict 를 받아 mosaic_min.py 로 모자이크를 생성한다.
altitude 는 bottom_loader.py(사이드카/트래킹)를 우선 사용하고,
실패하면 XTF 의 SensorPrimaryAltitude 로 되돌아간다.
"""
from __future__ import annotations

import os
import sys
import json

import numpy as np

import mosaic_min as MM

try:
    import bottom_loader as BL
except Exception:
    BL = None


# ============================================ 유틸
def _resample(a, n):
    """길이가 다른 1D 배열을 핑 개수 n 에 맞춘다."""
    a = np.asarray(a, dtype=np.float64).ravel()
    if a.size == 0:
        return None
    if a.size == n:
        return a
    src = np.linspace(0.0, 1.0, a.size)
    dst = np.linspace(0.0, 1.0, n)
    return np.interp(dst, src, a)


def _as_alt_array(obj, n):
    """load_bottom 반환값(배열/튜플/딕셔너리)을 altitude 배열로 정규화."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in ("altitude_m", "altitude", "alt", "bottom"):
            if k in obj:
                return _resample(np.asarray(obj[k], dtype=np.float64), n)
        return None
    if isinstance(obj, (tuple, list)) and len(obj) == 2:
        try:
            second = np.asarray(obj[1], dtype=np.float64).ravel()
            if second.size:
                return _resample(second, n)
        except Exception:
            pass
    arr = np.asarray(obj, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr[:, -1]
    return _resample(arr, n)


def _interp_nan(a):
    a = np.asarray(a, dtype=np.float64).copy()
    ok = np.isfinite(a)
    if ok.sum() == 0:
        return a
    if ok.sum() == a.size:
        return a
    idx = np.arange(a.size, dtype=np.float64)
    a[~ok] = np.interp(idx[~ok], idx[ok], a[ok])
    return a


def _median1d(a, k=9):
    if BL is not None and hasattr(BL, "median_filter1d"):
        try:
            return np.asarray(BL.median_filter1d(a, k), dtype=np.float64)
        except Exception:
            pass
    a = np.asarray(a, dtype=np.float64)
    k = max(3, int(k) | 1)
    pad = k // 2
    ext = np.pad(a, pad, mode="edge")
    out = np.empty_like(a)
    for i in range(a.size):
        out[i] = np.median(ext[i:i + k])
    return out


# ============================================ 바닥 추적 (로컬 fallback)
def _first_return(d):
    """핑 1개에서 첫 해저 반사 샘플 인덱스. 없으면 None."""
    d = np.asarray(d, dtype=np.float64)
    m = d.size
    if m < 16:
        return None
    w = max(3, m // 200)
    sm = np.convolve(d, np.ones(w) / float(w), mode="same")
    peak = float(np.nanmax(sm)) if np.isfinite(sm).any() else 0.0
    if peak <= 0.0:
        return None
    i0 = max(1, int(0.005 * m))
    hit = np.nonzero(sm[i0:] > 0.35 * peak)[0]
    if hit.size == 0:
        return None
    return i0 + int(hit[0])


def _track_local(P, S, meta):
    """port/stbd 첫 반사로 altitude(m) 추정."""
    n = len(meta)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(n):
        cand = []
        for arr, key in ((P, "rp"), (S, "rs")):
            rg = float(meta[i].get(key, 0.0))
            if rg <= 0.0 or i >= arr.shape[0]:
                continue
            d = arr[i]
            k = _first_return(d)
            if k is None:
                continue
            cand.append(rg * k / float(max(d.size - 1, 1)))
        if cand:
            out[i] = min(cand)
    return out


def _track_bottom(path, P, S, meta):
    """bottom_loader 트래커 우선, 실패 시 로컬 트래커."""
    n = len(meta)
    if BL is not None:
        for fname in ("track_bottom_all", "detect_altitude_bottom_track"):
            fn = getattr(BL, fname, None)
            if fn is None:
                continue
            for args in ((path,), (P, S, meta), (P, meta)):
                try:
                    a = _as_alt_array(fn(*args), n)
                except Exception:
                    continue
                if a is not None and np.isfinite(a).any():
                    print("  bottom: %s() 사용" % fname)
                    return a
    print("  bottom: 내장 트래커 사용")
    return _track_local(P, S, meta)


def _load_sidecar(path, n):
    if BL is None:
        return None
    fn = getattr(BL, "load_bottom", None)
    if fn is None:
        return None
    for args in ((path,), (path, n)):
        try:
            a = _as_alt_array(fn(*args), n)
        except Exception:
            continue
        if a is not None and np.isfinite(a).any():
            return a
    return None


def _save_sidecar(path, alt):
    if BL is None:
        return
    fn = getattr(BL, "save_bottom", None)
    if fn is None:
        return
    try:
        fn(path, np.asarray(alt, dtype=np.float64))
        print("  bottom: 사이드카 저장 완료")
    except Exception:
        pass


# ============================================ altitude 결정
def _apply_bottom(path, P, S, meta, p):
    n = len(meta)
    lo = float(p.get("min_alt", 0.5))
    hi = float(p.get("max_alt", 500.0))
    sensor = np.array([m["alt"] for m in meta], dtype=np.float64)

    alt = None
    src = "sensor"
    if bool(p.get("force_track", False)):
        alt = _track_bottom(path, P, S, meta)
        src = "track(force)"
    else:
        if bool(p.get("use_sidecar", True)):
            alt = _load_sidecar(path, n)
            if alt is not None:
                src = "sidecar"
        if alt is None:
            alt = sensor.copy()
        bad = ~np.isfinite(alt) | (alt < lo) | (alt > hi)
        if bad.any() and bool(p.get("auto_track", True)):
            tr = _track_bottom(path, P, S, meta)
            alt = np.where(bad, tr, alt)
            src = src + "+track(%d)" % int(bad.sum())

    alt = np.asarray(alt, dtype=np.float64)
    alt[~np.isfinite(alt)] = np.nan
    alt[(alt < lo) | (alt > hi)] = np.nan
    if not np.isfinite(alt).any():
        alt = np.clip(sensor, lo, hi)
        src = "sensor(fallback)"
    alt = _median1d(_interp_nan(alt), 9)
    alt = np.clip(alt, lo, hi)

    for i in range(n):
        meta[i]["alt"] = float(alt[i])
    print("  altitude[%s] %.2f ~ %.2f m (평균 %.2f)"
          % (src, alt.min(), alt.max(), alt.mean()))

    if "track" in src and bool(p.get("use_sidecar", True)):
        _save_sidecar(path, alt)
    return alt


# ============================================ 파라미터 변환
def params_to_cfg(p):
    cfg = dict(MM.DEF)
    clip = float(p.get("clip_percent", 2.0))
    clip = min(max(clip, 0.0), 40.0)
    cfg.update({
        "files": list(p.get("xtf_files") or []),
        "outdir": p.get("out_dir") or MM.DEF["outdir"],
        "name": p.get("out_name") or "",
        "epsg": int(p.get("epsg", 32652)),
        "res": float(p.get("resolution", 0.2)),
        "stride": max(1, int(p.get("ping_stride", 1))),
        "maxrange": float(p.get("max_range", 0.0)),
        "minalt": float(p.get("min_alt", 0.5)),
        "wc": float(p.get("wc_margin", 1.0)),
        "slant": bool(p.get("slant_correct", True)),
        "removewc": bool(p.get("remove_water_column", True)),
        "port": bool(p.get("use_port", True)),
        "stbd": bool(p.get("use_stbd", True)),
        "flip_port": bool(p.get("flip_port", True)),
        "avg": bool(p.get("avc_enable", True)),
        "cog": bool(p.get("use_cog", False)),
        "lo": clip,
        "hi": 100.0 - clip,
        "fill": int(p.get("fill_iter", 1)) if p.get("fill_holes", True) else 0,
    })
    if not cfg["name"] and cfg["files"]:
        cfg["name"] = os.path.splitext(os.path.basename(cfg["files"][0]))[0]
    return cfg


# ============================================ 메인 진입점
def build_mosaic_from_params(p):
    """다이얼로그 파라미터 dict → 모자이크 파일 경로."""
    cfg = params_to_cfg(p)
    if not cfg["files"]:
        raise ValueError("XTF 파일이 지정되지 않았습니다.")

    print("=== 모자이크 생성 ===")
    print("파일 %d개 · res=%.3f m · EPSG:%d · wc=%.2f · avg=%s"
          % (len(cfg["files"]), cfg["res"], cfg["epsg"],
             cfg["wc"], cfg["avg"]))

    orig_read = MM.read_xtf

    def _read_with_bottom(path, flip_port=True):
        P, S, meta = orig_read(path, flip_port)
        try:
            _apply_bottom(path, P, S, meta, p)
        except Exception as e:
            print("  [경고] bottom 처리 실패, 센서 고도 사용:", e)
        return P, S, meta

    MM.read_xtf = _read_with_bottom
    try:
        img, x0, y1, res = MM.build(cfg)
    finally:
        MM.read_xtf = orig_read

    out = MM.save(img, x0, y1, res, cfg)
    print("생성 완료:", out)
    return out


def build_mosaic(xtf_files, **kw):
    """코드에서 직접 호출할 때: build_mosaic([...], resolution=0.2)."""
    p = {"xtf_files": list(xtf_files)}
    p.update(kw)
    return build_mosaic_from_params(p)


def _main():
    args = [a for a in sys.argv[1:] if os.path.isfile(a)]
    try:
        from mosaic_dialog import run_dialog, ensure_app
    except ImportError:
        if not args:
            print("사용법: python mosaic_builder.py <file.xtf> ...")
            return 1
        build_mosaic(args)
        return 0

    ensure_app()
    p = run_dialog(None, xtf_files=args)
    if p is None:
        print("취소되었습니다.")
        return 1
    print(json.dumps({k: v for k, v in p.items() if k != "xtf_files"},
                     indent=2, ensure_ascii=False))
    build_mosaic_from_params(p)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
