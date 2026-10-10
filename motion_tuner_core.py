# -*- coding: utf-8 -*-
"""
Created on Sun Aug  9 19:04:03 2026

@author: swath
"""

"""motion_tuner_core.py - 수동 모션 보정 계산 코어 (numpy 전용, Qt 비의존)

모델:
    gain_db(ping, sample) = w(sample) * SUM_t  g_t * term_t(ping - lag_t)

  * term_t : roll / pitch / roll_rate / pitch_rate 의 이동평균 디트렌드 결과
  * g_t    : 항별 게인 [dB per unit]
  * lag_t  : 항별 ping 지연
  * w      : nadir 0 -> far 1 거리 램프 (near/far smoothstep + tail 감쇠)

공개 API:
    ManualModel, TermCache, range_weight, gain_map, apply_gain,
    level_std, score, seed_from_fit, to_image8, diff_image8,
    level_curve, quick_stats
"""

from __future__ import annotations

import numpy as np

TERMS = ("roll", "pitch", "roll_rate", "pitch_rate")

_ALIAS = {
    "roll": ("roll", "roll_deg", "Roll", "ROLL"),
    "pitch": ("pitch", "pitch_deg", "Pitch", "PITCH"),
    "roll_rate": ("roll_rate", "rollrate", "RollRate"),
    "pitch_rate": ("pitch_rate", "pitchrate", "PitchRate"),
}


# ---------------------------------------------------------------- 기본 유틸
def moving_average(x, win):
    """홀수 창 이동평균 (edge padding, scipy 불필요)."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n == 0:
        return x.copy()
    w = int(max(1, win))
    if w % 2 == 0:
        w += 1
    if w <= 1 or w >= 2 * n:
        return np.full(n, float(np.nanmean(x)) if np.isfinite(x).any() else 0.0)
    h = w // 2
    p = np.pad(x, h, mode="edge")
    c = np.cumsum(np.insert(p, 0, 0.0))
    return (c[w:] - c[:-w]) / float(w)


def interp_nan(x):
    """NaN 을 선형 보간으로 채운다."""
    x = np.asarray(x, dtype=float).copy()
    m = np.isfinite(x)
    if not m.any():
        return np.zeros_like(x)
    if m.all():
        return x
    idx = np.arange(x.size)
    x[~m] = np.interp(idx[~m], idx[m], x[m])
    return x


def _pick(motion, names):
    """dict / 객체 어느 쪽이든 첫 번째로 존재하는 필드를 반환."""
    if motion is None:
        return None
    for nm in names:
        if isinstance(motion, dict):
            if nm in motion and motion[nm] is not None:
                return np.asarray(motion[nm], dtype=float).ravel()
        else:
            v = getattr(motion, nm, None)
            if v is not None:
                return np.asarray(v, dtype=float).ravel()
    return None


def _fit_len(v, n):
    """길이 n 으로 맞춘다 (부족/초과 시 선형 리샘플)."""
    v = interp_nan(np.asarray(v, dtype=float).ravel())
    if v.size == n:
        return v
    if v.size == 0:
        return np.zeros(n)
    if v.size == 1:
        return np.full(n, float(v[0]))
    src = np.linspace(0.0, 1.0, v.size)
    dst = np.linspace(0.0, 1.0, n)
    return np.interp(dst, src, v)


def _term_series(motion, name, n):
    """항 원본 시계열 (없으면 각도 미분으로 rate 생성, 그래도 없으면 0)."""
    v = _pick(motion, _ALIAS.get(name, (name,)))
    if v is None and name.endswith("_rate"):
        base = _pick(motion, _ALIAS.get(name[:-5], ()))
        if base is not None:
            b = _fit_len(base, n)
            v = np.gradient(b) if b.size > 2 else np.zeros(n)
    if v is None:
        return np.zeros(n)
    return _fit_len(v, n)


# ---------------------------------------------------------------- 모델
class ManualModel(object):
    """슬라이더로 조정하는 수동 보정 파라미터 묶음."""

    def __init__(self):
        self.gains = {k: 0.0 for k in TERMS}
        self.lags = {k: 0 for k in TERMS}
        self.near = 0.35
        self.far = 0.55
        self.tail = 1.0
        self.max_db = 6.0
        self.enabled = True

    def copy(self):
        m = ManualModel()
        m.gains = dict(self.gains)
        m.lags = dict(self.lags)
        m.near, m.far, m.tail = self.near, self.far, self.tail
        m.max_db, m.enabled = self.max_db, self.enabled
        return m

    def to_dict(self):
        return {"gains": {k: float(self.gains.get(k, 0.0)) for k in TERMS},
                "lags": {k: int(self.lags.get(k, 0)) for k in TERMS},
                "near": float(self.near), "far": float(self.far),
                "tail": float(self.tail), "max_db": float(self.max_db),
                "enabled": bool(self.enabled)}

    @staticmethod
    def from_dict(d):
        m = ManualModel()
        if not isinstance(d, dict):
            return m
        g = d.get("gains") or {}
        k = d.get("lags") or {}
        for nm in TERMS:
            try:
                m.gains[nm] = float(g.get(nm, 0.0))
            except Exception:
                m.gains[nm] = 0.0
            try:
                m.lags[nm] = int(k.get(nm, 0))
            except Exception:
                m.lags[nm] = 0
        for nm, dv in (("near", 0.35), ("far", 0.55), ("tail", 1.0),
                       ("max_db", 6.0)):
            try:
                setattr(m, nm, float(d.get(nm, dv)))
            except Exception:
                setattr(m, nm, dv)
        m.enabled = bool(d.get("enabled", True))
        return m


# ---------------------------------------------------------------- 항 캐시
class TermCache(object):
    """(항, lag) 조합의 디트렌드 시계열을 캐시해 슬라이더 반응을 확보한다."""

    def __init__(self, motion, nping, win=101):
        self.n = int(max(1, nping))
        self.win = int(max(3, win))
        self.raw, self.base, self._c = {}, {}, {}
        for nm in TERMS:
            s = _term_series(motion, nm, self.n)
            self.raw[nm] = s
            self.base[nm] = s - moving_average(s, self.win)

    def get(self, name, lag=0):
        """term(ping - lag). 경계는 값 유지(clamp)."""
        nm = str(name)
        b = self.base.get(nm)
        if b is None:
            return np.zeros(self.n)
        lg = int(lag)
        if lg == 0:
            return b
        key = (nm, lg)
        v = self._c.get(key)
        if v is None:
            idx = np.clip(np.arange(self.n) - lg, 0, self.n - 1)
            v = b[idx]
            if len(self._c) > 256:
                self._c.clear()
            self._c[key] = v
        return v

    def std(self, name):
        b = self.base.get(str(name))
        return float(np.std(b)) if b is not None and b.size else 0.0


# ---------------------------------------------------------------- 거리 가중
def range_weight(nsample, near=0.35, far=0.55, tail=1.0, nadir_first=True):
    """nadir 0 -> far 1 의 smoothstep 램프 + 말단 tail 감쇠."""
    n = int(max(1, nsample))
    x = np.linspace(0.0, 1.0, n)
    a = float(min(max(near, 0.0), 1.0))
    b = float(min(max(far, 0.0), 1.0))
    if b <= a:
        b = min(1.0, a + 1e-3)
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)
    tl = float(tail)
    if abs(tl - 1.0) > 1e-6 and b < 1.0:
        r = np.clip((x - b) / (1.0 - b), 0.0, 1.0)
        w = w * (1.0 + (tl - 1.0) * r)
    if not nadir_first:
        w = w[::-1]
    return w.astype(float)


# ---------------------------------------------------------------- 게인
def gain_map(model, cache, nsample, nadir_first=True):
    """(nping, nsample) dB 게인 맵을 만든다."""
    n = int(getattr(cache, "n", 0))
    m = int(max(1, nsample))
    if n <= 0:
        return np.zeros((0, m))
    if model is None or not getattr(model, "enabled", True):
        return np.zeros((n, m))
    acc = np.zeros(n)
    for nm in TERMS:
        g = float(model.gains.get(nm, 0.0))
        if abs(g) < 1e-12:
            continue
        acc += g * cache.get(nm, int(model.lags.get(nm, 0)))
    if not np.any(acc):
        return np.zeros((n, m))
    w = range_weight(m, model.near, model.far, model.tail, nadir_first)
    G = np.outer(acc, w)
    lim = abs(float(model.max_db))
    if lim > 0:
        np.clip(G, -lim, lim, out=G)
    return G


def apply_gain(amp, gain_db):
    """진폭에 dB 게인을 적용한다 (보정 = 원본 * 10^(-G/20))."""
    A = np.asarray(amp, dtype=float)
    G = np.asarray(gain_db, dtype=float)
    if G.size == 0 or A.size == 0:
        return A.copy()
    if G.shape != A.shape:
        G = np.resize(G, A.shape)
    f = np.power(10.0, -G / 20.0)
    out = A * f
    return np.where(np.isfinite(out), out, 0.0)


# ---------------------------------------------------------------- 지표
def level_curve(amp, eps=1e-6):
    """ping 별 평균 레벨 [dB]."""
    A = np.asarray(amp, dtype=float)
    if A.ndim != 2 or A.size == 0:
        return np.zeros(0)
    v = np.where(np.isfinite(A) & (A > 0), A, np.nan)
    with np.errstate(invalid="ignore"):
        mu = np.nanmean(v, axis=1)
    mu = interp_nan(mu)
    return 20.0 * np.log10(np.maximum(mu, eps))


def level_std(amp, win=101):
    """ping 간 레벨 변동(디트렌드 후 표준편차) [dB]. 작을수록 균질."""
    L = level_curve(amp)
    if L.size < 3:
        return 0.0
    r = L - moving_average(L, win)
    return float(np.std(r))


def score(std0, std1):
    """감소율 [%]. 음수면 개선."""
    s0 = float(std0)
    if s0 <= 1e-9:
        return 0.0
    return (float(std1) - s0) / s0 * 100.0


def quick_stats(amp, corrected=None, win=101):
    """튜너 하단 표시용 요약 dict."""
    s0 = level_std(amp, win)
    d = {"std_raw": s0, "std_cor": s0, "gain_pct": 0.0}
    if corrected is not None:
        s1 = level_std(corrected, win)
        d["std_cor"] = s1
        d["gain_pct"] = score(s0, s1)
    return d


# ---------------------------------------------------------------- 이미지
def to_image8(amp, lo=2.0, hi=98.0):
    """percentile 스트레치 uint8 이미지와 (lo, hi) 값을 반환."""
    A = np.asarray(amp, dtype=float)
    if A.ndim != 2 or A.size == 0:
        return np.zeros((1, 1), dtype=np.uint8), (0.0, 1.0)
    v = A[np.isfinite(A)]
    v = v[v > 0]
    if v.size < 16:
        return np.zeros(A.shape, dtype=np.uint8), (0.0, 1.0)
    a = float(np.percentile(v, lo))
    b = float(np.percentile(v, hi))
    if b <= a:
        b = a + 1e-6
    g = (np.clip(A, a, b) - a) / (b - a)
    g = np.where(np.isfinite(g), g, 0.0)
    return (g * 255.0 + 0.5).astype(np.uint8), (a, b)


def diff_image8(amp, corrected, span_db=3.0):
    """보정/원본 비율을 ±span_db 로 정규화한 uint8 차이맵 (128 = 변화 없음)."""
    A = np.asarray(amp, dtype=float)
    B = np.asarray(corrected, dtype=float)
    if A.shape != B.shape or A.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    eps = 1e-6
    with np.errstate(divide="ignore", invalid="ignore"):
        d = 20.0 * np.log10(np.maximum(B, eps) / np.maximum(A, eps))
    d = np.where(np.isfinite(d), d, 0.0)
    s = abs(float(span_db)) or 1.0
    g = np.clip(d, -s, s) / s * 0.5 + 0.5
    return (g * 255.0 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------- 시드
def seed_from_fit(fit):
    """motion_gain 의 자동 적합 결과를 슬라이더 초기값으로 변환."""
    m = ManualModel()
    if fit is None:
        return m
    d = fit if isinstance(fit, dict) else getattr(fit, "__dict__", {})
    coef = d.get("coef") or d.get("coefs") or d.get("far_coef") or {}
    lags = d.get("lags") or d.get("lag") or {}
    if isinstance(coef, dict):
        for nm in TERMS:
            try:
                m.gains[nm] = float(coef.get(nm, 0.0))
            except Exception:
                m.gains[nm] = 0.0
    if isinstance(lags, dict):
        for nm in TERMS:
            try:
                m.lags[nm] = int(lags.get(nm, 0))
            except Exception:
                m.lags[nm] = 0
    for k in ("near", "far", "tail", "max_db"):
        if k in d:
            try:
                setattr(m, k, float(d[k]))
            except Exception:
                pass
    return m