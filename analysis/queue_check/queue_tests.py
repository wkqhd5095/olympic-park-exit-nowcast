"""줄 판정 검정 T4·T2·T1 (모델 무관 지표) + 공연 단위 클러스터 추론

가이드(CLAUDE.md v1) 5절 구현. 입력은 '분 단위 공연 차량 출차 곡선'(T4·T2)과
'차 한 대 출차 시각'(T1). 실데이터·가상데이터 모두 같은 함수로 계산한다.
"""
import numpy as np
import pandas as pd


# ── T4. 공연 간 스케일링 ─────────────────────────────
def curve_metrics(show_exits, win=10, frac=0.5):
    """show_exits: 분당 공연 차량 출차(배열, rel −60~239). 반환 r_peak, T_high, N."""
    s = pd.Series(np.asarray(show_exits, float))
    ma = s.rolling(win, center=True, min_periods=win).mean()
    r_peak = float(ma.max())
    t_high = int((ma >= frac * r_peak).sum()) if r_peak > 0 else 0
    t90 = int((ma >= 0.9 * r_peak).sum()) if r_peak > 0 else 0
    # 평탄도: 피크 90% 이상 유지 시간 / 50% 이상 유지 시간. 감마형 수요는 N과 무관하게 일정,
    # 천장(줄)이 있으면 꼭대기가 평평해져 N이 클수록 커진다.
    flat = t90 / t_high if t_high > 0 else np.nan
    return {"N_curve": float(s.sum()), "r_peak": r_peak, "T_high": t_high, "T90": t90, "flat": flat}


def _ols(y, X):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


def design(df, logN, gate_fe=True):
    X = [np.ones(len(df)), logN]
    if gate_fe:
        for g in sorted(df["gate"].unique())[1:]:
            X.append((df["gate"] == g).astype(float).values)
    return np.column_stack(X)


def slope_beta(df, y="r_peak", B=2000, seed=0, gate_fe=True):
    """log y ~ log N (+문 고정효과). 공연(show_id) 단위 클러스터 부트스트랩 95% CI."""
    d = df[(df[y] > 0) & (df["N"] > 0)].reset_index(drop=True)
    ly, lN = np.log(d[y].values), np.log(d["N"].values)
    b0 = _ols(ly, design(d, lN, gate_fe))[1]
    rng = np.random.default_rng(seed)
    ids = d["show_id"].unique()
    groups = {k: np.flatnonzero(d["show_id"].values == k) for k in ids}
    bs = []
    for _ in range(B):
        take = np.concatenate([groups[k] for k in rng.choice(ids, len(ids))])
        dd = d.iloc[take]
        if dd["gate"].nunique() < d["gate"].nunique():
            continue
        bs.append(_ols(np.log(dd[y].values), design(dd, np.log(dd["N"].values), gate_fe))[1])
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return {"beta": b0, "lo": lo, "hi": hi, "n": len(d), "n_show": len(ids)}


def hinge_fit(df, y="r_peak", B=1000, seed=0, grid=None):
    """분할회귀: log y = a + b1·min(logN, c) + b2·max(logN−c, 0) (+문 FE). c는 격자 탐색.
    용량 천장 가설 → b1≈1, b2≈0. 클러스터 부트스트랩으로 b2와 c의 CI."""
    d = df[(df[y] > 0) & (df["N"] > 0)].reset_index(drop=True)

    def fit(dd):
        ly, lN = np.log(dd[y].values), np.log(dd["N"].values)
        cs = grid if grid is not None else np.quantile(lN, np.linspace(0.2, 0.9, 29))
        best = None
        for c in cs:
            X = design(dd, np.minimum(lN, c))
            X = np.column_stack([X, np.maximum(lN - c, 0)])
            b = _ols(ly, X)
            sse = np.sum((ly - X @ b) ** 2)
            if best is None or sse < best[0]:
                best = (sse, c, b[1], b[-1])
        return best

    sse, c, b1, b2 = fit(d)
    # 단일 직선 대비 개선
    lN, ly = np.log(d["N"].values), np.log(d[y].values)
    X0 = design(d, lN); sse0 = np.sum((ly - X0 @ _ols(ly, X0)) ** 2)
    rng = np.random.default_rng(seed)
    ids = d["show_id"].unique()
    groups = {k: np.flatnonzero(d["show_id"].values == k) for k in ids}
    bb = []
    for _ in range(B):
        dd = d.iloc[np.concatenate([groups[k] for k in rng.choice(ids, len(ids))])].reset_index(drop=True)
        if dd["gate"].nunique() < d["gate"].nunique():
            continue
        r = fit(dd); bb.append((r[1], r[2], r[3]))
    bb = np.array(bb)
    return {"kink_N": float(np.exp(c)), "b1": b1, "b2": b2,
            "b2_lo": np.percentile(bb[:, 2], 2.5), "b2_hi": np.percentile(bb[:, 2], 97.5),
            "kinkN_lo": float(np.exp(np.percentile(bb[:, 0], 2.5))),
            "kinkN_hi": float(np.exp(np.percentile(bb[:, 0], 97.5))),
            "sse_ratio": sse / sse0}


# ── T2. 모델 무관 급락 ───────────────────────────────
def drop_metric(exits, win=10, min_before=1.0):
    """피크 이후 각 시점 t에서 before=[t−win,t) 평균, after=[t,t+win) 평균.
    (before − after)가 최대인 t를 하락점으로. 반환: 비율 after/before, 하락폭(대/분), 위치.
    before < min_before 이면 NaN(불안정)."""
    s = np.asarray(exits, float)
    ma = pd.Series(s).rolling(win, center=True, min_periods=win).mean().values
    pk = int(np.nanargmax(ma))
    best = None
    for t in range(pk, len(s) - win):
        if t - win < 0:
            continue
        bf, af = s[t - win:t].mean(), s[t:t + win].mean()
        if best is None or (bf - af) > best[0]:
            best = (bf - af, t, bf, af)
    if best is None or best[2] < min_before:
        return {"drop_ratio": np.nan, "drop_size": np.nan, "drop_at": np.nan, "pre_rate": np.nan}
    return {"drop_ratio": best[3] / best[2], "drop_size": best[0], "drop_at": best[1], "pre_rate": best[2]}


def boot_diff(values, labels, clusters, B=3000, seed=0, stat=np.nanmedian):
    """그룹(label 1 − 0)의 중앙값 차이와 공연(cluster) 단위 부트스트랩 95% CI.
    같은 공연의 연속일·여러 문은 독립이 아니므로 공연을 통째로 재표집한다."""
    v, l, c = map(np.asarray, (values, labels, clusters))
    ok = ~np.isnan(v.astype(float)); v, l, c = v[ok].astype(float), l[ok], c[ok]
    obs = stat(v[l == 1]) - stat(v[l == 0])
    rng = np.random.default_rng(seed); ids = np.unique(c)
    idx = {k: np.flatnonzero(c == k) for k in ids}
    bs = []
    for _ in range(B):
        t = np.concatenate([idx[k] for k in rng.choice(ids, len(ids))])
        vv, ll = v[t], l[t]
        if (ll == 1).any() and (ll == 0).any():
            bs.append(stat(vv[ll == 1]) - stat(vv[ll == 0]))
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return {"diff": obs, "lo": lo, "hi": hi, "n1": int((l == 1).sum()), "n0": int((l == 0).sum())}


# ── T1. 차간 간격 CV (차로별, 국소 정규화, 절단 없음) ──────
def headway_cv(times_s, local_win=11, max_gap=None):
    """times_s: 한 차로·한 구간의 출차 시각(초, 정렬). 간격을 '국소 평균 간격'으로 나눈 뒤 CV.
    비정상 포아송(율 변화)의 CV 부풀림을 줄인다. max_gap: 절단(기본 없음)."""
    t = np.sort(np.asarray(times_s, float))
    g = np.diff(t)
    g = g[g > 0]
    if max_gap:
        g = g[g < max_gap]
    if len(g) < 15:
        return np.nan, len(g)
    loc = pd.Series(g).rolling(local_win, center=True, min_periods=3).mean().values
    z = g / loc
    return float(np.std(z, ddof=1) / np.mean(z)), len(g)
