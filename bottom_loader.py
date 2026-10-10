
# -*- coding: utf-8 -*-
"""bottom_loader.py — 해저면(bottom / altitude) 자동 트래킹 + 사이드카 연동

설계 원칙
---------
  * XTF 원본은 절대 수정하지 않는다.
  * 사이드카 규약은 기존 processing/bottom_io.py 와 100% 동일:
        <xtf 폴더>/bottom/<원본명>.bottom.csv
        헤더: ping_no,altitude_m
    → 뷰어에서 수동 편집한 바탐을 모자이크가 그대로 사용한다.
  * 우선순위:  ① 사이드카(수동 편집) → ② XTF 헤더 SensorPrimaryAltitude
               → ③ 자동 bottom tracking → ④ 기본값

공개 API
--------
  [사이드카]
    bottom_path(xtf_path)                  -> str
    load_bottom(xtf_path)                  -> dict{ping_no: alt} | None
    save_bottom(xtf_path, pings)           -> str
    sync_bottom(xtf_path, pings)           -> bool  (True=사이드카 적용됨)
    apply_bottom_map(pings, alt_map)       -> int   (적용된 핑 수)

  [자동 추적]
    detect_altitude_bottom_track(trace, slant_range, ...) -> float
    track_bottom_ping(ping, ...)                          -> float
    track_bottom_all(pings, ...)                          -> np.ndarray
    ensure_altitudes(pings, xtf_path=None, ...)           -> np.ndarray

  [1D 신호 유틸]
    interp_nan(a) / median_filter1d(a, k) / moving_average(a, k)
    clean_bottom(alt, ...)

ping dict 규약 (xtf_processor 와 공유)
    port_data, stbd_data : np.float32 1D (index 0 = nadir, 바깥으로 증가)
    port_range, stbd_range : float (m)
    altitude : float (m)
    ping_no  : int

의존성 : numpy (필수) / pyxtf, matplotlib (CLI 전용, 선택)
단독 실행 :
    python bottom_loader.py <file.xtf> [--save] [--plot] [--force]
"""

from __future__ import annotations

import os
import sys
import csv

import numpy as np


# ==================================================== 상수
MIN_ALTITUDE = 0.5          # m — 이보다 작으면 무효
MAX_ALTITUDE = 500.0        # m — 이보다 크면 무효
DEFAULT_ALTITUDE = 1.0      # m — 최후 fallback

_SMOOTH_K = 9               # 트레이스 평활 커널
_RUN_LEN = 5                # 임계 초과가 연속되어야 하는 샘플 수
_THRESH_FACTOR = 0.28       # noise + f*(peak-noise)


# ==================================================== 1D 유틸
def interp_nan(arr):
    """NaN 을 선형보간으로 채운다. 전부 NaN 이면 그대로 반환."""
    a = np.asarray(arr, dtype=np.float64).copy()
    if a.size == 0:
        return a
    good = np.isfinite(a)
    if not np.any(good) or np.all(good):
        return a
    idx = np.arange(a.size)
    a[~good] = np.interp(idx[~good], idx[good], a[good])
    return a


def _windows(a, k):
    """(n, k) sliding window (reflect 패딩). numpy 구버전 fallback 포함."""
    n = a.size
    half = k // 2
    pad = np.pad(a, half, mode='reflect')
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return sliding_window_view(pad, k)
    except Exception:
        out = np.empty((n, k), dtype=np.float64)
        for i in range(k):
            out[:, i] = pad[i:i + n]
        return out


def median_filter1d(arr, k=7):
    """SciPy 없이 동작하는 1D 중앙값 필터."""
    a = np.asarray(arr, dtype=np.float64)
    k = int(k)
    if a.size == 0 or k < 3:
        return a.copy()
    if k % 2 == 0:
        k += 1
    if k > a.size:
        k = a.size if a.size % 2 == 1 else max(3, a.size - 1)
    return np.nanmedian(_windows(a, k), axis=1)


def moving_average(arr, k=5):
    """단순 이동평균 (reflect 패딩)."""
    a = np.asarray(arr, dtype=np.float64)
    k = int(k)
    if a.size == 0 or k < 2:
        return a.copy()
    if k % 2 == 0:
        k += 1
    if k > a.size:
        k = a.size if a.size % 2 == 1 else max(3, a.size - 1)
    half = k // 2
    pad = np.pad(a, half, mode='reflect')
    kern = np.ones(k, dtype=np.float64) / float(k)
    return np.convolve(pad, kern, mode='valid')[:a.size]


def clean_bottom(alt, min_alt=MIN_ALTITUDE, max_alt=MAX_ALTITUDE,
                 median_k=9, smooth_k=5, mad_thresh=4.0):
    """바탐 시계열 정리: 범위 밖/스파이크 → NaN → 보간 → 중앙값 → 평활."""
    a = np.asarray(alt, dtype=np.float64).copy()
    if a.size == 0:
        return np.zeros(0, dtype=np.float32)

    a[~np.isfinite(a)] = np.nan
    a[(a < float(min_alt)) | (a > float(max_alt))] = np.nan

    if np.any(np.isfinite(a)) and mad_thresh and mad_thresh > 0:
        base = median_filter1d(interp_nan(a), max(5, int(median_k)))
        resid = a - base
        fin = np.isfinite(resid)
        if np.any(fin):
            med = np.nanmedian(resid[fin])
            mad = np.nanmedian(np.abs(resid[fin] - med))
            sigma = 1.4826 * mad
            if sigma > 1e-6:
                a[np.abs(resid - med) > float(mad_thresh) * sigma] = np.nan

    if not np.any(np.isfinite(a)):
        return np.full(a.size, float(DEFAULT_ALTITUDE), dtype=np.float32)

    a = interp_nan(a)
    if median_k and median_k >= 3:
        a = median_filter1d(a, median_k)
    if smooth_k and smooth_k >= 3:
        a = moving_average(a, smooth_k)

    return np.clip(a, float(min_alt), float(max_alt)).astype(np.float32)


# ==================================================== 사이드카 (bottom_io 호환)
def bottom_path(xtf_path):
    """<xtf 폴더>/bottom/<원본명>.bottom.csv"""
    d = os.path.join(os.path.dirname(os.path.abspath(str(xtf_path))), "bottom")
    name = os.path.splitext(os.path.basename(str(xtf_path)))[0]
    return os.path.join(d, name + ".bottom.csv")


def _ping_no(p, fallback):
    """ping dict / meta dict 어디서든 핑 번호를 얻는다."""
    for key in ("ping_no", "ping_number", "PingNumber", "ping"):
        v = p.get(key) if isinstance(p, dict) else None
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return int(fallback)


def load_bottom(xtf_path):
    """사이드카가 있으면 {ping_no: altitude} dict, 없으면 None."""
    path = bottom_path(xtf_path)
    if not os.path.exists(path):
        return None
    alt = {}
    try:
        with open(path, newline="", encoding="utf-8") as fp:
            r = csv.reader(fp)
            next(r, None)                        # 헤더 skip
            for row in r:
                if len(row) < 2:
                    continue
                try:
                    alt[int(float(row[0]))] = float(row[1])
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    return alt if alt else None


def save_bottom(xtf_path, pings):
    """pings(또는 meta)의 현재 altitude 를 사이드카에 기록하고 경로 반환."""
    path = bottom_path(xtf_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["ping_no", "altitude_m"])
        for i, p in enumerate(pings):
            a = p.get("altitude", 0.0)
            try:
                a = float(a)
            except (TypeError, ValueError):
                a = 0.0
            if not np.isfinite(a):
                a = 0.0
            w.writerow([_ping_no(p, i), "%.3f" % a])
    return path


def apply_bottom_map(pings, alt_map):
    """{ping_no: alt} 를 ping dict 에 주입. 적용된 개수 반환."""
    if not alt_map:
        return 0
    cnt = 0
    for i, p in enumerate(pings):
        a = alt_map.get(_ping_no(p, i))
        if a is None:
            continue
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        if np.isfinite(a) and MIN_ALTITUDE <= a <= MAX_ALTITUDE:
            p["altitude"] = a
            cnt += 1
    return cnt


def sync_bottom(xtf_path, pings):
    """열기/재로드 직후 호출.
      사이드카 있음 → altitude 덮어쓰기 (True 반환)
      사이드카 없음 → 현재 altitude 로 신규 생성 (False 반환)
    """
    alt = load_bottom(xtf_path)
    if alt is None:
        save_bottom(xtf_path, pings)
        return False
    apply_bottom_map(pings, alt)
    return True


# ==================================================== 자동 bottom tracking
def detect_altitude_bottom_track(trace, slant_range,
                                 min_alt=MIN_ALTITUDE, max_alt=MAX_ALTITUDE,
                                 smooth_k=_SMOOTH_K, run_len=_RUN_LEN,
                                 thresh_factor=_THRESH_FACTOR):
    """단일 트레이스에서 해저면 first return 의 slant altitude(m) 추정.

    trace : 1D 강도 배열. index 0 = nadir, 바깥쪽으로 증가한다고 가정.
    실패 시 np.nan 반환.
    """
    a = np.asarray(trace, dtype=np.float64).ravel()
    n = a.size
    try:
        sr = float(slant_range)
    except (TypeError, ValueError):
        return float('nan')
    if n < 16 or not np.isfinite(sr) or sr <= 0.0:
        return float('nan')

    a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
    a = np.abs(a)
    if not np.any(a > 0):
        return float('nan')

    spacing = sr / float(n)                     # m / sample
    if spacing <= 0.0:
        return float('nan')

    env = moving_average(a, max(3, int(smooth_k)))

    i0 = int(max(1, np.floor(float(min_alt) / spacing)))
    i1 = int(min(n, np.ceil(min(float(max_alt), sr) / spacing)))
    if i1 - i0 < max(8, int(run_len) * 2):
        i0 = max(1, min(i0, n - 8))
        i1 = n
        if i1 - i0 < 8:
            return float('nan')

    seg = env[i0:i1]
    if seg.size == 0 or not np.any(np.isfinite(seg)):
        return float('nan')

    # 수주(water column) 잡음 레벨: nadir 직전 구간 + 세그먼트 하위 분포
    wc = env[:i0]
    noise_cands = []
    if wc.size >= 4:
        noise_cands.append(float(np.nanmedian(wc)))
    noise_cands.append(float(np.nanpercentile(seg, 20.0)))
    noise = float(np.nanmin(noise_cands))
    peak = float(np.nanpercentile(seg, 99.0))
    if not np.isfinite(noise):
        noise = 0.0
    if not np.isfinite(peak) or peak <= noise:
        return float('nan')

    thr = noise + float(thresh_factor) * (peak - noise)

    over = (seg > thr).astype(np.float64)
    rl = max(1, int(run_len))
    if over.size >= rl:
        run = np.convolve(over, np.ones(rl, dtype=np.float64), mode='valid')
        hits = np.flatnonzero(run >= rl - 0.5)
    else:
        hits = np.flatnonzero(over > 0.5)
    if hits.size == 0:
        # 완화된 임계로 재시도
        thr2 = noise + 0.5 * float(thresh_factor) * (peak - noise)
        hits = np.flatnonzero(seg > thr2)
        if hits.size == 0:
            return float('nan')

    k = int(hits[0])

    # leading edge 로 후퇴: noise + 0.1*(peak-noise) 아래로 떨어지는 지점까지
    low = noise + 0.10 * (peak - noise)
    j = k
    while j > 0 and seg[j - 1] > low:
        j -= 1

    alt = (i0 + j) * spacing
    if not np.isfinite(alt):
        return float('nan')
    if alt < float(min_alt) or alt > min(float(max_alt), sr):
        return float('nan')
    return float(alt)


def track_bottom_ping(ping, min_alt=MIN_ALTITUDE, max_alt=MAX_ALTITUDE,
                      smooth_k=_SMOOTH_K, run_len=_RUN_LEN,
                      thresh_factor=_THRESH_FACTOR):
    """port / stbd 양쪽에서 추정 후 결합(median). 실패 시 NaN."""
    if not isinstance(ping, dict):
        return float('nan')

    vals = []
    for dkey, rkey in (("port_data", "port_range"),
                       ("stbd_data", "stbd_range")):
        data = ping.get(dkey)
        if data is None:
            continue
        arr = np.asarray(data)
        if arr.size < 16:
            continue
        v = detect_altitude_bottom_track(
            arr, ping.get(rkey, 0.0), min_alt=min_alt, max_alt=max_alt,
            smooth_k=smooth_k, run_len=run_len, thresh_factor=thresh_factor)
        if np.isfinite(v):
            vals.append(v)

    if not vals:
        return float('nan')
    if len(vals) == 2:
        # 두 값이 크게 어긋나면 작은 쪽(더 이른 first return)을 신뢰
        if abs(vals[0] - vals[1]) > 0.35 * max(vals):
            return float(min(vals))
    return float(np.median(vals))


def track_bottom_all(pings, min_alt=MIN_ALTITUDE, max_alt=MAX_ALTITUDE,
                     smooth_k=_SMOOTH_K, run_len=_RUN_LEN,
                     thresh_factor=_THRESH_FACTOR, clean=True,
                     progress=None):
    """모든 핑에 대해 자동 추적. (N,) float32 반환 (NaN 가능)."""
    n = len(pings)
    out = np.full(n, np.nan, dtype=np.float64)
    step = max(1, n // 100)
    for i, p in enumerate(pings):
        out[i] = track_bottom_ping(
            p, min_alt=min_alt, max_alt=max_alt, smooth_k=smooth_k,
            run_len=run_len, thresh_factor=thresh_factor)
        if progress is not None and (i % step == 0 or i == n - 1):
            try:
                if progress("bottom tracking %d/%d" % (i + 1, n),
                            i + 1, n) is False:
                    break
            except TypeError:
                progress("bottom tracking %d/%d" % (i + 1, n))
    if clean:
        return clean_bottom(out, min_alt=min_alt, max_alt=max_alt)
    return out.astype(np.float32)


# ==================================================== 통합 진입점
def ensure_altitudes(pings, xtf_path=None, use_sidecar=True,
                     auto_track=True, force_track=False,
                     min_alt=MIN_ALTITUDE, max_alt=MAX_ALTITUDE,
                     default_alt=DEFAULT_ALTITUDE, write_sidecar=False,
                     progress=None, verbose=True):
    """ping 리스트의 altitude 를 확정하고 (N,) float32 배열 반환.

    ① 사이드카(수동 편집) → ② 헤더값 → ③ 자동 추적 → ④ default_alt
    force_track=True 면 헤더/사이드카를 무시하고 전 핑 자동 추적.
    """
    n = len(pings)
    if n == 0:
        return np.zeros(0, dtype=np.float32)

    alt = np.full(n, np.nan, dtype=np.float64)

    # ---- ① 헤더값 수집 ----
    if not force_track:
        for i, p in enumerate(pings):
            try:
                v = float(p.get("altitude", 0.0))
            except (TypeError, ValueError):
                v = np.nan
            if np.isfinite(v) and min_alt <= v <= max_alt:
                alt[i] = v

    # ---- ② 사이드카 덮어쓰기 (수동 편집 최우선) ----
    n_side = 0
    if use_sidecar and xtf_path and not force_track:
        amap = load_bottom(xtf_path)
        if amap:
            for i, p in enumerate(pings):
                v = amap.get(_ping_no(p, i))
                if v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(v) and min_alt <= v <= max_alt:
                    alt[i] = v
                    n_side += 1

    n_bad = int(np.count_nonzero(~np.isfinite(alt)))

    # ---- ③ 자동 추적 (무효 핑만, 또는 강제 전체) ----
    n_track = 0
    if (auto_track and n_bad > 0) or force_track:
        targets = (np.arange(n) if force_track
                   else np.flatnonzero(~np.isfinite(alt)))
        step = max(1, targets.size // 100)
        for c, i in enumerate(targets):
            v = track_bottom_ping(pings[int(i)], min_alt=min_alt,
                                  max_alt=max_alt)
            if np.isfinite(v):
                alt[int(i)] = v
                n_track += 1
            if progress is not None and (c % step == 0 or
                                         c == targets.size - 1):
                try:
                    if progress("bottom tracking %d/%d" % (c + 1, targets.size),
                                c + 1, targets.size) is False:
                        break
                except TypeError:
                    progress("bottom tracking %d/%d" % (c + 1, targets.size))

    # ---- ④ 정리 + fallback ----
    if np.any(np.isfinite(alt)):
        alt = clean_bottom(alt, min_alt=min_alt, max_alt=max_alt).astype(
            np.float64)
    else:
        alt = np.full(n, float(default_alt), dtype=np.float64)

    alt[~np.isfinite(alt)] = float(default_alt)
    alt = np.clip(alt, float(min_alt), float(max_alt))

    for i, p in enumerate(pings):
        p["altitude"] = float(alt[i])

    if verbose:
        print("[bottom] pings=%d  sidecar=%d  tracked=%d  invalid_before=%d"
              % (n, n_side, n_track, n_bad))
        print("[bottom] altitude  min=%.2f  med=%.2f  max=%.2f (m)"
              % (float(np.min(alt)), float(np.median(alt)),
                 float(np.max(alt))))

    if write_sidecar and xtf_path:
        try:
            p = save_bottom(xtf_path, pings)
            if verbose:
                print("[bottom] 사이드카 저장: %s" % p)
        except OSError as e:
            print("[bottom] 사이드카 저장 실패: %s" % e)

    return alt.astype(np.float32)


# ==================================================== CLI
def _load_pings_for_cli(xtf_path):
    """xtf_processor 가 있으면 그것을, 없으면 최소 pyxtf 파서를 사용."""
    try:
        import xtf_processor
        fn = getattr(xtf_processor, "parse_xtf_file", None)
        if callable(fn):
            res = fn(xtf_path)
            if isinstance(res, tuple):
                return list(res[0])
            return list(res)
    except Exception as e:
        print("[cli] xtf_processor 사용 불가 (%s) → 내장 파서 사용" % e)

    import pyxtf
    _, packets = pyxtf.xtf_read(str(xtf_path))

    sonar = None
    if isinstance(packets, dict):
        for key in list(packets.keys()):
            name = str(key).lower()
            if "sonar" in name or key == 0:
                sonar = packets[key]
                break
        if sonar is None and packets:
            sonar = list(packets.values())[0]
    if not sonar:
        raise RuntimeError("sonar 패킷을 찾을 수 없습니다: %s" % xtf_path)

    pings = []
    for i, pk in enumerate(sonar):
        data = getattr(pk, "data", None)
        if data is None or len(data) < 1:
            continue
        port = np.asarray(data[0], dtype=np.float32)[::-1]
        stbd = (np.asarray(data[1], dtype=np.float32)
                if len(data) > 1 else np.zeros(0, dtype=np.float32))

        chans = getattr(pk, "ping_chan_headers", None) or []
        pr = sr = 0.0
        if len(chans) > 0:
            pr = float(getattr(chans[0], "SlantRange", 0.0) or 0.0)
        if len(chans) > 1:
            sr = float(getattr(chans[1], "SlantRange", 0.0) or 0.0)
        if pr <= 0.0:
            pr = sr
        if sr <= 0.0:
            sr = pr

        try:
            alt = float(getattr(pk, "SensorPrimaryAltitude", 0.0) or 0.0)
        except (TypeError, ValueError):
            alt = 0.0

        pings.append({
            "ping_no": int(getattr(pk, "PingNumber", i) or i),
            "port_data": port, "stbd_data": stbd,
            "port_range": pr, "stbd_range": sr,
            "altitude": alt,
        })
    return pings


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        print("사용법: python bottom_loader.py <file.xtf> "
              "[--save] [--plot] [--force]")
        return 1

    xtf_path = argv[0]
    do_save = "--save" in argv
    do_plot = "--plot" in argv
    force = "--force" in argv

    if not os.path.isfile(xtf_path):
        print("파일을 찾을 수 없습니다: %s" % xtf_path)
        return 2

    print("[cli] 로딩: %s" % xtf_path)
    pings = _load_pings_for_cli(xtf_path)
    print("[cli] 핑 수: %d" % len(pings))
    if not pings:
        return 3

    hdr = np.array([float(p.get("altitude", 0.0)) for p in pings],
                   dtype=np.float64)
    n_ok = int(np.count_nonzero((hdr >= MIN_ALTITUDE) & (hdr <= MAX_ALTITUDE)))
    print("[cli] 헤더 유효 altitude: %d / %d" % (n_ok, len(pings)))

    def _prog(msg, cur=None, total=None):
        if cur is not None and total and (cur % max(1, total // 10) == 0):
            print("  %s" % msg)
        return True

    alt = ensure_altitudes(pings, xtf_path=xtf_path, use_sidecar=True,
                           auto_track=True, force_track=force,
                           write_sidecar=do_save, progress=_prog)

    if do_plot:
        try:
            import matplotlib.pyplot as plt
            plt.figure(figsize=(11, 4))
            plt.plot(hdr, lw=0.8, alpha=0.55, label="header altitude")
            plt.plot(alt, lw=1.2, color="crimson", label="final altitude")
            plt.gca().invert_yaxis()
            plt.xlabel("ping index")
            plt.ylabel("altitude (m)")
            plt.title(os.path.basename(xtf_path))
            plt.legend()
            plt.grid(alpha=0.3)
            plt.tight_layout()
            plt.show()
        except Exception as e:
            print("[cli] plot 실패: %s" % e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
