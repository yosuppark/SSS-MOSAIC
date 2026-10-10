# -*- coding: utf-8 -*-
"""
overlay_mixin.py
Leaflet 이미지 오버레이 믹스인.

map_view.py 에서 다음과 같이 사용:

    from overlay_mixin import ImageOverlayMixin

    class MapView(ImageOverlayMixin, QWebEngineView):
        ...

요구 사항
  - 클래스에 self._run_js(js: str) 메서드가 있어야 함
  - JS 전역에 leaflet map 객체 이름이 'map' 이어야 함
    (다른 이름이면 아래 _MAP_VAR 를 수정)
"""

import base64
import json
import os
from pathlib import Path

import numpy as np

from bounds_utils import to_leaflet, BoundsError  # noqa: F401
from png_encode import array_to_png_bytes as _png_bytes

_MAP_VAR = "map"

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

__all__ = ["ImageOverlayMixin", "to_image_url",
           "array_to_png_bytes", "tif_to_png_bytes"]


# ---------------------------------------------------------------- 유틸
def array_to_png_bytes(arr, alpha=None):
    """ndarray -> PNG bytes.  (모듈 함수: self 인자 없음)"""
    if arr is None:
        raise ValueError("array_to_png_bytes: arr 가 None 입니다")
    try:
        return _png_bytes(arr, alpha=alpha)
    except TypeError:
        # png_encode 쪽이 alpha 키워드를 받지 않는 구버전인 경우
        return _png_bytes(arr)


def tif_to_png_bytes(path):
    """GeoTIFF -> RGBA PNG 바이트 (rasterio 필요)."""
    import rasterio

    with rasterio.open(str(path)) as ds:
        arr = ds.read()
        nod = ds.nodata

    nb = arr.shape[0]

    if nb == 1:
        g = arr[0]
        mask = np.ones(g.shape, dtype=bool)
        if nod is not None:
            mask &= (g != nod)
        if g.dtype.kind == "f":
            mask &= np.isfinite(g)

        v = g.astype("float32")
        if mask.any():
            lo, hi = np.nanpercentile(v[mask], [1, 99])
        else:
            lo, hi = 0.0, 1.0
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.nanmin(v)) if mask.any() else 0.0, None
            hi = lo + 1.0
        v = np.clip((v - lo) / (hi - lo), 0.0, 1.0) * 255.0

        rgba = np.zeros((g.shape[0], g.shape[1], 4), dtype="uint8")
        gv = v.astype("uint8")
        rgba[:, :, 0] = gv
        rgba[:, :, 1] = gv
        rgba[:, :, 2] = gv
        rgba[:, :, 3] = np.where(mask, 255, 0).astype("uint8")

    elif nb == 2:
        g = arr[0].astype("float32")
        a = arr[1]
        lo, hi = float(np.nanmin(g)), float(np.nanmax(g))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = 0.0, 1.0
        v = np.clip((g - lo) / (hi - lo), 0.0, 1.0) * 255.0

        rgba = np.zeros((g.shape[0], g.shape[1], 4), dtype="uint8")
        gv = v.astype("uint8")
        rgba[:, :, 0] = gv
        rgba[:, :, 1] = gv
        rgba[:, :, 2] = gv
        rgba[:, :, 3] = (a if a.dtype == np.uint8
                         else np.clip(a, 0, 255).astype("uint8"))

    else:
        rgba = np.zeros((arr.shape[1], arr.shape[2], 4), dtype="uint8")
        for k in range(3):
            b = arr[min(k, nb - 1)].astype("float32")
            lo, hi = float(np.nanmin(b)), float(np.nanmax(b))
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            rgba[:, :, k] = (np.clip((b - lo) / (hi - lo), 0.0, 1.0)
                             * 255).astype("uint8")
        if nb >= 4:
            a = arr[3]
            rgba[:, :, 3] = (a if a.dtype == np.uint8
                             else np.clip(a, 0, 255).astype("uint8"))
        else:
            rgba[:, :, 3] = 255

    return array_to_png_bytes(rgba)


def to_image_url(src):
    """경로 / URL / numpy 배열 -> 브라우저가 읽을 수 있는 URL 문자열."""
    if src is None:
        raise ValueError("to_image_url: src 가 None 입니다")

    # 1) numpy 배열
    if hasattr(src, "shape") and hasattr(src, "dtype"):
        b64 = base64.b64encode(array_to_png_bytes(src)).decode("ascii")
        return "data:image/png;base64," + b64

    # 2) 이미 인코딩된 바이트
    if isinstance(src, (bytes, bytearray)):
        b64 = base64.b64encode(bytes(src)).decode("ascii")
        return "data:image/png;base64," + b64

    s = str(src).strip()

    # 3) 이미 URL
    if s.startswith(("http://", "https://", "data:", "file:///")):
        return s

    # 4) 로컬 파일
    p = Path(os.path.abspath(s))
    if not p.exists():
        raise FileNotFoundError(str(p))

    ext = p.suffix.lower()
    if ext in (".tif", ".tiff"):
        b64 = base64.b64encode(tif_to_png_bytes(str(p))).decode("ascii")
        return "data:image/png;base64," + b64

    mime = _MIME.get(ext, "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64)


# 구버전 호출부 호환용 별칭
_array_to_png_bytes = array_to_png_bytes
_tif_to_png_bytes = tif_to_png_bytes


# ---------------------------------------------------------------- 믹스인
class ImageOverlayMixin(object):
    """Leaflet 이미지 오버레이 추가/제거."""

    def _to_image_url(self, src):
        return to_image_url(src)

    def add_image_overlay(self, url, bounds, opacity=0.8, fit=True):
        """georeferenced 이미지 오버레이.

        url    : PNG/TIF 경로, 'file:///...', 'data:...', numpy RGBA 배열
        bounds : ((s, w), (n, e)) / (left, bottom, right, top) /
                 rasterio BoundingBox  (투영 좌표면 자동 WGS84 변환)
        """
        try:
            (s, w), (n, e) = to_leaflet(bounds, latlon=True)
        except Exception as exc:
            print("[오버레이] bounds 정규화 실패:", repr(bounds), exc)
            return None

        for v, name in ((s, "south"), (w, "west"), (n, "north"), (e, "east")):
            if v != v or v in (float("inf"), float("-inf")):
                print("[오버레이] bounds.%s 값이 유효하지 않습니다: %r"
                      % (name, v))
                return None

        try:
            src = self._to_image_url(url)
        except Exception as exc:
            print("[오버레이] 이미지 인코딩 실패:", exc)
            return None

        op = max(0.0, min(1.0, float(opacity)))

        js = (
            "(function(){"
            "  if(!window.overlays){window.overlays=[];}"
            "  var ov=L.imageOverlay(%s,[[%s,%s],[%s,%s]],"
            "                        {opacity:%s,interactive:false});"
            "  ov.addTo(%s);"
            "  window.overlays.push(ov);"
            "  if(%s){try{%s.fitBounds(ov.getBounds());}catch(err){}}"
            "})();"
        ) % (json.dumps(src), json.dumps(s), json.dumps(w),
             json.dumps(n), json.dumps(e), json.dumps(op),
             _MAP_VAR, json.dumps(bool(fit)), _MAP_VAR)

        self._run_js(js)
        return True

    def clear_image_overlays(self):
        """추가한 이미지 오버레이를 모두 제거."""
        js = (
            "(function(){"
            "  if(!window.overlays){window.overlays=[];return;}"
            "  window.overlays.forEach(function(o){"
            "    try{%s.removeLayer(o);}catch(err){}"
            "  });"
            "  window.overlays=[];"
            "})();"
        ) % (_MAP_VAR,)
        self._run_js(js)
        return True


if __name__ == "__main__":
    a = np.zeros((4, 4, 4), dtype="uint8")
    a[:, :, 0] = 255
    a[:, :, 3] = 128
    u = to_image_url(a)
    print(u[:40], "...", len(u), "chars")
    print(json.dumps(r"C:\pywork\scan6\260708_004347_2_a.png"))