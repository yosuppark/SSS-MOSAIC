# -*- coding: utf-8 -*-
"""
Created on Tue Jul 28 10:46:42 2026

@author: swath

AGC — 열(레인지 빈)별 along-track 이동평균 정규화."""
import numpy as np


def apply_agc(img_f, window=301, strength=1.0, eps=1e-3):
    """
    img_f    : float32 2D (n_pings, n_samples), 0 = 패딩(무효)
    window   : along-track 이동평균 창 크기 (핑 수, 홀수 권장)
    strength : 0.0(원본) ~ 1.0(완전 평탄화)
    반환     : AGC 적용된 float32 배열 (패딩은 0 유지)
    """
    valid = img_f > 0
    s = np.where(valid, img_f, 0.0).astype(np.float32)
    v = valid.astype(np.float32)

    def _moving_sum(x):
        n = x.shape[0]
        c = np.vstack([np.zeros((1, x.shape[1]), np.float32),
                       np.cumsum(x, axis=0)])
        hi = np.minimum(np.arange(n) + window // 2 + 1, n)
        lo = np.maximum(np.arange(n) - window // 2, 0)
        return c[hi] - c[lo]

    local_mean = _moving_sum(s) / np.maximum(_moving_sum(v), 1.0)

    global_mean = s[valid].mean() if valid.any() else 1.0
    gain = np.where(local_mean > eps, global_mean / local_mean, 1.0)

    if strength < 1.0:
        gain = 1.0 + strength * (gain - 1.0)

    out = s * gain
    out[~valid] = 0.0
    return out