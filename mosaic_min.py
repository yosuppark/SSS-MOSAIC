# -*- coding: utf-8 -*-
"""mosaic_min.py - pyxtf 직접 읽기 사이드스캔 모자이크 (EPSG:32652)."""
from __future__ import annotations

import os
import sys
import glob
import math

import numpy as np
import pyxtf

DEF = {
    "res": 0.2, "epsg": 32652, "stride": 1, "maxrange": 0.0,
    "minalt": 0.5, "wc": 1.0, "slant": True, "removewc": True,
    "port": True, "stbd": True, "flip_port": True, "avg": True,
    "cog": False, "lo": 2.0, "hi": 98.0, "fill": 1,
    "outdir": os.path.join(".", "exported_mosaics"), "name": "", "files": [],
}


def _chan(ping, ch):
    """핑에서 채널 샘플을 1D float32로 추출. 없으면 None."""
    try:
        d = ping.data[ch]
    except (IndexError, TypeError, KeyError):
        return None
    if d is None or len(d) == 0:
        return None
    return np.asarray(d, dtype=np.float32).ravel()


def _srange(headers, ch):
    try:
        return float(getattr(headers[ch], "SlantRange", 0.0))
    except (IndexError, TypeError, AttributeError):
        return 0.0


def _fmt_time(ping):
    """핑 헤더의 날짜/시각 필드 → 'YYYY-MM-DD HH:MM:SS.mmm' 문자열."""
    try:
        return (f"{ping.Year:04d}-{ping.Month:02d}-{ping.Day:02d} "
                f"{ping.Hour:02d}:{ping.Minute:02d}:{ping.Second:02d}"
                f".{getattr(ping, 'HSeconds', 0):02d}0")
    except (AttributeError, TypeError):
        return ""


def _pad(rows):
    """길이가 다른 1D 배열 리스트를 0-padding 2D 배열로."""
    w = max((r.size for r in rows), default=0)
    out = np.zeros((len(rows), max(w, 1)), dtype=np.float32)
    for i, r in enumerate(rows):
        if r.size:
            out[i, :r.size] = r
    return out


def read_xtf(path, flip_port=True, swap_channels=False):
    """원시 float 진폭과 메타를 읽는다. (Sidescan Sonar 및 Norbit MBES 백스캐터 완벽 지원)"""
    try:
        from xtf_loader import read_xtf_channels
        return read_xtf_channels(path, flip_port=flip_port, swap_channels=swap_channels)
    except Exception as e:
        print(f"  [mosaic_min] read_xtf_channels 오류, fallback 로더 사용: {e}")

    _fh, packets = pyxtf.xtf_read(path)
    pings = packets.get(pyxtf.XTFHeaderType.sonar, [])
    if not pings:
        raise ValueError("소나 핑 패킷(XTF_HEADER_SONAR)이 없습니다.")

    ch_port = 1 if swap_channels else 0
    ch_stbd = 0 if swap_channels else 1

    pl, sl, meta = [], [], []
    for idx, ping in enumerate(pings):
        p = _chan(ping, ch_port)
        s = _chan(ping, ch_stbd)
        if p is None and s is None:
            continue
        if p is None:
            p = np.zeros(0, dtype=np.float32)
        if s is None:
            s = np.zeros(0, dtype=np.float32)
        if flip_port and p.size:
            p = p[::-1]
        pl.append(p)
        sl.append(s)
        h = ping.ping_chan_headers
        meta.append({
            "no": int(getattr(ping, "PingNumber", idx)),
            "time": _fmt_time(ping),
            "x": float(getattr(ping, "SensorXcoordinate", 0.0)),
            "y": float(getattr(ping, "SensorYcoordinate", 0.0)),
            "hd": float(getattr(ping, "SensorHeading", 0.0)),
            "alt": float(getattr(ping, "SensorPrimaryAltitude", 0.0)),
            "rp": _srange(h, ch_port),
            "rs": _srange(h, ch_stbd),
        })

    if not meta:
        raise ValueError("유효한 소나 데이터가 없습니다.")
    return _pad(pl), _pad(sl), meta


def avg_correct(A):
    """샘플 인덱스별 중앙값으로 나눠 빔 패턴/TVG 밴딩 제거."""
    if A.size == 0:
        return A
    M = np.where(A > 0, A, np.nan)
    with np.errstate(invalid="ignore"):
        prof = np.nanmedian(M, axis=0)
    prof = np.where(np.isfinite(prof) & (prof > 0), prof, np.nan)
    good = np.isfinite(prof)
    if good.sum() < 4:
        return A
    idx = np.arange(prof.size, dtype=np.float64)
    prof = np.interp(idx, idx[good], prof[good])
    out = A / prof[None, :]
    return np.where(np.isfinite(out), out, 0.0).astype(np.float32)


def headings(meta, force_cog=False):
    """보정된 hd가 존재하면 이를 우선 사용하고,
    COG를 계산해야 하는 경우에도 단위벡터 가우시안 스무딩을 적용하여 유려한 각도 반환."""
    hd = np.array([m.get("hd", 0.0) for m in meta], dtype=np.float64)
    x = np.array([m.get("x", 0.0) for m in meta], dtype=np.float64)
    y = np.array([m.get("y", 0.0) for m in meta], dtype=np.float64)

    valid_hd = np.isfinite(hd) & (hd >= 0.0) & (hd <= 360.0)
    has_valid_hd = (np.sum(valid_hd) >= 0.5 * len(meta)) and (float(np.nanstd(hd[valid_hd])) > 0.5)

    if has_valid_hd and not force_cog:
        print("  heading: 메타데이터 헤딩 사용 (%.1f ~ %.1f deg)"
              % (np.nanmin(hd[valid_hd]), np.nanmax(hd[valid_hd])))
        return hd

    n = len(x)
    ref_y = float(np.nanmedian(y)) if n > 0 else 35.0
    m_lat = 111132.0
    m_lon = 111132.0 * math.cos(math.radians(ref_y))
    east = (x - float(np.nanmedian(x))) * m_lon
    north = (y - ref_y) * m_lat

    win = max(7, min(61, n if n % 2 == 1 else n - 1))
    radius = win // 2
    kx = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (kx / max(win / 4.0, 1.0)) ** 2)
    kernel /= kernel.sum()

    sm_e = np.convolve(np.pad(east, radius, mode="edge"), kernel, mode="same")[radius : radius + n]
    sm_n = np.convolve(np.pad(north, radius, mode="edge"), kernel, mode="same")[radius : radius + n]

    step = max(1, min(10, n // 100))
    pad_e = np.pad(sm_e, step, mode="edge")
    pad_n = np.pad(sm_n, step, mode="edge")
    de = pad_e[2 * step:] - pad_e[:-2 * step]
    dn = pad_n[2 * step:] - pad_n[:-2 * step]
    mag = np.maximum(np.hypot(de, dn), 1e-6)
    ux = de / mag
    uy = dn / mag

    sm_ux = np.convolve(np.pad(ux, radius, mode="edge"), kernel, mode="same")[radius : radius + n]
    sm_uy = np.convolve(np.pad(uy, radius, mode="edge"), kernel, mode="same")[radius : radius + n]
    cog = np.degrees(np.arctan2(sm_ux, sm_uy)) % 360.0

    print("  heading: 부드러운 스무딩 항적 COG 사용 (%.1f ~ %.1f deg)"
          % (cog.min(), cog.max()))
    return cog


def project(x, y, epsg):
    if float(np.nanmax(np.abs(x))) > 360.0:
        return x, y
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:%d" % epsg, always_xy=True)
    ex, ny = tr.transform(x, y)
    print("  좌표: 경위도 -> EPSG:%d" % epsg)
    return np.asarray(ex, dtype=np.float64), np.asarray(ny, dtype=np.float64)

def build(cfg):
    full_cfg = dict(DEF)
    if isinstance(cfg, dict):
        full_cfg.update(cfg)
    cfg = full_cfg

    res = float(cfg.get("res", 0.2))
    step = max(1, int(cfg.get("stride", cfg.get("ping_stride", 1))))
    xs, ys, vs = [], [], []

    for fp in cfg["files"]:
        print("[읽는 중]", os.path.basename(fp))
        P, S, meta = read_xtf(fp, bool(cfg["flip_port"]), bool(cfg.get("swap_channels", False)))
        n = len(meta)
        print("  pings=%d port=%s stbd=%s" % (n, P.shape, S.shape))

        nz = np.concatenate([P[P > 0][:50000], S[S > 0][:50000]])
        if nz.size:
            print("  진폭 min/med/max = %.4g / %.4g / %.4g"
                  % (nz.min(), np.median(nz), nz.max()))
        if cfg["avg"]:
            P = avg_correct(P)
            S = avg_correct(S)
            print("  AVG 보정 적용")

        X = np.array([m["x"] for m in meta], dtype=np.float64)
        Y = np.array([m["y"] for m in meta], dtype=np.float64)
        AL = np.array([m["alt"] for m in meta], dtype=np.float64)
        RP = np.array([m["rp"] for m in meta], dtype=np.float64)
        RS = np.array([m["rs"] for m in meta], dtype=np.float64)
        HD = np.radians(headings(meta, bool(cfg["cog"])))

        RS = np.where(RS > 0, RS, RP)
        RP = np.where(RP > 0, RP, RS)
        AL = np.where(np.isfinite(AL) & (AL >= cfg["minalt"]),
                      AL, float(cfg["minalt"]))

        X, Y = project(X, Y, int(cfg["epsg"]))
        good = np.isfinite(X) & np.isfinite(Y) & ~((X == 0) & (Y == 0))
        if good.sum() < 2:
            print("  [건너뜀] 유효 좌표 없음")
            continue
        span = math.hypot(float(X[good].max() - X[good].min()),
                          float(Y[good].max() - Y[good].min()))
        print("  항적 범위 %.1f m, 유효 %d/%d" % (span, good.sum(), n))

        jobs = []
        if cfg["port"]:
            jobs.append((P, RP, -1.0))
        if cfg["stbd"]:
            jobs.append((S, RS, 1.0))

        used = 0
        for i in range(0, n, step):
            if not good[i]:
                continue
            e, nn = X[i], Y[i]
            alt = float(AL[i])
            sx, sy = math.cos(HD[i]), -math.sin(HD[i])
            for arr, rng, sign in jobs:
                rg = float(rng[i])
                if rg <= 0.0:
                    continue
                d = arr[i]
                m0 = d > 0
                if not m0.any():
                    continue
                sr = np.linspace(0.0, rg, d.size, dtype=np.float64)
                sr, d = sr[m0], d[m0]
                if cfg["removewc"]:
                    m = sr >= alt * float(cfg["wc"])
                    sr, d = sr[m], d[m]
                    if sr.size == 0:
                        continue
                if cfg["slant"]:
                    gr = np.sqrt(np.maximum(sr * sr - alt * alt, 0.0))
                else:
                    gr = sr
                if cfg["maxrange"] > 0.0:
                    m = gr <= float(cfg["maxrange"])
                    gr, d = gr[m], d[m]
                    if gr.size == 0:
                        continue
                xs.append(e + sign * sx * gr)
                ys.append(nn + sign * sy * gr)
                vs.append(d)
                used += 1
        print("  사용된 트레이스: %d" % used)

    if not xs:
        raise RuntimeError("유효한 점이 없습니다.")

    X = np.concatenate(xs)
    Y = np.concatenate(ys)
    V = np.concatenate(vs).astype(np.float64)
    m = np.isfinite(X) & np.isfinite(Y) & np.isfinite(V) & (V > 0)
    X, Y, V = X[m], Y[m], V[m]
    if X.size == 0:
        raise RuntimeError("모든 점이 무효입니다.")
    print("총 점 수: %d" % X.size)

    x0 = float(X.min()) - res
    y1 = float(Y.max()) + res
    W = max(1, int(math.ceil((float(X.max()) + res - x0) / res)))
    H = max(1, int(math.ceil((y1 - (float(Y.min()) - res)) / res)))
    print("래스터: %d x %d px" % (W, H))
    if W * H > 400000000:
        raise RuntimeError("래스터가 너무 큽니다. --res 를 키우세요.")

    col = np.clip(((X - x0) / res).astype(np.int64), 0, W - 1)
    row = np.clip(((y1 - Y) / res).astype(np.int64), 0, H - 1)
    flat = row * W + col
    acc = np.bincount(flat, weights=V, minlength=W * H)
    cnt = np.bincount(flat, minlength=W * H)
    ok = cnt > 0
    img = np.full(W * H, np.nan, dtype=np.float32)
    img[ok] = (acc[ok] / cnt[ok]).astype(np.float32)
    print("채워진 픽셀: %.2f%%" % (100.0 * ok.sum() / float(ok.size)))

    img = img.reshape(H, W)
    if int(cfg["fill"]) > 0:
        img = fill_holes(img, int(cfg["fill"]))
    return stretch(img, float(cfg["lo"]), float(cfg["hi"])), x0, y1, res


def fill_holes(img, it=1):
    """NaN 픽셀을 이웃 평균으로 메운다 (작은 틈 제거)."""
    out = img.copy()
    for _ in range(max(0, it)):
        nan = ~np.isfinite(out)
        if not nan.any():
            break
        v = np.where(nan, 0.0, out).astype(np.float32)
        c = (~nan).astype(np.float32)
        sv = np.zeros_like(v)
        sc = np.zeros_like(c)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sv += np.roll(np.roll(v, dy, 0), dx, 1)
                sc += np.roll(np.roll(c, dy, 0), dx, 1)
        f = nan & (sc > 0)
        out[f] = sv[f] / sc[f]
    return out


def stretch(img, lo_p=2.0, hi_p=98.0):
    """percentile 스트레치 -> uint8 (0 = nodata)."""
    v = img[np.isfinite(img)]
    if v.size == 0:
        return np.zeros(img.shape, dtype=np.uint8)
    lo, hi = np.percentile(v, (lo_p, hi_p))
    if hi <= lo:
        hi = lo + 1e-6
    print("  스트레치 %.4g ~ %.4g" % (lo, hi))
    g = (img - lo) / (hi - lo) * 254.0 + 1.0
    g = np.where(np.isfinite(img), np.clip(g, 1, 255), 0)
    return g.astype(np.uint8)


def save(img, x0, y1, res, cfg):
    name = cfg["name"] or "mosaic"
    outdir = os.path.join(cfg["outdir"], name)
    os.makedirs(outdir, exist_ok=True)

    try:
        from PIL import Image
        Image.fromarray(img).save(os.path.join(outdir, name + "_preview.png"))
    except Exception:
        pass

    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError:
        npy = os.path.join(outdir, name + "_mosaic.npy")
        np.save(npy, img)
        lines = ["%.10f" % res, "0.0", "0.0", "%.10f" % (-res),
                 "%.6f" % (x0 + res / 2.0), "%.6f" % (y1 - res / 2.0)]
        with open(os.path.join(outdir, name + "_mosaic.pgw"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("[알림] rasterio 없음 -> npy + worldfile 저장")
        return npy

    tif = os.path.join(outdir, name + "_mosaic.tif")
    with rasterio.open(tif, "w", driver="GTiff",
                       height=img.shape[0], width=img.shape[1],
                       count=1, dtype="uint8",
                       crs="EPSG:%d" % int(cfg["epsg"]),
                       transform=from_origin(x0, y1, res, res),
                       nodata=0, compress="lzw") as ds:
        ds.write(img, 1)
    return tif


def parse(argv):
    cfg = dict(DEF)
    pair = {"--res": ("res", float), "--epsg": ("epsg", int),
            "--stride": ("stride", int), "--maxrange": ("maxrange", float),
            "--minalt": ("minalt", float), "--wc": ("wc", float),
            "--lo": ("lo", float), "--hi": ("hi", float),
            "--fill": ("fill", int), "--name": ("name", str),
            "--outdir": ("outdir", str)}
    flag = {"--noflip": ("flip_port", False), "--noslant": ("slant", False),
            "--nowc": ("removewc", False), "--noavg": ("avg", False),
            "--cog": ("cog", True), "--portonly": ("stbd", False),
            "--stbdonly": ("port", False)}
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in pair and i + 1 < len(argv):
            key, cast = pair[a]
            cfg[key] = cast(argv[i + 1])
            i += 2
        elif a in flag:
            key, val = flag[a]
            cfg[key] = val
            i += 1
        else:
            rest.append(a)
            i += 1
    files = [a for a in rest
             if a.lower().endswith(".xtf") and os.path.isfile(a)]
    cfg["files"] = files or sorted(glob.glob("*.xtf"))
    return cfg


def main():
    cfg = parse(sys.argv[1:])
    if not cfg["files"]:
        print("XTF 파일이 없습니다.")
        return 1
    if not cfg["name"]:
        cfg["name"] = os.path.splitext(os.path.basename(cfg["files"][0]))[0]
    img, x0, y1, res = build(cfg)
    print("생성 완료:", save(img, x0, y1, res, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())