# -*- coding: utf-8 -*-
"""
bounds_utils.py
------------------------------------------------------------------
지도 오버레이용 bounds 유틸리티 통합 모듈.

공개 API
    BoundsError
    normalize_bounds(bounds, latlon=None) -> (lon0, lat0, lon1, lat1)
    explain(bounds, latlon=None)          -> str
    is_valid(bounds, latlon=None)         -> bool
    to_leaflet(bounds, latlon=None)       -> [[lat0, lon0], [lat1, lon1]]
    reproject_bounds(bounds, src_crs, dst_crs="EPSG:4326")
    bounds_from_transform(transform, width, height)
    bounds_from_raster(src, dst_crs="EPSG:4326", latlon=False)
    bounds_from_xy(xs, ys, src_crs=None, dst_crs="EPSG:4326")
"""

from __future__ import annotations

__all__ = ["BoundsError", "normalize_bounds", "explain", "is_valid",
           "to_leaflet", "reproject_bounds", "bounds_from_transform",
           "bounds_from_raster", "bounds_from_xy"]

MIN_SPAN = 1e-12


class BoundsError(ValueError):
    pass


_KEYSETS = (
    ("west", "south", "east", "north"),
    ("left", "bottom", "right", "top"),
    ("min_lon", "min_lat", "max_lon", "max_lat"),
    ("minx", "miny", "maxx", "maxy"),
    ("lon0", "lat0", "lon1", "lat1"),
    ("x0", "y0", "x1", "y1"),
)


def _f(v):
    try:
        return float(v)
    except Exception:
        raise BoundsError("숫자로 변환할 수 없습니다: %r" % (v,))


def _is_seq(v):
    return hasattr(v, "__len__") and not isinstance(v, (str, bytes))


def _looks_like_latlon(a, b, c, d):
    lat_first = abs(a) <= 90.0 and abs(c) <= 90.0
    lon_first = abs(b) <= 90.0 and abs(d) <= 90.0
    if lat_first and not lon_first:
        return True
    if lon_first and not lat_first:
        return False
    return False


def _order(lo0, la0, lo1, la1):
    if lo0 > lo1:
        lo0, lo1 = lo1, lo0
    if la0 > la1:
        la0, la1 = la1, la0
    return (lo0, la0, lo1, la1)


def normalize_bounds(bounds, latlon=None):
    if bounds is None:
        raise BoundsError("bounds 가 None 입니다.")

    if isinstance(bounds, dict):
        low = {str(k).lower(): v for k, v in bounds.items()}
        for ks in _KEYSETS:
            if all(k in low for k in ks):
                a, b, c, d = (_f(low[k]) for k in ks)
                return _order(a, b, c, d)
        raise BoundsError("dict 에서 bounds 키를 찾지 못했습니다: %s"
                          % sorted(low.keys()))

    if all(hasattr(bounds, k) for k in ("left", "bottom", "right", "top")):
        return _order(_f(bounds.left), _f(bounds.bottom),
                      _f(bounds.right), _f(bounds.top))

    try:
        seq = list(bounds)
    except TypeError:
        raise BoundsError("지원하지 않는 bounds 타입: %r" % type(bounds))

    if len(seq) == 4 and not any(_is_seq(v) for v in seq):
        a, b, c, d = (_f(v) for v in seq)
        return _order(a, b, c, d)

    if len(seq) == 2 and all(_is_seq(v) for v in seq):
        p0, p1 = list(seq[0]), list(seq[1])
        if len(p0) < 2 or len(p1) < 2:
            raise BoundsError("중첩 bounds 의 좌표쌍 길이가 부족합니다.")
        a, b = _f(p0[0]), _f(p0[1])
        c, d = _f(p1[0]), _f(p1[1])
        if latlon is None:
            latlon = _looks_like_latlon(a, b, c, d)
        if latlon:
            return _order(b, a, d, c)
        return _order(a, b, c, d)

    if len(seq) == 1:
        return normalize_bounds(seq[0], latlon=latlon)

    raise BoundsError("bounds 길이가 %d 입니다. 2 또는 4 여야 합니다."
                      % len(seq))


def explain(bounds, latlon=None):
    try:
        lo0, la0, lo1, la1 = normalize_bounds(bounds, latlon=latlon)
    except Exception as exc:
        return "정규화 실패: %s: %s" % (type(exc).__name__, exc)

    msgs = []
    for name, v in (("lon0", lo0), ("lat0", la0),
                    ("lon1", lo1), ("lat1", la1)):
        if v != v:
            msgs.append("%s 가 NaN" % name)
        elif v in (float("inf"), float("-inf")):
            msgs.append("%s 가 무한대" % name)
    if lo1 - lo0 <= MIN_SPAN:
        msgs.append("경도 폭이 0 (%.12g)" % (lo1 - lo0))
    if la1 - la0 <= MIN_SPAN:
        msgs.append("위도 폭이 0 (%.12g)" % (la1 - la0))
    if abs(lo0) > 180.0 or abs(lo1) > 180.0:
        msgs.append("경도 범위 초과 (%.6f ~ %.6f)" % (lo0, lo1))
    if abs(la0) > 90.0 or abs(la1) > 90.0:
        msgs.append("위도 범위 초과 (%.6f ~ %.6f)" % (la0, la1))

    norm = "(%.8f, %.8f, %.8f, %.8f)" % (lo0, la0, lo1, la1)
    if msgs:
        return "무효 %s : %s" % (norm, " / ".join(msgs))
    return "유효 %s" % norm


def is_valid(bounds, latlon=None):
    return explain(bounds, latlon=latlon).startswith("유효")


def to_leaflet(bounds, latlon=None):
    lo0, la0, lo1, la1 = normalize_bounds(bounds, latlon=latlon)
    return [[la0, lo0], [la1, lo1]]


def reproject_bounds(bounds, src_crs, dst_crs="EPSG:4326"):
    """투영 좌표 bounds 를 dst_crs 로 변환한다. (x0, y0, x1, y1) 반환."""
    x0, y0, x1, y1 = normalize_bounds(bounds, latlon=False)
    if src_crs is None:
        return (x0, y0, x1, y1)
    try:
        from rasterio.warp import transform_bounds
        return tuple(transform_bounds(src_crs, dst_crs, x0, y0, x1, y1,
                                      densify_pts=21))
    except Exception:
        pass
    from pyproj import Transformer
    tr = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xs, ys = [], []
    n = 21
    for i in range(n):
        t = i / float(n - 1)
        for px, py in ((x0 + (x1 - x0) * t, y0),
                       (x0 + (x1 - x0) * t, y1),
                       (x0, y0 + (y1 - y0) * t),
                       (x1, y0 + (y1 - y0) * t)):
            a, b = tr.transform(px, py)
            xs.append(a)
            ys.append(b)
    return (min(xs), min(ys), max(xs), max(ys))


def bounds_from_transform(transform, width, height):
    """affine transform + 크기 -> (x0, y0, x1, y1)."""
    a, b, c = transform[0], transform[1], transform[2]
    d, e, f = transform[3], transform[4], transform[5]
    xs, ys = [], []
    for col, row in ((0, 0), (width, 0), (0, height), (width, height)):
        xs.append(a * col + b * row + c)
        ys.append(d * col + e * row + f)
    return (min(xs), min(ys), max(xs), max(ys))


def bounds_from_raster(src, dst_crs="EPSG:4326", latlon=False):
    """GeoTIFF 경로 또는 열린 rasterio 데이터셋에서 bounds 를 얻는다.

    반환은 기본적으로 (lon0, lat0, lon1, lat1).
    latlon=True 면 Leaflet 용 [[lat0, lon0], [lat1, lon1]].
    """
    import rasterio

    if hasattr(src, "bounds") and hasattr(src, "crs"):
        ds, close = src, False
    else:
        ds, close = rasterio.open(str(src)), True
    try:
        bb = ds.bounds
        src_crs = ds.crs
        raw = (float(bb.left), float(bb.bottom),
               float(bb.right), float(bb.top))
    finally:
        if close:
            ds.close()

    if src_crs is None:
        out = raw
    elif dst_crs is None or str(src_crs).upper() == str(dst_crs).upper():
        out = raw
    else:
        out = reproject_bounds(raw, src_crs, dst_crs)

    out = _order(*out)
    if latlon:
        return [[out[1], out[0]], [out[3], out[2]]]
    return out


def bounds_from_xy(xs, ys, src_crs=None, dst_crs="EPSG:4326"):
    """좌표 배열에서 bounds 를 만든다. src_crs 지정 시 dst_crs 로 변환."""
    xs = [float(v) for v in xs if v == v]
    ys = [float(v) for v in ys if v == v]
    if not xs or not ys:
        raise BoundsError("좌표 배열이 비어 있습니다.")
    raw = (min(xs), min(ys), max(xs), max(ys))
    if src_crs is None:
        return raw
    return reproject_bounds(raw, src_crs, dst_crs)


if __name__ == "__main__":
    cases = [
        ((33.23405088207483, 126.57413778312186),
         (33.23592030879591, 126.58586765137908)),
        (126.5, 33.2, 126.6, 33.3),
        [[33.2, 126.5], [33.3, 126.6]],
        {"west": 126.5, "south": 33.2, "east": 126.6, "north": 33.3},
        ((33.2, 126.5), (33.2, 126.6)),
        None,
    ]
    for c in cases:
        print("%-46s -> %s" % (repr(c)[:44], explain(c)))
    print("leaflet ->", to_leaflet(((126.5, 33.2), (126.6, 33.3))))
    print("bounds_from_transform ->",
          bounds_from_transform((0.2, 0.0, 300000.0,
                                 0.0, -0.2, 3680000.0), 1000, 500))
    print("self test OK")