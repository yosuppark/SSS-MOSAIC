
# -*- coding: utf-8 -*-
"""
png_encode.py
------------------------------------------------------------------
Pillow(PIL) 없이 NumPy 배열을 PNG 바이트로 인코딩한다.
표준 라이브러리 zlib / struct / base64 만 사용하므로
PyInstaller 번들에서 추가 의존성이 발생하지 않는다.

지원 형태
    (H, W)          8/16bit 그레이스케일        color type 0
    (H, W, 2)       그레이스케일 + 알파          color type 4
    (H, W, 3)       RGB                         color type 2
    (H, W, 4)       RGBA                        color type 6

공개 API
    encode_png(arr, level=6)        -> bytes
    array_to_png_bytes(arr, alpha=None, nodata=None, vmin=None, vmax=None)
    save_png(path, arr, **kw)
    to_data_url(arr, **kw)          -> "data:image/png;base64,..."
"""

from __future__ import annotations

import base64
import struct
import zlib

import numpy as np

__all__ = [
    "encode_png",
    "array_to_png_bytes",
    "save_png",
    "to_data_url",
]

_SIG = b"\x89PNG\r\n\x1a\n"

# color type: 0=Gray 2=RGB 3=Palette 4=GrayAlpha 6=RGBA
_COLOR_TYPE = {1: 0, 2: 4, 3: 2, 4: 6}


# ----------------------------------------------------------------- 내부 유틸
def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG 청크 하나를 만든다."""
    out = struct.pack(">I", len(data)) + tag + data
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return out + struct.pack(">I", crc)


def _as_3d(arr: np.ndarray) -> np.ndarray:
    """(H, W) 또는 (H, W, C) 를 항상 (H, W, C) 로 만든다."""
    a = np.asarray(arr)
    if a.ndim == 2:
        a = a[:, :, None]
    if a.ndim != 3:
        raise ValueError("배열 차원은 2 또는 3 이어야 합니다: ndim=%d" % a.ndim)
    if a.shape[2] not in (1, 2, 3, 4):
        raise ValueError("채널 수는 1,2,3,4 만 지원합니다: C=%d" % a.shape[2])
    return a


def _stretch_to_uint8(a: np.ndarray, vmin=None, vmax=None) -> np.ndarray:
    """float / int 배열을 0..255 uint8 로 선형 변환한다. NaN 은 0."""
    f = np.asarray(a, dtype=np.float64)
    finite = np.isfinite(f)
    if not finite.any():
        return np.zeros(f.shape, dtype=np.uint8)

    lo = float(np.nanmin(f[finite])) if vmin is None else float(vmin)
    hi = float(np.nanmax(f[finite])) if vmax is None else float(vmax)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0

    g = (f - lo) / (hi - lo)
    g = np.clip(g, 0.0, 1.0)
    g[~finite] = 0.0
    return (g * 255.0 + 0.5).astype(np.uint8)


# ------------------------------------------------------------------ 인코딩
def encode_png(arr: np.ndarray, level: int = 6) -> bytes:
    """
    uint8 또는 uint16 배열을 PNG 바이트로 인코딩한다.
    다른 dtype 은 uint8 로 자동 스트레치한다.
    """
    a = _as_3d(arr)

    if a.dtype == np.uint16:
        depth = 16
        body = np.ascontiguousarray(a.astype(">u2"))
    elif a.dtype == np.uint8:
        depth = 8
        body = np.ascontiguousarray(a)
    else:
        depth = 8
        body = np.ascontiguousarray(_stretch_to_uint8(a))

    h, w, c = body.shape
    ctype = _COLOR_TYPE[c]

    raw = body.tobytes()
    stride = w * c * (depth // 8)

    # 각 행 앞에 필터 바이트 0 을 붙인다.
    lines = bytearray()
    for y in range(h):
        lines.append(0)
        lines += raw[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 0)

    out = bytearray(_SIG)
    out += _chunk(b"IHDR", ihdr)
    out += _chunk(b"IDAT", zlib.compress(bytes(lines), level))
    out += _chunk(b"IEND", b"")
    return bytes(out)


def array_to_png_bytes(arr,
                       alpha=None,
                       nodata=None,
                       vmin=None,
                       vmax=None,
                       level: int = 6) -> bytes:
    """
    임의 배열 -> PNG 바이트.

    arr    : (H,W) 또는 (H,W,3) / (H,W,4)
    alpha  : (H,W) 알파 마스크. bool / 0~1 float / 0~255 uint8 모두 허용.
    nodata : 이 값과 같은 화소를 투명 처리 (alpha 미지정 시)
    """
    a = np.asarray(arr)

    # ---- 본체를 uint8 로 정규화 --------------------------------------
    if a.dtype == np.uint8:
        body = _as_3d(a)
    elif a.dtype == np.uint16:
        body = _as_3d(a)          # 16bit 유지
    else:
        body = _as_3d(_stretch_to_uint8(a, vmin, vmax))

    h, w, c = body.shape

    # ---- 알파 결정 ----------------------------------------------------
    am = None
    if alpha is not None:
        am = np.asarray(alpha)
        if am.ndim == 3 and am.shape[2] == 1:
            am = am[:, :, 0]
        if am.shape != (h, w):
            raise ValueError("alpha 크기 불일치: %s vs %s"
                             % (am.shape, (h, w)))
        if am.dtype == np.bool_:
            am = np.where(am, 255, 0).astype(np.uint8)
        elif am.dtype != np.uint8:
            f = np.asarray(am, dtype=np.float64)
            if np.nanmax(f) <= 1.0 + 1e-9:
                f = f * 255.0
            am = np.clip(np.nan_to_num(f), 0, 255).astype(np.uint8)
    elif nodata is not None:
        src = np.asarray(arr)
        if src.ndim == 3:
            src = src[:, :, 0]
        if isinstance(nodata, float) and np.isnan(nodata):
            m = ~np.isnan(src)
        else:
            m = src != nodata
        am = np.where(m, 255, 0).astype(np.uint8)
    elif np.issubdtype(a.dtype, np.floating):
        src = a[:, :, 0] if a.ndim == 3 else a
        am = np.where(np.isfinite(src), 255, 0).astype(np.uint8)

    # ---- 알파 합성 ----------------------------------------------------
    if am is not None:
        if body.dtype == np.uint16:
            body = (body >> 8).astype(np.uint8)
        if c == 1:
            body = np.concatenate([body, am[:, :, None]], axis=2)
        elif c == 3:
            body = np.concatenate([body, am[:, :, None]], axis=2)
        elif c == 2:
            body = np.concatenate([body[:, :, :1], am[:, :, None]], axis=2)
        elif c == 4:
            body = body.copy()
            body[:, :, 3] = np.minimum(body[:, :, 3], am)

    return encode_png(body, level=level)


def save_png(path: str, arr, **kw) -> str:
    """PNG 파일로 저장하고 경로를 반환한다."""
    data = array_to_png_bytes(arr, **kw)
    with open(path, "wb") as f:
        f.write(data)
    return path


def to_data_url(arr, **kw) -> str:
    """Leaflet 등에서 바로 쓸 수 있는 data URL 문자열."""
    data = array_to_png_bytes(arr, **kw)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


# ------------------------------------------------------------------- self test
if __name__ == "__main__":
    yy, xx = np.mgrid[0:120, 0:200]
    g = ((xx / 199.0) * 255).astype(np.uint8)
    msk = ((xx - 100) ** 2 + (yy - 60) ** 2) < 55 ** 2

    b1 = array_to_png_bytes(g)
    b2 = array_to_png_bytes(g, alpha=msk)
    rgb = np.dstack([g, g[::-1], g[:, ::-1]])
    b3 = array_to_png_bytes(rgb, alpha=msk)
    f = np.where(msk, xx.astype(np.float32), np.nan)
    b4 = array_to_png_bytes(f)

    for name, b in (("gray", b1), ("gray+a", b2),
                    ("rgba", b3), ("float", b4)):
        ok = b.startswith(_SIG) and b.endswith(b"IEND\xae\x42\x60\x82")
        print("%-8s %7d bytes  sig/iend=%s" % (name, len(b), ok))

    url = to_data_url(g)
    print("data url  :", url[:48], "...", len(url), "chars")
    print("self test OK")
