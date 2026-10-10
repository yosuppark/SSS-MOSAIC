
# -*- coding: utf-8 -*-
"""
mosaic_alpha.py
사이드스캔 모자이크를 알파 채널(배경 투명) 포함 GeoTIFF 로 저장한다.
 - mode="gray" : 2밴드 (Gray + Alpha)
 - mode="rgba" : 4밴드 (R, G, B + Alpha)
배경(무데이터) 픽셀은 alpha=0 이 되어 QGIS/ArcGIS 에서 투명하게 표시된다.
"""
from __future__ import annotations

import os
import sys

import numpy as np

import rasterio
from rasterio.transform import from_origin
from rasterio.enums import ColorInterp, Resampling


# ============================================ 유효 마스크
def valid_mask(img, nodata=0.0):
    """데이터가 있는 픽셀 True. NaN 과 nodata 값을 배경으로 본다."""
    a = np.asarray(img)
    m = np.isfinite(a)
    if nodata is not None:
        m &= (a != nodata)
    return m


def shrink_mask(mask, iters=0):
    """가장자리 노이즈 제거용 단순 침식(4-이웃). iters=0 이면 그대로."""
    m = np.asarray(mask, dtype=bool)
    for _ in range(max(0, int(iters))):
        e = m.copy()
        e[1:, :] &= m[:-1, :]
        e[:-1, :] &= m[1:, :]
        e[:, 1:] &= m[:, :-1]
        e[:, :-1] &= m[:, 1:]
        m = e
    return m


# ============================================ 8비트 변환
def to_uint8(img, mask, lo=2.0, hi=98.0):
    """유효 픽셀만으로 퍼센타일 스트레치 후 1~255 로 변환(0 은 배경 예약)."""
    a = np.asarray(img, dtype=np.float64)
    v = a[mask]
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.zeros(a.shape, dtype=np.uint8)
    p0 = float(np.percentile(v, max(0.0, float(lo))))
    p1 = float(np.percentile(v, min(100.0, float(hi))))
    if not np.isfinite(p0) or not np.isfinite(p1) or p1 <= p0:
        p0, p1 = float(v.min()), float(v.max())
    if p1 <= p0:
        p1 = p0 + 1.0
    out = (a - p0) / (p1 - p0)
    out = np.clip(out, 0.0, 1.0)
    out = 1.0 + out * 254.0
    out[~mask] = 0.0
    return np.nan_to_num(out, nan=0.0, posinf=255.0, neginf=0.0).astype(np.uint8)


def gray_to_rgb(g):
    """그레이스케일 uint8 -> RGB 3밴드 (동일 값 복제)."""
    g = np.asarray(g, dtype=np.uint8)
    return np.stack([g, g, g], axis=0)


# ============================================ 저장
def save_alpha(img, x0, y1, res, cfg, mode="gray", erode=0, out_path=None):
    """
    img      : 2D 배열 (float 또는 uint8), 배경은 NaN 또는 0
    x0, y1   : 좌상단 좌표 (EPSG:cfg['epsg'])
    res      : 픽셀 크기 (m)
    cfg      : mosaic_min 설정 dict (epsg/outdir/name/lo/hi 사용)
    mode     : "gray"(2밴드) 또는 "rgba"(4밴드)
    erode    : 유효 마스크 침식 반복 횟수
    out_path : 지정 시 이 경로로 그대로 저장
    반환     : 저장된 GeoTIFF 경로
    """
    a = np.asarray(img)
    if a.ndim != 2:
        raise ValueError("img 는 2D 배열이어야 합니다.")

    h, w = a.shape
    epsg = int(cfg.get("epsg", 32652))
    lo = float(cfg.get("lo", 2.0))
    hi = float(cfg.get("hi", 98.0))

    mask = shrink_mask(valid_mask(a, nodata=0.0), erode)
    if not mask.any():
        raise ValueError("유효 픽셀이 없습니다. 모자이크가 비어 있습니다.")

    gray = to_uint8(a, mask, lo, hi)
    gray[~mask] = 0
    alpha = np.where(mask, 255, 0).astype(np.uint8)

    if str(mode).lower() == "rgba":
        bands = np.concatenate([gray_to_rgb(gray), alpha[None, :, :]], axis=0)
        interp = [ColorInterp.red, ColorInterp.green,
                  ColorInterp.blue, ColorInterp.alpha]
        names = ["red", "green", "blue", "alpha"]
        photo = "RGB"
    else:
        bands = np.stack([gray, alpha], axis=0)
        interp = [ColorInterp.gray, ColorInterp.alpha]
        names = ["intensity", "alpha"]
        photo = "MINISBLACK"

    if out_path:
        path = out_path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    else:
        name = cfg.get("name") or "mosaic"
        outdir = os.path.join(cfg.get("outdir", "./exported_mosaics"), name)
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "%s_mosaic.tif" % name)

    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": int(bands.shape[0]),
        "dtype": "uint8",
        "crs": "EPSG:%d" % epsg,
        "transform": from_origin(float(x0), float(y1), float(res), float(res)),
        "compress": "LZW",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "photometric": photo,
        "alpha": "yes",
        "BIGTIFF": "IF_SAFER",
    }

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(bands)
        try:
            dst.colorinterp = interp
        except Exception:
            pass
        for i, nm in enumerate(names, start=1):
            dst.set_band_description(i, nm)
        dst.update_tags(AREA_OR_POINT="Area", MOSAIC_RES_M="%.4f" % float(res))
        try:
            dst.build_overviews([2, 4, 8, 16], Resampling.average)
            dst.update_tags(ns="rio_overview", resampling="average")
        except Exception:
            pass

    cov = 100.0 * float(mask.sum()) / float(mask.size)
    print("[알파 저장] %s (%dx%d, %d밴드, 유효 %.1f%%)"
          % (path, w, h, bands.shape[0], cov))
    return path


# ============================================ 기존 파일에 알파 추가
def add_alpha_to_file(src_path, dst_path=None, nodata=0.0, mode="gray",
                      lo=0.0, hi=100.0, erode=0):
    """이미 만들어진 단일밴드 GeoTIFF 에 알파 채널을 붙여 새 파일로 저장."""
    if dst_path is None:
        base, ext = os.path.splitext(src_path)
        dst_path = base + "_alpha" + (ext or ".tif")

    with rasterio.open(src_path) as src:
        arr = src.read(1).astype(np.float64)
        nd = src.nodata if src.nodata is not None else nodata
        epsg = src.crs.to_epsg() if src.crs else 32652
        x0 = float(src.transform.c)
        y1 = float(src.transform.f)
        res = abs(float(src.transform.a))

    mask = valid_mask(arr, nodata=nd)
    tmp = np.where(mask, arr, np.nan)
    cfg = {"epsg": int(epsg or 32652), "lo": float(lo), "hi": float(hi)}
    return save_alpha(tmp, x0, y1, res, cfg, mode=mode, erode=erode,
                      out_path=dst_path)


# ============================================ CLI
def _main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    mode = "rgba" if "--rgba" in sys.argv else "gray"
    if not args:
        print("사용법: python mosaic_alpha.py <mosaic.tif> [out.tif] [--rgba]")
        return 1
    src = args[0]
    dst = args[1] if len(args) > 1 else None
    if not os.path.isfile(src):
        print("파일이 없습니다:", src)
        return 1
    add_alpha_to_file(src, dst, mode=mode)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
